from __future__ import annotations

from pathlib import Path
import sqlite3

from flask import current_app, g


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        settings = current_app.config["APP_SETTINGS"]
        g.db = sqlite3.connect(settings.database_path)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_: Exception | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    schema_path = Path(__file__).with_name("schema.sql")
    db.executescript(schema_path.read_text(encoding="utf-8"))
    _ensure_column(db, "fingerprint_templates", "template_format", "TEXT")
    _ensure_column(db, "fingerprint_templates", "template_data", "BLOB")
    _ensure_column(db, "staff", "phone", "TEXT")
    _ensure_column(db, "staff", "photo_filename", "TEXT")
    _ensure_column(db, "staff", "ghana_card_number", "TEXT")
    _ensure_column(db, "staff", "nationality", "TEXT")
    _ensure_column(db, "staff", "sex", "TEXT")
    _ensure_column(db, "staff", "date_of_birth", "TEXT")
    _ensure_column(db, "staff", "place_of_birth", "TEXT")
    _ensure_column(db, "staff", "residential_address", "TEXT")
    _ensure_column(db, "staff", "digital_address", "TEXT")
    _ensure_column(db, "staff", "ghana_card_verified_at", "TEXT")
    _ensure_column(db, "staff", "ghana_card_verified_by", "TEXT")
    _ensure_column(db, "staff", "access_role", "TEXT NOT NULL DEFAULT 'Staff'")
    _ensure_column(db, "staff", "password_hash", "TEXT")
    _ensure_column(db, "staff", "pin_hash", "TEXT")
    _ensure_column(db, "staff", "qr_token", "TEXT")
    _ensure_column(db, "staff", "allow_mobile_clock", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(db, "staff", "allow_pin_clock", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(db, "staff", "allow_qr_clock", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(db, "attendance_events", "latitude", "REAL")
    _ensure_column(db, "attendance_events", "longitude", "REAL")
    _ensure_column(db, "attendance_events", "gps_accuracy", "REAL")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_staff_qr_token ON staff(qr_token)")
    db.commit()


def _ensure_column(db: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {row["name"] for row in rows}
    if column_name in existing:
        return
    db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
