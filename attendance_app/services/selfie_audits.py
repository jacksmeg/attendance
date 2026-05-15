from __future__ import annotations

from datetime import datetime
from typing import Any

from attendance_app.db import get_db


def create_staff_selfie_audit(
    *,
    staff_id: int,
    login_identifier: str,
    auth_method: str,
    photo_filename: str,
    photo_mime_type: str,
    file_size_bytes: int,
    ip_address: str,
    device_name: str,
    audit_type: str = "staff_login",
) -> int:
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO staff_selfie_audits (
            staff_id,
            audit_type,
            login_identifier,
            auth_method,
            photo_filename,
            photo_mime_type,
            file_size_bytes,
            ip_address,
            device_name,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            staff_id,
            audit_type,
            login_identifier,
            auth_method,
            photo_filename,
            photo_mime_type,
            file_size_bytes,
            ip_address,
            device_name,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def list_staff_selfie_audits(limit: int = 50) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            a.*,
            s.staff_code,
            s.first_name,
            s.last_name,
            s.department,
            s.photo_filename AS staff_photo_filename
        FROM staff_selfie_audits a
        JOIN staff s ON s.id = a.staff_id
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    return [dict(row) for row in rows]
