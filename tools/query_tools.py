"""SQLite üzerindeki musteri/islem verisine sorgu atan LangChain tool'lari."""

from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from langchain_core.tools import tool

DB_PATH = Path(__file__).parent.parent / "data" / "banking.db"


def _connect():
    import sqlite3

    return sqlite3.connect(DB_PATH)


def _month_range(year_month: Optional[str] = None) -> tuple[str, str]:
    """'YYYY-MM' icin (ay basi, ay sonu) ISO tarih araligini dondurur. None ise bu ay."""
    today = date.today()
    if year_month:
        year, month = (int(p) for p in year_month.split("-"))
    else:
        year, month = today.year, today.month
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start.isoformat(), end.isoformat()


def _previous_month_range(offset: int = 1) -> tuple[str, str]:
    """Bugunden 'offset' ay once basleyan ayin (ay basi, ay sonu) araligi."""
    today = date.today()
    year, month = today.year, today.month
    for _ in range(offset):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return _month_range(f"{year:04d}-{month:02d}")


@tool
def get_account_balance(customer_id: int) -> dict:
    """Belirtilen musterinin guncel hesap bakiyesini dondurur."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT customer_id, name, account_balance FROM customers WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()
        if row is None:
            return {"error": f"customer_id={customer_id} bulunamadi"}
        return {"customer_id": row[0], "name": row[1], "balance": row[2]}
    finally:
        conn.close()


@tool
def get_transactions(
    customer_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """Musterinin islemlerini, opsiyonel tarih araligi/kategori/limit filtresiyle dondurur.

    start_date/end_date 'YYYY-MM-DD' formatinda olmalidir. limit verilirse en
    son islemlerden bu kadari dondurulur (orn. 'son 5 islemim' sorusu icin).
    """
    conn = _connect()
    try:
        query = "SELECT date, amount, category, merchant, type FROM transactions WHERE customer_id = ?"
        params: list = [customer_id]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date < ?"
            params.append(end_date)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY date DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)

        df = pd.read_sql(query, conn, params=params)
        return df.to_dict(orient="records")
    finally:
        conn.close()


@tool
def categorize_spending(
    customer_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """Musterinin verilen tarih araligindaki harcamalarini kategoriye gore toplar.

    Tarih verilmezse bu ayin verisi kullanilir. Sadece 'expense' tipi islemler
    dahil edilir (gelir/maas haric).
    """
    if not start_date or not end_date:
        start_date, end_date = _month_range()

    conn = _connect()
    try:
        df = pd.read_sql(
            "SELECT category, amount FROM transactions "
            "WHERE customer_id = ? AND type = 'expense' AND date >= ? AND date < ?",
            conn,
            params=[customer_id, start_date, end_date],
        )
        if df.empty:
            return {"start_date": start_date, "end_date": end_date, "by_category": {}, "total": 0.0}

        by_category = df.groupby("category")["amount"].sum().sort_values(ascending=False).round(2)
        return {
            "start_date": start_date,
            "end_date": end_date,
            "by_category": {k: float(v) for k, v in by_category.to_dict().items()},
            "total": float(round(df["amount"].sum(), 2)),
            "top_category": by_category.index[0],
        }
    finally:
        conn.close()


@tool
def get_monthly_summary(customer_id: int, year_month: Optional[str] = None) -> dict:
    """Belirtilen ayin (varsayilan: bu ay, 'YYYY-MM') toplam gelir/gider ve
    kategori dagilimini ozetler."""
    start_date, end_date = _month_range(year_month)
    conn = _connect()
    try:
        df = pd.read_sql(
            "SELECT amount, category, type FROM transactions "
            "WHERE customer_id = ? AND date >= ? AND date < ?",
            conn,
            params=[customer_id, start_date, end_date],
        )
        expense_df = df[df["type"] == "expense"]
        income_df = df[df["type"] == "income"]

        by_category = (
            expense_df.groupby("category")["amount"].sum().sort_values(ascending=False).round(2)
            if not expense_df.empty
            else pd.Series(dtype=float)
        )

        return {
            "year_month": year_month or date.today().strftime("%Y-%m"),
            "total_expense": float(round(expense_df["amount"].sum(), 2)) if not expense_df.empty else 0.0,
            "total_income": float(round(income_df["amount"].sum(), 2)) if not income_df.empty else 0.0,
            "by_category": {k: float(v) for k, v in by_category.to_dict().items()},
            "top_category": by_category.index[0] if not by_category.empty else None,
        }
    finally:
        conn.close()


@tool
def detect_anomaly(customer_id: int, threshold_pct: float = 50.0) -> dict:
    """Bu ayin kategori bazli harcamalarini onceki 2 ayin ortalamasiyla
    karsilastirir; threshold_pct uzerinde artis olan kategorileri anomali
    olarak isaretler."""
    conn = _connect()
    try:
        current_start, current_end = _month_range()
        prev1_start, prev1_end = _previous_month_range(1)
        prev2_start, prev2_end = _previous_month_range(2)

        current_df = pd.read_sql(
            "SELECT category, amount FROM transactions "
            "WHERE customer_id = ? AND type = 'expense' AND date >= ? AND date < ?",
            conn,
            params=[customer_id, current_start, current_end],
        )
        prev_df = pd.read_sql(
            "SELECT category, amount FROM transactions "
            "WHERE customer_id = ? AND type = 'expense' AND date >= ? AND date < ?",
            conn,
            params=[customer_id, prev2_start, prev1_end],
        )

        current_by_cat = current_df.groupby("category")["amount"].sum() if not current_df.empty else pd.Series(dtype=float)
        # 2 onceki ayin ortalamasi (ay basina)
        prev_by_cat = (prev_df.groupby("category")["amount"].sum() / 2) if not prev_df.empty else pd.Series(dtype=float)

        anomalies = []
        for category, current_total in current_by_cat.items():
            avg_prev = prev_by_cat.get(category, 0.0)
            if avg_prev <= 0:
                continue
            pct_increase = round(((current_total - avg_prev) / avg_prev) * 100, 1)
            if pct_increase >= threshold_pct:
                anomalies.append(
                    {
                        "category": category,
                        "current_month_total": float(round(current_total, 2)),
                        "avg_previous_months": float(round(avg_prev, 2)),
                        "pct_increase": float(pct_increase),
                    }
                )

        anomalies.sort(key=lambda a: a["pct_increase"], reverse=True)
        return {"has_anomaly": len(anomalies) > 0, "anomalies": anomalies}
    finally:
        conn.close()
