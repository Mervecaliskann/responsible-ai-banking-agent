"""LangGraph state machine: parse_intent -> route -> [query_data/summarize/alert] -> respond."""

import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph

sys.path.append(str(Path(__file__).parent.parent))
from tools.query_tools import (  # noqa: E402
    categorize_spending,
    detect_anomaly,
    get_account_balance,
    get_monthly_summary,
    get_transactions,
)
from agent import audit, privacy  # noqa: E402

load_dotenv()

MODEL_NAME = "llama-3.3-70b-versatile"
INTENTS = {"balance", "transactions", "spending", "anomaly", "general"}
CATEGORIES = ["market", "fatura", "restoran", "ulaşım", "eğlence", "sağlık", "ATM"]


def _llm() -> ChatGroq:
    return ChatGroq(model=MODEL_NAME, temperature=0, api_key=os.environ.get("GROQ_API_KEY"))


def _accumulate_usage(base: Optional[dict], message) -> dict:
    """Add an LLM response's token usage onto a running total for the request."""
    base = base or {}
    usage = getattr(message, "usage_metadata", None) or {}
    return {
        "input_tokens": base.get("input_tokens", 0) + usage.get("input_tokens", 0),
        "output_tokens": base.get("output_tokens", 0) + usage.get("output_tokens", 0),
        "total_tokens": base.get("total_tokens", 0) + usage.get("total_tokens", 0),
    }


def _this_month_range() -> tuple[str, str]:
    today = date.today()
    start = today.replace(day=1)
    end = date(today.year + 1, 1, 1) if today.month == 12 else date(today.year, today.month + 1, 1)
    return start.isoformat(), end.isoformat()


def _last_month_range() -> tuple[str, str]:
    today = date.today()
    year, month = today.year, today.month
    month -= 1
    if month == 0:
        month = 12
        year -= 1
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


class AgentState(TypedDict):
    messages: list[BaseMessage]
    customer_id: int
    intent: str
    period: Optional[str]
    category: Optional[str]
    limit: Optional[int]
    query_result: dict
    response: str
    tools_called: list[str]
    token_usage: dict


INTENT_PROMPT = """Sen bir bankacilik asistanisin. Kullanicinin sorusunu asagidaki JSON \
formatinda siniflandir. SADECE JSON dondur, baska hicbir aciklama yazma.

intent secenekleri:
- "balance": hesap bakiyesi sorusu
- "transactions": son islemler / islem listesi sorusu
- "spending": harcama analizi / kategoriye gore harcama sorusu
- "anomaly": anormal/fazla harcama, uyari sorusu
- "general": yukaridakilerden hicbiri degil, genel sohbet

period secenekleri: "this_month", "last_month" ya da null (belirtilmemisse this_month kullan)
category secenekleri: {categories} listesinden biri ya da null
limit: "son N islem" gibi bir sayi belirtilmisse o sayi, yoksa null

JSON formati:
{{"intent": "...", "period": "...", "category": "...", "limit": null}}

Kullanici sorusu: {question}
"""

RESPONSE_PROMPT = """Sen ING Hubs tarzi bir bankacilik AI asistanisin. Asagidaki musteri \
sorusuna, verilen veriye dayanarak Turkce, kisa ve net bir yanit yaz. Para \
birimi olarak TL kullan, sayilari okunakli formatla (orn. 1.234,56 TL). Veri \
yoksa veya bos ise bunu nazikce belirt.

Musteri sorusu: {question}

Veri (JSON): {data}
"""


def parse_intent(state: AgentState) -> dict:
    question = state["messages"][-1].content
    prompt = INTENT_PROMPT.format(categories=", ".join(CATEGORIES), question=question)
    message = _llm().invoke(prompt)
    raw = message.content

    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        parsed = {}

    intent = parsed.get("intent") if parsed.get("intent") in INTENTS else "general"
    return {
        "intent": intent,
        "period": parsed.get("period") or "this_month",
        "category": parsed.get("category"),
        "limit": parsed.get("limit"),
        "tools_called": [],
        "token_usage": _accumulate_usage(state.get("token_usage"), message),
    }


def route(state: AgentState) -> str:
    return {
        "balance": "query_data",
        "transactions": "query_data",
        "spending": "summarize",
        "anomaly": "alert",
        "general": "respond",
    }[state["intent"]]


def query_data(state: AgentState) -> dict:
    customer_id = state["customer_id"]
    if state["intent"] == "balance":
        result = get_account_balance.invoke({"customer_id": customer_id})
        tool_name = "get_account_balance"
    else:
        start_date, end_date = _last_month_range() if state["period"] == "last_month" else _this_month_range()
        limit = state.get("limit") or 5
        result = get_transactions.invoke(
            {
                "customer_id": customer_id,
                "category": state.get("category"),
                "limit": limit,
                "start_date": None if state.get("limit") else start_date,
                "end_date": None if state.get("limit") else end_date,
            }
        )
        tool_name = "get_transactions"
    return {"query_result": result, "tools_called": [tool_name]}


def summarize(state: AgentState) -> dict:
    customer_id = state["customer_id"]
    start_date, end_date = _last_month_range() if state["period"] == "last_month" else _this_month_range()
    spending = categorize_spending.invoke({"customer_id": customer_id, "start_date": start_date, "end_date": end_date})

    category = state.get("category")
    if category and category in spending.get("by_category", {}):
        result = {
            "category": category,
            "amount": spending["by_category"][category],
            "start_date": start_date,
            "end_date": end_date,
        }
    else:
        result = spending
    return {"query_result": result, "tools_called": ["categorize_spending"]}


def alert(state: AgentState) -> dict:
    result = detect_anomaly.invoke({"customer_id": state["customer_id"]})
    return {"query_result": result, "tools_called": ["detect_anomaly"]}


def respond(state: AgentState) -> dict:
    question = state["messages"][-1].content
    data = state.get("query_result", {})
    prompt = RESPONSE_PROMPT.format(question=question, data=json.dumps(data, ensure_ascii=False))
    message = _llm().invoke(prompt)
    reply = message.content
    return {
        "response": reply,
        "messages": state["messages"] + [AIMessage(content=reply)],
        "token_usage": _accumulate_usage(state.get("token_usage"), message),
    }


def build_agent():
    graph = StateGraph(AgentState)
    graph.add_node("parse_intent", parse_intent)
    graph.add_node("query_data", query_data)
    graph.add_node("summarize", summarize)
    graph.add_node("alert", alert)
    graph.add_node("respond", respond)

    graph.set_entry_point("parse_intent")
    graph.add_conditional_edges(
        "parse_intent",
        route,
        {"query_data": "query_data", "summarize": "summarize", "alert": "alert", "respond": "respond"},
    )
    graph.add_edge("query_data", "respond")
    graph.add_edge("summarize", "respond")
    graph.add_edge("alert", "respond")
    graph.add_edge("respond", END)

    return graph.compile()


def ask(agent, customer_id: int, question: str, history: Optional[list[BaseMessage]] = None) -> str:
    """Tek bir kullanici sorusunu agent'a yollar, yaniti dondurur.

    The question is PII-redacted before it reaches the LLM (parse_intent
    and respond both read it from state["messages"]), so raw TCKN, IBAN,
    phone numbers, and heuristically-detected names never leave this
    function. Uses the precision-first policy - see agent/privacy.py.
    """
    redacted_question = privacy.redact_for_llm(question)
    messages = (history or []) + [HumanMessage(content=redacted_question)]
    started = time.perf_counter()
    final_state = agent.invoke({"messages": messages, "customer_id": customer_id})
    latency_ms = (time.perf_counter() - started) * 1000

    audit.log_request(
        user_id=customer_id,
        intent=final_state.get("intent"),
        tools_called=final_state.get("tools_called", []),
        latency_ms=latency_ms,
        token_usage=final_state.get("token_usage", {}),
        question=redacted_question,
        response=final_state["response"],
    )
    return final_state["response"]
