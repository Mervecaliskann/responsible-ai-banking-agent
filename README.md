# Responsible AI Banking Agent

**Live Demo:** huggingface.co/spaces/Mervecaliskan/banking-ai-agent

[English](#english) | [Türkçe](#türkçe)

---

<a id="english"></a>
## English

A conversational banking assistant that can be used by any banking
institution, built on a [LangGraph](https://www.langchain.com/langgraph)
state machine architecture and powered by the Llama-3.3 model on
[Groq](https://groq.com/). It works entirely on synthetically generated
account and transaction records, without using any real customer data;
the user asks a question in natural language, and the agent translates
that question into SQL/pandas queries and produces a clear, natural-language
response.

### Purpose

This is a reference/demo project that shows how frequently asked banking
customer-service questions — "what's my balance?", "what did I spend the
most on this month?", "is there any unusual spending?" — can be answered
by an AI agent that queries real-time data and routes based on intent. It
contains no real banking infrastructure or customer data; all data is
synthetically generated with `Faker`. The architecture is designed to be
generic and institution-agnostic, so it can be integrated into any bank's
existing customer database.

### System in Action

![Demo Screenshot](assets/demo_screenshot.png)

### Architecture: LangGraph State Machine

The agent is designed not as a single linear chain, but as a graph that
performs **intent-based conditional routing**:

```
                 ┌───────────────┐
                 │  parse_intent  │   The question is classified by the LLM:
                 │                │   balance / transactions / spending /
                 └───────┬────────┘   anomaly / general + period/category
                         │
                  conditional routing
                  (branches based on intent)
                         │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
 ┌───────────┐    ┌─────────────┐    ┌───────────┐
 │ query_data │    │  summarize  │    │   alert    │
 │ (balance / │    │ (spending   │    │ (anomaly   │
 │ transaction│    │  analysis)  │    │  detection)│
 │   list)    │    │             │    │            │
 └─────┬──────┘    └──────┬──────┘    └─────┬──────┘
       │                  │                 │
       └──────────────────┼─────────────────┘
                           ▼
                     ┌───────────┐
                     │  respond   │   The LLM turns the query result
                     │            │   into a natural-language reply
                     └───────────┘
```

- **parse_intent**: `ChatGroq` (llama-3.3-70b-versatile) classifies the
  user's message as JSON — extracting the intent (`intent`), time range
  (`period`: this month / last month), category, and transaction limit if
  present.
- **conditional routing**: using `add_conditional_edges`, without a
  separate execution node, the classified intent routes directly to
  `query_data`, `summarize`, `alert`, or to `respond` for `general`
  questions.
- **query_data**: for balance or transaction-list questions, runs the
  `get_account_balance` / `get_transactions` tools.
- **summarize**: for spending-analysis questions, computes category-based
  totals with `categorize_spending`.
- **alert**: for "is there unusual spending?" type questions, uses
  `detect_anomaly` to compare this month's category spending against the
  average of previous months.
- **respond**: converts the resulting data into a natural, concise reply
  via the LLM and appends it to the chat history.

Code: [`agent/banking_agent.py`](agent/banking_agent.py)

### Synthetic Data Approach

Since no real banking data is used, the [`data/generate_data.py`](data/generate_data.py)
script generates a SQLite database (`data/banking.db`) using
`Faker('tr_TR')` with realistic Turkish names:

- **customers**: 10 customers — `customer_id, name, account_balance`
- **transactions**: 500+ transactions (~55 per customer), spread over the
  last 3 months — `id, customer_id, date, amount, category, merchant, type`
  - Categories: `market, fatura (bill), restoran (restaurant), ulaşım
    (transport), eğlence (entertainment), sağlık (health), ATM`
    (+ `maaş` (salary) income, for balance consistency)
  - Realistic merchant lists for each category (Migros, Türk Telekom,
    Netflix, Shell, etc.)
  - **Deliberate anomaly scenario**: for some customers, 80-150% more
    spending than previous months is injected into a specific category
    this month, so that the "unusual spending" question has a real,
    testable data condition to detect.

The script is re-runnable (idempotent) — it drops & recreates the tables
each time.

### Example Questions

| Question | Node it routes to |
|---|---|
| "What's my account balance?" | `query_data` (balance) |
| "What are my last 5 transactions?" | `query_data` (transactions) |
| "What did I spend the most on this month?" | `summarize` (spending) |
| "How much did I spend at the market last month?" | `summarize` (spending + category filter) |
| "Is there more spending than usual this month?" | `alert` (anomaly) |

### Project Structure

```
responsible-ai-banking-agent/
├── assets/
│   └── demo_screenshot.png  # Demo screenshot
├── data/
│   └── generate_data.py     # Synthetic customer/transaction generator
├── tools/
│   └── query_tools.py       # LangChain tools (SQLite + pandas)
├── agent/
│   ├── banking_agent.py     # LangGraph state machine
│   └── audit.py             # Structured audit logging
├── app.py                   # Streamlit chat interface
├── requirements.txt
└── .env.example
```

### Governance Layer

This project also includes a governance layer aimed at making a banking
AI agent operate responsibly:

- **Structured audit logging** — implemented in [`agent/audit.py`](agent/audit.py).
  Every decision the agent makes (intent classification, data queried,
  response generated) is logged in a traceable way.
- **PII redaction** — in progress.
- **Guardrails** — in progress.
- **Model card** — in progress.

### Setup and Running

```bash
# 1. Virtual environment
python -m venv venv
source venv/Scripts/activate   # Windows: venv\Scripts\activate

# 2. Dependencies
pip install -r requirements.txt

# 3. Add your GROQ_API_KEY to the .env file
cp .env.example .env
# in .env: GROQ_API_KEY=sk-...

# 4. Generate synthetic data
python data/generate_data.py

# 5. Start the app
streamlit run app.py
```

### Tech Stack

- **LangGraph** — state machine performing intent-based conditional routing
- **LangChain** — tool definitions
- **Groq (Llama-3.3-70b-versatile)** — intent classification and natural-language response generation
- **SQLite + pandas** — synthetic data storage and querying
- **Streamlit** — chat interface
- **Faker** — synthetic Turkish data generation

---

<a id="türkçe"></a>
## Türkçe

Herhangi bir bankacılık kurumu için kullanılabilecek, [LangGraph](https://www.langchain.com/langgraph)
state machine mimarisiyle çalışan, [Groq](https://groq.com/) üzerinde
Llama-3.3 modelini kullanan konuşma tabanlı bir bankacılık asistanı.
Gerçek müşteri verisi kullanmadan, sentetik olarak üretilmiş hesap ve
işlem kayıtları üzerinden çalışır; kullanıcı doğal dilde soru sorar, agent
bu soruyu SQL/pandas sorgularına çevirip Türkçe, anlaşılır bir yanıt üretir.

### Amaç

Bankacılık müşteri hizmetlerinde sıkça sorulan "bakiyem ne kadar?",
"bu ay en çok neye harcadım?", "anormal bir harcama var mı?" gibi soruları,
gerçek zamanlı veri sorgulayan ve niyet bazlı yönlendirme yapan bir AI agent
ile yanıtlamayı gösteren bir referans/demo projesidir. Gerçek banka altyapısı
veya müşteri verisi içermez; tüm veriler `Faker` ile sentetik olarak üretilir.
Mimari, herhangi bir bankanın mevcut müşteri veritabanına entegre edilebilecek
şekilde genel ve kuruma bağımsız tasarlanmıştır.

### System in Action

![Demo Screenshot](assets/demo_screenshot.png)

### Mimari: LangGraph State Machine

Agent, tek bir doğrusal zincir değil, **niyet bazlı koşullu yönlendirme**
yapan bir graph olarak tasarlanmıştır:

```
                 ┌───────────────┐
                 │  parse_intent  │   LLM ile soru sınıflandırılır:
                 │                │   balance / transactions / spending /
                 └───────┬────────┘   anomaly / general + period/category
                         │
                  conditional routing
                  (intent'e göre dallanma)
                         │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
 ┌───────────┐    ┌─────────────┐    ┌───────────┐
 │ query_data │    │  summarize  │    │   alert    │
 │ (bakiye /  │    │ (harcama    │    │ (anomali   │
 │ işlem      │    │  analizi)   │    │  tespiti)  │
 │  listesi)  │    │             │    │            │
 └─────┬──────┘    └──────┬──────┘    └─────┬──────┘
       │                  │                 │
       └──────────────────┼─────────────────┘
                           ▼
                     ┌───────────┐
                     │  respond   │   LLM, sorgu sonucunu doğal
                     │            │   Türkçe yanıta dönüştürür
                     └───────────┘
```

- **parse_intent**: `ChatGroq` (llama-3.3-70b-versatile) kullanıcı mesajını
  JSON formatında sınıflandırır — niyet (`intent`), zaman aralığı (`period`:
  bu ay / geçen ay), kategori ve varsa işlem limiti çıkarılır.
- **conditional routing**: `add_conditional_edges` ile, ayrı bir yürütme
  node'u olmadan, sınıflandırılan niyete göre doğrudan `query_data`,
  `summarize`, `alert` veya `general` sorular için `respond`'a yönlendirilir.
- **query_data**: Bakiye veya işlem listesi sorularında `get_account_balance`
  / `get_transactions` tool'larını çalıştırır.
- **summarize**: Harcama analizi sorularında `categorize_spending` ile
  kategori bazlı toplamları hesaplar.
- **alert**: "Anormal harcama var mı?" tipi sorularda `detect_anomaly` ile
  bu ayın kategori harcamalarını önceki ayların ortalamasıyla karşılaştırır.
- **respond**: Elde edilen veriyi LLM ile doğal, kısa bir Türkçe cümleye
  dönüştürür ve sohbet geçmişine ekler.

Kod: [`agent/banking_agent.py`](agent/banking_agent.py)

### Sentetik Veri Yaklaşımı

Gerçek banka verisi kullanılmadığı için [`data/generate_data.py`](data/generate_data.py)
script'i, `Faker('tr_TR')` ile gerçekçi Türkçe isimler kullanarak SQLite
veritabanı (`data/banking.db`) üretir:

- **customers**: 10 müşteri — `customer_id, name, account_balance`
- **transactions**: 500+ işlem (müşteri başına ~55), son 3 aya yayılmış —
  `id, customer_id, date, amount, category, merchant, type`
  - Kategoriler: `market, fatura, restoran, ulaşım, eğlence, sağlık, ATM`
    (+ `maaş` geliri, bakiye tutarlılığı için)
  - Her kategori için gerçekçi merchant listeleri (Migros, Türk Telekom,
    Netflix, Shell vb.)
  - **Bilinçli anomali senaryosu**: bazı müşterilerde bu ay belirli bir
    kategoride geçmiş aylara göre %80-150 daha fazla harcama enjekte edilir,
    böylece "anormal harcama" sorusu test edilebilir gerçek bir veri durumu
    oluşur.

Script tekrar çalıştırılabilir (idempotent) — her seferinde tabloları
DROP & CREATE eder.

### Örnek Sorular

| Soru | Yönlendiği node |
|---|---|
| "Hesap bakiyem ne kadar?" | `query_data` (balance) |
| "Son 5 işlemim neler?" | `query_data` (transactions) |
| "Bu ay en çok ne için harcama yaptım?" | `summarize` (spending) |
| "Geçen ay markete ne kadar harcadım?" | `summarize` (spending + kategori filtresi) |
| "Bu ay normalden fazla harcama var mı?" | `alert` (anomaly) |

### Proje Yapısı

```
responsible-ai-banking-agent/
├── assets/
│   └── demo_screenshot.png  # Demo ekran görüntüsü
├── data/
│   └── generate_data.py     # Sentetik müşteri/işlem üretici
├── tools/
│   └── query_tools.py       # LangChain tool'ları (SQLite + pandas)
├── agent/
│   ├── banking_agent.py     # LangGraph state machine
│   └── audit.py             # Yapılandırılmış audit logging
├── app.py                   # Streamlit chat arayüzü
├── requirements.txt
└── .env.example
```

### Governance Layer

Bu proje, bir bankacılık AI agent'ının sorumlu (responsible AI) şekilde
çalışmasını sağlayacak governance katmanını da içerir:

- **Structured audit logging** — [`agent/audit.py`](agent/audit.py)
  ile implemente edildi. Agent'ın aldığı her karar (niyet sınıflandırma,
  sorgulanan veri, üretilen yanıt) izlenebilir şekilde loglanır.
- **PII redaction** — geliştirme aşamasında.
- **Guardrails** — geliştirme aşamasında.
- **Model card** — geliştirme aşamasında.

### Kurulum ve Çalıştırma

```bash
# 1. Sanal ortam
python -m venv venv
source venv/Scripts/activate   # Windows: venv\Scripts\activate

# 2. Bağımlılıklar
pip install -r requirements.txt

# 3. GROQ_API_KEY'inizi .env dosyasına ekleyin
cp .env.example .env
# .env içine: GROQ_API_KEY=sk-...

# 4. Sentetik veriyi üret
python data/generate_data.py

# 5. Uygulamayı başlat
streamlit run app.py
```

### Tech Stack

- **LangGraph** — niyet bazlı koşullu yönlendirme yapan state machine
- **LangChain** — tool tanımları
- **Groq (Llama-3.3-70b-versatile)** — niyet sınıflandırma ve doğal dil yanıt üretimi
- **SQLite + pandas** — sentetik veri saklama ve sorgulama
- **Streamlit** — chat arayüzü
- **Faker** — Türkçe sentetik veri üretimi
