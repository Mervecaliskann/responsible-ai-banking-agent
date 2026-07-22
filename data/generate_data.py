"""Sentetik müşteri ve işlem verisi üretir, data/banking.db (SQLite) dosyasına yazar."""

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

fake = Faker("tr_TR")
random.seed(42)
Faker.seed(42)

DB_PATH = Path(__file__).parent / "banking.db"

NUM_CUSTOMERS = 10
TX_PER_CUSTOMER = 55  # ~10 * 55 = 550 islem, "en az 500" sartini saglar
MONTHS_BACK = 3

CATEGORY_MERCHANTS = {
    "market": ["Migros", "BİM", "A101", "ŞOK", "CarrefourSA"],
    "fatura": ["Türk Telekom", "Vodafone", "Enerjisa", "İGDAŞ", "Turkcell"],
    "restoran": ["Yemeksepeti", "Getir Yemek", "Burger King", "Köfteci Yusuf", "Starbucks"],
    "ulaşım": ["İstanbulkart Dolum", "Shell Benzin", "BiTaksi", "Metro İstanbul", "Opet"],
    "eğlence": ["Netflix", "Spotify", "Sinema Maximum", "PlayStation Store", "Bubilet"],
    "sağlık": ["Eczane", "Acıbadem Hastanesi", "Memorial Sağlık", "Medical Park", "Optik Dünyası"],
    "ATM": ["ATM Çekim"],
}

CATEGORY_AMOUNT_RANGE = {
    "market": (100, 1500),
    "fatura": (150, 900),
    "restoran": (80, 700),
    "ulaşım": (50, 600),
    "eğlence": (40, 400),
    "sağlık": (100, 2500),
    "ATM": (200, 3000),
}

CATEGORIES = list(CATEGORY_MERCHANTS.keys())


def random_date_within_months(months_back: int) -> date:
    today = date.today()
    start = today - timedelta(days=months_back * 30)
    delta_days = (today - start).days
    return start + timedelta(days=random.randint(0, delta_days))


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS transactions;
        DROP TABLE IF EXISTS customers;

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            account_balance REAL NOT NULL
        );

        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            merchant TEXT NOT NULL,
            type TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        );
        """
    )


def generate_customers(conn: sqlite3.Connection) -> list[int]:
    customer_ids = []
    rows = []
    for i in range(1, NUM_CUSTOMERS + 1):
        balance = round(random.uniform(1000, 100000), 2)
        rows.append((i, fake.name(), balance))
        customer_ids.append(i)
    conn.executemany(
        "INSERT INTO customers (customer_id, name, account_balance) VALUES (?, ?, ?)",
        rows,
    )
    return customer_ids


def generate_transactions(conn: sqlite3.Connection, customer_ids: list[int]) -> None:
    today = date.today()
    current_month_start = today.replace(day=1)

    # Her musteri icin bir kategoride bilincli anomali olusturalim (gecmis aylara
    # gore bu ay belirgin yuksek harcama) ki "bu ay normalden fazla harcama var mi"
    # sorusu gercek bir veriyle test edilebilsin.
    anomaly_customer_ids = random.sample(customer_ids, k=max(1, NUM_CUSTOMERS // 2))
    anomaly_category_by_customer = {
        cid: random.choice(CATEGORIES) for cid in anomaly_customer_ids
    }

    rows = []
    for cid in customer_ids:
        # Aylik maas/gelir kayitlari (son MONTHS_BACK ay icin)
        for m in range(MONTHS_BACK):
            income_date = (current_month_start - timedelta(days=30 * m)).replace(day=1) + timedelta(days=random.randint(0, 4))
            salary = round(random.uniform(15000, 60000), 2)
            rows.append((cid, income_date.isoformat(), salary, "maaş", "Maaş Ödemesi", "income"))

        anomaly_category = anomaly_category_by_customer.get(cid)

        for _ in range(TX_PER_CUSTOMER):
            category = random.choice(CATEGORIES)
            merchant = random.choice(CATEGORY_MERCHANTS[category])
            low, high = CATEGORY_AMOUNT_RANGE[category]
            amount = round(random.uniform(low, high), 2)
            tx_date = random_date_within_months(MONTHS_BACK)

            # Anomali kategorisi icin bu ayki islemleri belirgin sekilde buyut
            if category == anomaly_category and tx_date >= current_month_start:
                amount = round(amount * random.uniform(1.8, 2.5), 2)

            rows.append((cid, tx_date.isoformat(), amount, category, merchant, "expense"))

    conn.executemany(
        "INSERT INTO transactions (customer_id, date, amount, category, merchant, type) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        create_schema(conn)
        customer_ids = generate_customers(conn)
        generate_transactions(conn, customer_ids)
        conn.commit()

        tx_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        cust_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        print(f"{cust_count} müşteri, {tx_count} işlem üretildi -> {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
