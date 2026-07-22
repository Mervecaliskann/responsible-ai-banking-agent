"""Streamlit chat arayuzu: bankacilik AI agent ile sohbet."""

import sqlite3
from pathlib import Path

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from agent.banking_agent import ask, build_agent

DB_PATH = Path(__file__).parent / "data" / "banking.db"

st.set_page_config(page_title="Bankacilik AI Asistani", page_icon="\U0001F3E6")
st.title("\U0001F3E6 Bankacilik AI Asistani")


@st.cache_resource
def get_agent():
    return build_agent()


@st.cache_data
def get_customers():
    conn = sqlite3.connect(DB_PATH)
    try:
        return conn.execute("SELECT customer_id, name, account_balance FROM customers ORDER BY customer_id").fetchall()
    finally:
        conn.close()


if not DB_PATH.exists():
    st.error("Veritabani bulunamadi. Once `python data/generate_data.py` calistirin.")
    st.stop()

customers = get_customers()
customer_options = {f"{name} (#{cid})": (cid, balance) for cid, name, balance in customers}

with st.sidebar:
    st.header("Aktif Musteri")
    selected_label = st.selectbox("Musteri secin", list(customer_options.keys()))
    selected_id, selected_balance = customer_options[selected_label]
    st.metric("Hesap Bakiyesi", f"{selected_balance:,.2f} TL")

if "customer_id" not in st.session_state or st.session_state.customer_id != selected_id:
    st.session_state.customer_id = selected_id
    st.session_state.chat_history = []
    st.session_state.lc_messages = []

agent = get_agent()

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

user_input = st.chat_input("Sorunuzu yazin (orn. 'Bu ay en cok ne icin harcama yaptim?')")
if user_input:
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Dusunuyor..."):
            reply = ask(agent, st.session_state.customer_id, user_input, st.session_state.lc_messages)
        st.markdown(reply)

    st.session_state.chat_history.append({"role": "assistant", "content": reply})
    st.session_state.lc_messages += [HumanMessage(content=user_input), AIMessage(content=reply)]
