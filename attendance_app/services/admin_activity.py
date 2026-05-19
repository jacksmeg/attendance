from __future__ import annotations

from datetime import datetime
from typing import Any

from attendance_app.db import get_db


def log_admin_activity(
    *,
    actor_type: str,
    actor_name: str,
    event_type: str,
    actor_role: str = "",
    target_name: str = "",
    details: str = "",
    ip_address: str = "",
    device_name: str = "",
) -> int:
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO admin_activity_logs (
            actor_type,
            actor_name,
            actor_role,
            event_type,
            target_name,
            details,
            ip_address,
            device_name,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_type.strip() or "user",
            actor_name.strip() or "Unknown User",
            actor_role.strip(),
            event_type.strip() or "activity_recorded",
            target_name.strip(),
            details.strip(),
            ip_address.strip(),
            device_name.strip(),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def list_admin_activity_logs(limit: int = 60) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT *
        FROM admin_activity_logs
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    return [dict(row) for row in rows]
