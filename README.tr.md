# Responsible AI Banking Agent

**Live Demo:** huggingface.co/spaces/Mervecaliskan/banking-ai-agent
**English:** [README.md](README.md)

Herhangi bir bankacılık kurumu için kullanılabilecek, [LangGraph](https://www.langchain.com/langgraph)
state machine mimarisiyle çalışan, [Groq](https://groq.com/) üzerinde
Llama-3.3 modelini kullanan konuşma tabanlı bir bankacılık asistanı.
Gerçek müşteri verisi kullanmadan, sentetik olarak üretilmiş hesap ve
işlem kayıtları üzerinden çalışır; kullanıcı doğal dilde soru sorar, agent
bu soruyu SQL/pandas sorgularına çevirip Türkçe, anlaşılır bir yanıt üretir.

> Bu dosya, projenin orijinal Türkçe açıklamasını korur. Governance katmanının
> (PII redaction, guardrails, audit logging) tam hikayesi ve mimari diyagramı
> için İngilizce [README.md](README.md) dosyasına bakın.

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
responsible-ai-banking-agent/
├── assets/
│   └── demo_screenshot.png  # Demo ekran görüntüsü
├── data/
│   └── generate_data.py     # Sentetik müşteri/işlem üretici
├── tools/
│   └── query_tools.py       # LangChain tool'ları (SQLite + pandas)
├── agent/
│   ├── banking_agent.py     # LangGraph state machine
│   ├── audit.py             # Yapılandırılmış audit logging
│   ├── privacy.py           # PII tespiti & maskeleme (Presidio)
│   └── guardrails.py        # Girdi/çıktı guardrail katmanı
├── tests/
│   ├── test_privacy.py      # 32 test
│   └── test_guardrails.py   # 18 test
├── docs/
│   └── MODEL_CARD.md        # Sistem kartı (İngilizce)
├── app.py                   # Streamlit chat arayüzü
├── requirements.txt
└── .env.example
```

## Governance Layer

Bu proje, bir bankacılık AI agent'ının sorumlu (responsible AI) şekilde
çalışmasını sağlayacak governance katmanını da içerir:

- **Structured audit logging** — [`agent/audit.py`](agent/audit.py)
  ile implemente edildi. Agent'ın aldığı her karar (niyet sınıflandırma,
  sorgulanan veri, üretilen yanıt) izlenebilir şekilde loglanır.
- **PII redaction** — [`agent/privacy.py`](agent/privacy.py) ile Microsoft
  Presidio kullanılarak implemente edildi. Türkiye'ye özgü TC Kimlik No ve
  IBAN icin checksum dogrulamali custom recognizer'lar, ayrica isim tespiti
  icin stopword listesiyle filtrelenmis bir regex sezgiseli eklendi. Metnin
  nereye gittigine gore iki farkli politika uygulanir: kullanici girdisi
  LLM'e ulasmadan once precision-first (NER'siz) bir gecis, audit log'a
  yazilmadan once ise recall-first (spaCy NER eklenmis) bir gecis.
  `user_id`, audit kayitlarinda ham haliyle degil, salted hash ile
  pseudonimize edilerek saklanir.
- **Guardrails** — [`agent/guardrails.py`](agent/guardrails.py) ile
  implemente edildi. Girdi tarafinda: prompt injection, sistem promptu
  sizdirma girisimi, customer_id manipülasyonu, SQL injection paternleri.
  Çıktı tarafinda: finansal/yatırım tavsiyesi, başka müşteriye ait veri
  sızıntısı, redaction'dan kaçan ham PII. Her blok kararı audit log'a
  yazılır.
- **Model card** — [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) (İngilizce).
  Kapsam, değerlendirme sonuçları ve bilinen kısıtlar (ör. Türkçe için
  güvenilir bir NER modelinin bulunmaması) belgelenmiştir.

Ölçülen sonuçlar (50 otomatik test): 25/25 adversarial prompt engellendi
(%100), 7/7 zararsız prompt doğru şekilde izin verildi (%0 yanlış pozitif).
Detaylar için İngilizce [README.md](README.md#results) ve
[model card](docs/MODEL_CARD.md).

## Kanıt

![Audit log](assets/log.png)

`logs/audit.log` dosyasından, agent gerçekten çalıştırılarak elde edilmiş iki
gerçek satır:

- **Normal istek** (üst satır) — içinde isim ve TC Kimlik No geçen bir soru,
  diske yazılmadan önce her ikisi de maskelenerek (`[NAME]`, `[TCKN]`)
  loglanır.
- **Engellenen istek** (alt satır) — bir prompt injection girişimi
  `prompt_injection` kuralını tetikler ve `guardrail_block` olayı olarak
  loglanır. İki detay dikkat çekici: `intent` alanı `null`, çünkü engelleme
  girdi aşamasında, LLM hiç çağrılmadan gerçekleşir — sınıflandırılacak bir
  niyet yoktur. Ayrıca `matched_text` — kuralı tetikleyen ifadenin kendisi —
  de maskelenmiştir (`[NAME]`), çünkü bir güvenlik logu da bir saldırı
  yüzeyidir; önlemeye çalıştığı PII'yi kendisi sızdıramaz.

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
- **Microsoft Presidio + spaCy** — PII tespiti ve maskeleme
- **SQLite + pandas** — sentetik veri saklama ve sorgulama
- **Streamlit** — chat arayüzü
- **Faker** — Türkçe sentetik veri üretimi
- **pytest** — 50 otomatik test
