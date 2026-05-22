from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
import sqlite3

from flask import g, has_request_context

from attendance_app.db import get_db
from attendance_app.services.settings import get_app_settings, get_app_settings_for_database


NOTIFICATION_CATEGORIES = {
    "attendance": "Attendance",
    "security": "Security",
    "payroll": "Payroll",
    "system": "System",
}

NOTIFICATION_PREFERENCE_MAP = {
    "attendance": "notification_attendance_enabled",
    "security": "notification_security_enabled",
    "payroll": "notification_payroll_enabled",
    "system": "notification_system_enabled",
}


def create_notification(
    *,
    title: str,
    message: str,
    category: str = "system",
    audience: str = "admin",
    tone: str = "neutral",
    action_url: str = "",
    target_staff_id: int | None = None,
) -> int | None:
    normalized_category = _normalize_category(category)
    if not _notifications_enabled(normalized_category):
        return None

    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO notification_events (
            audience,
            category,
            tone,
            title,
            message,
            action_url,
            target_staff_id,
            is_read,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            audience.strip() or "admin",
            normalized_category,
            tone.strip() or "neutral",
            title.strip() or "Notification",
            message.strip(),
            action_url.strip(),
            target_staff_id,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    _clear_notification_request_cache()
    return int(cursor.lastrowid)


def create_notification_for_database(
    database_path: Path,
    *,
    title: str,
    message: str,
    category: str = "system",
    audience: str = "admin",
    tone: str = "neutral",
    action_url: str = "",
    target_staff_id: int | None = None,
) -> int | None:
    normalized_category = _normalize_category(category)
    if not _notifications_enabled_for_database(database_path, normalized_category):
        return None

    db = sqlite3.connect(Path(database_path).resolve(), timeout=30)
    try:
        db.execute(
            """
            INSERT INTO notification_events (
                audience,
                category,
                tone,
                title,
                message,
                action_url,
                target_staff_id,
                is_read,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                audience.strip() or "admin",
                normalized_category,
                tone.strip() or "neutral",
                title.strip() or "Notification",
                message.strip(),
                action_url.strip(),
                target_staff_id,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        db.commit()
        return 1
    finally:
        db.close()


def list_notifications(
    *,
    limit: int = 20,
    audience: str = "admin",
    unread_only: bool = False,
    category: str = "",
    target_staff_id: int | None = None,
) -> list[dict[str, Any]]:
    cache = _notification_request_cache()
    cache_key = ("list", limit, audience, unread_only, category, target_staff_id)
    if cache_key in cache:
        return cache[cache_key]

    db = get_db()
    params: list[Any] = [audience]
    query = """
        SELECT *
        FROM notification_events
        WHERE audience = ?
    """
    if unread_only:
        query += " AND is_read = 0"
    normalized_category = _normalize_category(category) if category else ""
    if normalized_category:
        query += " AND category = ?"
        params.append(normalized_category)
    if target_staff_id is not None:
        query += " AND target_staff_id = ?"
        params.append(int(target_staff_id))
    query += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(max(1, limit))
    rows = [dict(row) for row in db.execute(query, params).fetchall()]
    cache[cache_key] = rows
    return rows


def count_unread_notifications(
    *,
    audience: str = "admin",
    category: str = "",
    target_staff_id: int | None = None,
) -> int:
    cache = _notification_request_cache()
    cache_key = ("count", audience, category, target_staff_id)
    if cache_key in cache:
        return cache[cache_key]

    db = get_db()
    params: list[Any] = [audience]
    query = """
        SELECT COUNT(*) AS count
        FROM notification_events
        WHERE audience = ? AND is_read = 0
    """
    normalized_category = _normalize_category(category) if category else ""
    if normalized_category:
        query += " AND category = ?"
        params.append(normalized_category)
    if target_staff_id is not None:
        query += " AND target_staff_id = ?"
        params.append(int(target_staff_id))
    row = db.execute(query, params).fetchone()
    value = int(row["count"]) if row else 0
    cache[cache_key] = value
    return value


def mark_notification_read(notification_id: int, *, audience: str = "admin") -> bool:
    db = get_db()
    cursor = db.execute(
        """
        UPDATE notification_events
        SET is_read = 1,
            read_at = ?
        WHERE id = ? AND audience = ?
        """,
        (datetime.now().isoformat(timespec="seconds"), int(notification_id), audience),
    )
    db.commit()
    _clear_notification_request_cache()
    return cursor.rowcount > 0


def mark_all_notifications_read(*, audience: str = "admin") -> int:
    db = get_db()
    cursor = db.execute(
        """
        UPDATE notification_events
        SET is_read = 1,
            read_at = ?
        WHERE audience = ? AND is_read = 0
        """,
        (datetime.now().isoformat(timespec="seconds"), audience),
    )
    db.commit()
    _clear_notification_request_cache()
    return int(cursor.rowcount)


def notification_category_options() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label in NOTIFICATION_CATEGORIES.items()]


def format_notification_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted: list[dict[str, Any]] = []
    for row in rows:
        created_at = str(row.get("created_at") or "")
        formatted.append(
            {
                **row,
                "category_label": NOTIFICATION_CATEGORIES.get(
                    str(row.get("category") or "").strip().lower(),
                    "System",
                ),
                "created_label": _notification_time_label(created_at),
                "is_read": bool(row.get("is_read")),
            }
        )
    return formatted


def _notification_request_cache() -> dict[Any, Any]:
    if has_request_context():
        cache = getattr(g, "_notification_cache", None)
        if cache is None:
            cache = {}
            g._notification_cache = cache
        return cache
    return {}


def _clear_notification_request_cache() -> None:
    if has_request_context():
        g.pop("_notification_cache", None)


def _normalize_category(category: str) -> str:
    cleaned = str(category or "").strip().lower()
    return cleaned if cleaned in NOTIFICATION_CATEGORIES else "system"


def _notifications_enabled(category: str) -> bool:
    settings = get_app_settings()
    key = NOTIFICATION_PREFERENCE_MAP.get(category)
    if not key:
        return True
    return bool(settings.get(key, True))


def _notifications_enabled_for_database(database_path: Path, category: str) -> bool:
    settings = get_app_settings_for_database(Path(database_path).resolve())
    key = NOTIFICATION_PREFERENCE_MAP.get(category)
    if not key:
        return True
    return bool(settings.get(key, True))


def _notification_time_label(value: str) -> str:
    if not value:
        return "Just now"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%b %d, %Y %I:%M %p")
