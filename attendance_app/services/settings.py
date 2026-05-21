from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping
import hmac
import sqlite3

from flask import g, has_request_context

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
    "location_enforcement_enabled": "0",
    "allowed_location_name": "",
    "allowed_location_address": "",
    "allowed_location_latitude": "",
    "allowed_location_longitude": "",
    "allowed_location_radius_meters": "150",
}

ADMIN_PASSWORD_HASH_KEY = "platform_admin_password_hash"
ADMIN_USERNAME_KEY = "institution_admin_username"


def get_app_settings(default_app_name: str = "") -> dict[str, Any]:
    if has_request_context():
        request_cache = getattr(g, "_app_settings_cache", None)
        if request_cache is None:
            request_cache = {}
            g._app_settings_cache = request_cache
        cache_key = default_app_name or ""
        if cache_key in request_cache:
            return request_cache[cache_key]

    db = get_db()
    try:
        rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    except sqlite3.OperationalError:
        rows = []
    settings = _deserialize_app_settings_rows(rows, default_app_name=default_app_name)
    if has_request_context():
        g._app_settings_cache[default_app_name or ""] = settings
    return settings


def get_app_settings_for_database(database_path: Path, default_app_name: str = "") -> dict[str, Any]:
    return _cached_app_settings_for_database(str(Path(database_path).resolve()), default_app_name)


@lru_cache(maxsize=256)
def _cached_app_settings_for_database(database_path: str, default_app_name: str = "") -> dict[str, Any]:
    db = sqlite3.connect(database_path)
    db.row_factory = sqlite3.Row
    try:
        try:
            rows = db.execute("SELECT key, value FROM app_settings").fetchall()
        except sqlite3.OperationalError:
            rows = []
        return _deserialize_app_settings_rows(rows, default_app_name=default_app_name)
    finally:
        db.close()


def _deserialize_app_settings_rows(rows, *, default_app_name: str = "") -> dict[str, Any]:
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
        "location_enforcement_enabled": _to_bool(merged.get("location_enforcement_enabled")),
        "allowed_location_name": str(merged.get("allowed_location_name", "") or "").strip(),
        "allowed_location_address": str(merged.get("allowed_location_address", "") or "").strip(),
        "allowed_location_latitude": _to_float(merged.get("allowed_location_latitude")),
        "allowed_location_longitude": _to_float(merged.get("allowed_location_longitude")),
        "allowed_location_radius_meters": max(
            25,
            _to_int(merged.get("allowed_location_radius_meters"), 150),
        ),
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
        "location_enforcement_enabled": "1" if _to_bool(data.get("location_enforcement_enabled")) else "0",
        "allowed_location_name": str(data.get("allowed_location_name", "") or "").strip(),
        "allowed_location_address": str(data.get("allowed_location_address", "") or "").strip(),
        "allowed_location_latitude": _normalize_optional_float(data.get("allowed_location_latitude")),
        "allowed_location_longitude": _normalize_optional_float(data.get("allowed_location_longitude")),
        "allowed_location_radius_meters": str(
            max(25, _to_int(data.get("allowed_location_radius_meters"), 150))
        ),
    }
    timestamp = datetime.now().isoformat(timespec="seconds")
    for key, value in normalized.items():
        _upsert_setting(db, key, value, timestamp=timestamp)
    db.commit()
    _clear_settings_caches()
    return get_app_settings(default_app_name=default_app_name)


def get_admin_security(default_username: str = "") -> dict[str, Any]:
    if has_request_context():
        request_cache = getattr(g, "_admin_security_cache", None)
        if request_cache is None:
            request_cache = {}
            g._admin_security_cache = request_cache
        cache_key = default_username or ""
        if cache_key in request_cache:
            return request_cache[cache_key]

    db = get_db()
    security = _read_admin_security(db, default_username=default_username)
    if has_request_context():
        g._admin_security_cache[default_username or ""] = security
    return security


def get_admin_security_for_database(database_path: Path, default_username: str = "") -> dict[str, Any]:
    return _cached_admin_security_for_database(str(Path(database_path).resolve()), default_username)


@lru_cache(maxsize=256)
def _cached_admin_security_for_database(database_path: str, default_username: str = "") -> dict[str, Any]:
    db = sqlite3.connect(database_path)
    db.row_factory = sqlite3.Row
    try:
        return _read_admin_security(db, default_username=default_username)
    finally:
        db.close()


def _read_admin_security(db, default_username: str = "") -> dict[str, Any]:
    username_row = db.execute(
        "SELECT value, updated_at FROM app_settings WHERE key = ?",
        (ADMIN_USERNAME_KEY,),
    ).fetchone()
    row = db.execute(
        "SELECT value, updated_at FROM app_settings WHERE key = ?",
        (ADMIN_PASSWORD_HASH_KEY,),
    ).fetchone()
    stored_username = str(username_row["value"]).strip() if username_row and username_row["value"] else ""
    admin_username = stored_username or default_username
    return {
        "admin_username": admin_username,
        "username_is_custom": bool(stored_username and stored_username != default_username),
        "password_is_custom": bool(row and row["value"]),
        "username_updated_at": str(username_row["updated_at"]) if username_row and username_row["updated_at"] else "",
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
    _clear_settings_caches()
    row = db.execute(
        "SELECT updated_at FROM app_settings WHERE key = ?",
        (ADMIN_PASSWORD_HASH_KEY,),
    ).fetchone()
    return {
        "password_is_custom": True,
        "password_updated_at": str(row["updated_at"]) if row and row["updated_at"] else "",
    }


def save_admin_password_for_database(database_path: Path, new_password: str) -> None:
    db = sqlite3.connect(Path(database_path).resolve())
    try:
        _upsert_setting(
            db,
            ADMIN_PASSWORD_HASH_KEY,
            hash_secret(new_password),
            timestamp=datetime.now().isoformat(timespec="seconds"),
        )
        db.commit()
        _clear_settings_caches()
    finally:
        db.close()


def save_admin_credentials_for_database(
    database_path: Path,
    *,
    username: str,
    password: str | None = None,
) -> None:
    cleaned_username = str(username or "").strip()
    if not cleaned_username:
        raise ValueError("Institution admin username is required.")

    db = sqlite3.connect(Path(database_path).resolve())
    try:
        timestamp = datetime.now().isoformat(timespec="seconds")
        _upsert_setting(
            db,
            ADMIN_USERNAME_KEY,
            cleaned_username,
            timestamp=timestamp,
        )
        if password:
            _upsert_setting(
                db,
                ADMIN_PASSWORD_HASH_KEY,
                hash_secret(password),
                timestamp=timestamp,
            )
        db.commit()
        _clear_settings_caches()
    finally:
        db.close()


def _to_int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: object) -> float | None:
    try:
        cleaned = str(value).strip()
        if not cleaned:
            return None
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _to_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_optional_float(value: object) -> str:
    parsed = _to_float(value)
    if parsed is None:
        return ""
    return f"{parsed:.6f}"


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


def _clear_settings_caches() -> None:
    _cached_app_settings_for_database.cache_clear()
    _cached_admin_security_for_database.cache_clear()
    if has_request_context():
        g.pop("_app_settings_cache", None)
        g.pop("_admin_security_cache", None)
