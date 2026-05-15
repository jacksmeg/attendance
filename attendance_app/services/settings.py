from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping
import hmac

from attendance_app.auth import hash_secret, secret_matches
from attendance_app.db import get_db


WORKDAY_OPTIONS = [
    "Mon",
    "Tue",
    "Wed",
    "Thu",
    "Fri",
    "Sat",
    "Sun",
]

DEFAULT_SETTINGS = {
    "organization_name": "",
    "system_logo_filename": "",
    "default_shift_start": "09:00",
    "default_shift_end": "17:00",
    "default_grace_minutes": "15",
    "working_days": "Mon,Tue,Wed,Thu,Fri",
    "report_default_range_days": "30",
}

ADMIN_PASSWORD_HASH_KEY = "platform_admin_password_hash"


def get_app_settings(default_app_name: str = "") -> dict[str, Any]:
    db = get_db()
    rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    merged = {**DEFAULT_SETTINGS, **values}
    organization_name = str(merged.get("organization_name", "")).strip() or default_app_name
    working_days = _normalize_working_days(merged.get("working_days", ""))
    return {
        "organization_name": organization_name,
        "system_logo_filename": str(merged.get("system_logo_filename", "") or ""),
        "default_shift_start": str(merged.get("default_shift_start", "09:00")),
        "default_shift_end": str(merged.get("default_shift_end", "17:00")),
        "default_grace_minutes": max(0, _to_int(merged.get("default_grace_minutes"), 15)),
        "working_days": working_days,
        "working_days_value": ",".join(working_days),
        "report_default_range_days": max(1, _to_int(merged.get("report_default_range_days"), 30)),
    }


def save_app_settings(data: Mapping[str, Any], default_app_name: str = "") -> dict[str, Any]:
    db = get_db()
    normalized = {
        "organization_name": str(data.get("organization_name", "")).strip() or default_app_name,
        "system_logo_filename": str(data.get("system_logo_filename", "") or "").strip(),
        "default_shift_start": str(data.get("default_shift_start", "09:00")).strip() or "09:00",
        "default_shift_end": str(data.get("default_shift_end", "17:00")).strip() or "17:00",
        "default_grace_minutes": str(max(0, _to_int(data.get("default_grace_minutes"), 15))),
        "working_days": ",".join(_normalize_working_days(data.get("working_days", ""))),
        "report_default_range_days": str(max(1, _to_int(data.get("report_default_range_days"), 30))),
    }
    timestamp = datetime.now().isoformat(timespec="seconds")
    for key, value in normalized.items():
        _upsert_setting(db, key, value, timestamp=timestamp)
    db.commit()
    return get_app_settings(default_app_name=default_app_name)


def get_admin_security(default_username: str = "") -> dict[str, Any]:
    db = get_db()
    row = db.execute(
        "SELECT value, updated_at FROM app_settings WHERE key = ?",
        (ADMIN_PASSWORD_HASH_KEY,),
    ).fetchone()
    return {
        "admin_username": default_username,
        "password_is_custom": bool(row and row["value"]),
        "password_updated_at": str(row["updated_at"]) if row and row["updated_at"] else "",
    }


def admin_password_matches(password: str, default_password: str) -> bool:
    if not password:
        return False

    db = get_db()
    row = db.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (ADMIN_PASSWORD_HASH_KEY,),
    ).fetchone()
    stored_hash = str(row["value"]) if row and row["value"] else ""
    if stored_hash:
        return secret_matches(stored_hash, password)
    return hmac.compare_digest(default_password, password)


def save_admin_password(new_password: str) -> dict[str, Any]:
    db = get_db()
    _upsert_setting(
        db,
        ADMIN_PASSWORD_HASH_KEY,
        hash_secret(new_password),
        timestamp=datetime.now().isoformat(timespec="seconds"),
    )
    db.commit()
    row = db.execute(
        "SELECT updated_at FROM app_settings WHERE key = ?",
        (ADMIN_PASSWORD_HASH_KEY,),
    ).fetchone()
    return {
        "password_is_custom": True,
        "password_updated_at": str(row["updated_at"]) if row and row["updated_at"] else "",
    }


def _to_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalize_working_days(value: object) -> list[str]:
    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",")]
    elif isinstance(value, Iterable):
        raw_values = [str(part).strip() for part in value]
    else:
        raw_values = []

    cleaned = [day for day in raw_values if day in WORKDAY_OPTIONS]
    if cleaned:
        return cleaned
    return ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _upsert_setting(
    db,
    key: str,
    value: str,
    *,
    timestamp: str,
) -> None:
    db.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, timestamp),
    )
