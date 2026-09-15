"""
Слой работы с базой данных (SQLite).

Хранит:
- companies: карточки фулфилмент-компаний (название, сайт, контакты для заявок)
- tariffs: тарифы компаний по видам услуг (цена + единица измерения + комментарий)
- requests_log: лог отправленных заявок (для истории)
"""

import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).parent / "fulfillbot.db"

# Виды услуг, которые сравниваем. Ключ -> человекочитаемое название.
SERVICE_TYPES = {
    "receiving_place": "Приёмка места (короб/паллета)",
    "receiving_unit": "Приёмка единицы товара",
    "marking": "Маркировка/стикеровка",
    "packing": "Упаковка/сборка заказа",
    "storage": "Хранение",
    "shipment_prep": "Оформление поставки в кабинете",
    "shipping": "Доставка до склада площадки",
    "min_check": "Минимальный чек в месяц",
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with closing(get_conn()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                website TEXT,
                city TEXT,
                price_file TEXT,
                price_file_original_name TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tariffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                service_type TEXT NOT NULL,
                price REAL,
                unit TEXT,
                comment TEXT,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(company_id, service_type)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                status TEXT,
                message TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tariff_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                service_type TEXT NOT NULL,
                old_price REAL,
                old_unit TEXT,
                old_comment TEXT,
                changed_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        # Миграция: добавляем новые колонки, если база уже существовала без них
        # (например, уже развёрнута на хостинге со старой схемой)
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(companies)")}
        for col_def in [
            ("price_file", "TEXT"),
            ("price_file_original_name", "TEXT"),
            ("ai_feedback", "TEXT"),
            ("ai_feedback_status", "TEXT"),
        ]:
            col_name, col_type = col_def
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE companies ADD COLUMN {col_name} {col_type}")


# ---------- companies ----------

def add_company(name, website=None, city=None, price_file=None, price_file_original_name=None,
                 contact_email=None, contact_phone=None, notes=None):
    with closing(get_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO companies (name, website, city, price_file, price_file_original_name, "
            "contact_email, contact_phone, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, website, city, price_file, price_file_original_name, contact_email, contact_phone, notes),
        )
        return cur.lastrowid


def set_price_file(company_id, price_file, price_file_original_name):
    with closing(get_conn()) as conn, conn:
        conn.execute(
            "UPDATE companies SET price_file = ?, price_file_original_name = ?, "
            "ai_feedback = NULL, ai_feedback_status = NULL WHERE id = ?",
            (price_file, price_file_original_name, company_id),
        )


def set_ai_feedback(company_id, feedback, status):
    with closing(get_conn()) as conn, conn:
        conn.execute(
            "UPDATE companies SET ai_feedback = ?, ai_feedback_status = ? WHERE id = ?",
            (feedback, status, company_id),
        )


def list_companies():
    with closing(get_conn()) as conn:
        return conn.execute("SELECT * FROM companies ORDER BY name").fetchall()


def get_company_by_name(name):
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM companies WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()


def get_company(company_id):
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()


def delete_company(name):
    with closing(get_conn()) as conn, conn:
        conn.execute("DELETE FROM companies WHERE name = ? COLLATE NOCASE", (name,))


def update_company_details(company_id, website=None, city=None):
    with closing(get_conn()) as conn, conn:
        conn.execute(
            "UPDATE companies SET website = ?, city = ? WHERE id = ?",
            (website, city, company_id),
        )


# ---------- tariffs ----------

def set_tariff(company_id, service_type, price=None, unit=None, comment=None):
    with closing(get_conn()) as conn, conn:
        existing = conn.execute(
            "SELECT price, unit, comment FROM tariffs WHERE company_id = ? AND service_type = ?",
            (company_id, service_type),
        ).fetchone()
        # логируем только реальное изменение цены (не первое сохранение "в пустоту")
        if existing is not None and existing["price"] != price:
            conn.execute(
                "INSERT INTO tariff_history (company_id, service_type, old_price, old_unit, old_comment) "
                "VALUES (?, ?, ?, ?, ?)",
                (company_id, service_type, existing["price"], existing["unit"], existing["comment"]),
            )
        conn.execute(
            """
            INSERT INTO tariffs (company_id, service_type, price, unit, comment, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(company_id, service_type)
            DO UPDATE SET price=excluded.price, unit=excluded.unit,
                          comment=excluded.comment, updated_at=datetime('now')
            """,
            (company_id, service_type, price, unit, comment),
        )


def get_tariff_history(company_id, service_type=None):
    with closing(get_conn()) as conn:
        if service_type:
            return conn.execute(
                "SELECT * FROM tariff_history WHERE company_id = ? AND service_type = ? "
                "ORDER BY changed_at DESC",
                (company_id, service_type),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM tariff_history WHERE company_id = ? ORDER BY changed_at DESC",
            (company_id,),
        ).fetchall()


def get_tariffs_for_company(company_id):
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM tariffs WHERE company_id = ?", (company_id,)
        ).fetchall()


def compare_service(service_type):
    """Вернуть все тарифы по указанному виду услуги, отсортированные по цене."""
    with closing(get_conn()) as conn:
        return conn.execute(
            """
            SELECT c.name, c.website, c.city, t.price, t.unit, t.comment, t.updated_at
            FROM tariffs t
            JOIN companies c ON c.id = t.company_id
            WHERE t.service_type = ? AND t.price IS NOT NULL
            ORDER BY t.price ASC
            """,
            (service_type,),
        ).fetchall()


# ---------- requests log ----------

def log_request(company_id, status, message):
    with closing(get_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO requests_log (company_id, status, message) VALUES (?, ?, ?)",
            (company_id, status, message),
        )
