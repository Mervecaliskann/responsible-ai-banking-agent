# Banking AI Agent

**Live Demo:** huggingface.co/spaces/Mervecaliskan/banking-ai-agent

Herhangi bir bankacılık kurumu için kullanılabilecek, [LangGraph](https://www.langchain.com/langgraph)
state machine mimarisiyle çalışan, [Groq](https://groq.com/) üzerinde
Llama-3.3 modelini kullanan konuşma tabanlı bir bankacılık asistanı.
Gerçek müşteri verisi kullanmadan, sentetik olarak üretilmiş hesap ve
işlem kayıtları üzerinden çalışır; kullanıcı doğal dilde soru sorar, agent
bu soruyu SQL/pandas sorgularına çevirip Türkçe, anlaşılır bir yanıt üretir.

## Amaç

Bankacılık müşteri hizmetlerinde sıkça sorulan "bakiyem ne kadar?",
"bu ay en çok neye harcadım?", "anormal bir harcama var mı?" gibi soruları,
gerçek zamanlı veri sorgulayan ve niyet bazlı yönlendirme yapan bir AI agent
ile yanıtlamayı gösteren bir referans/demo projesidir. Gerçek banka altyapısı
veya müşteri verisi içermez; tüm veriler `Faker` ile sentetik olarak üretilir.
Mimari, herhangi bir bankanın mevcut müşteri veritabanına entegre edilebilecek
şekilde genel ve kuruma bağımsız tasarlanmıştır.

## System in Action

![Demo Screenshot](assets/demo_screenshot.png)

## Mimari: LangGraph State Machine

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

## Sentetik Veri Yaklaşımı

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

## Örnek Sorular

| Soru | Yönlendiği node |
|---|---|
| "Hesap bakiyem ne kadar?" | `query_data` (balance) |
| "Son 5 işlemim neler?" | `query_data` (transactions) |
| "Bu ay en çok ne için harcama yaptım?" | `summarize` (spending) |
| "Geçen ay markete ne kadar harcadım?" | `summarize` (spending + kategori filtresi) |
| "Bu ay normalden fazla harcama var mı?" | `alert` (anomaly) |

## Proje Yapısı

```
banking-ai-agent/
├── assets/
│   └── demo_screenshot.png  # Demo ekran görüntüsü
├── data/
│   └── generate_data.py     # Sentetik müşteri/işlem üretici
├── tools/
│   └── query_tools.py       # LangChain tool'ları (SQLite + pandas)
├── agent/
│   └── banking_agent.py     # LangGraph state machine
├── app.py                   # Streamlit chat arayüzü
├── requirements.txt
└── .env.example
```

## Kurulum ve Çalıştırma

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

## Tech Stack

- **LangGraph** — niyet bazlı koşullu yönlendirme yapan state machine
- **LangChain** — tool tanımları
- **Groq (Llama-3.3-70b-versatile)** — niyet sınıflandırma ve doğal dil yanıt üretimi
- **SQLite + pandas** — sentetik veri saklama ve sorgulama
- **Streamlit** — chat arayüzü
- **Faker** — Türkçe sentetik veri üretimi
