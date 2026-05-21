from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from attendance_app.db import get_db

DEFAULT_WORK_SHIFTS: list[dict[str, Any]] = [
    {
        "name": "Morning Shift",
        "code": "MOR",
        "shift_start": "08:00",
        "shift_end": "14:00",
        "break_label": "Flexible",
        "grace_minutes": 15,
        "weekly_off": "Configured per department",
        "description": "Morning coverage for clinical and support teams.",
    },
    {
        "name": "Afternoon Shift",
        "code": "AFT",
        "shift_start": "14:00",
        "shift_end": "20:00",
        "break_label": "Flexible",
        "grace_minutes": 15,
        "weekly_off": "Configured per department",
        "description": "Afternoon coverage for handover and patient support.",
    },
    {
        "name": "Night Shift",
        "code": "NIG",
        "shift_start": "20:00",
        "shift_end": "08:00",
        "break_label": "Flexible",
        "grace_minutes": 20,
        "weekly_off": "Configured per department",
        "description": "Overnight shift with automatic next-day attendance handling.",
    },
    {
        "name": "Extended Day Shift",
        "code": "DAY",
        "shift_start": "08:00",
        "shift_end": "18:00",
        "break_label": "01:00 PM - 02:00 PM",
        "grace_minutes": 15,
        "weekly_off": "Configured per department",
        "description": "Long daytime duty for units requiring extended coverage.",
    },
    {
        "name": "Extended Night Shift",
        "code": "XNT",
        "shift_start": "18:00",
        "shift_end": "08:00",
        "break_label": "Flexible",
        "grace_minutes": 20,
        "weekly_off": "Configured per department",
        "description": "Twelve-hour night coverage for emergency and ward teams.",
    },
    {
        "name": "General Shift",
        "code": "GEN",
        "shift_start": "08:00",
        "shift_end": "16:00",
        "break_label": "12:00 PM - 01:00 PM",
        "grace_minutes": 15,
        "weekly_off": "Configured per department",
        "description": "Regular daytime office and support coverage.",
    },
]


def ensure_default_shifts() -> None:
    db = get_db()
    existing_codes = {
        str(row["code"]).strip().upper()
        for row in db.execute("SELECT code FROM work_shifts").fetchall()
    }
    now = datetime.now().isoformat(timespec="seconds")
    inserted = False
    for item in DEFAULT_WORK_SHIFTS:
        if str(item["code"]).strip().upper() in existing_codes:
            continue
        db.execute(
            """
            INSERT INTO work_shifts (
                name, code, shift_start, shift_end, break_label, grace_minutes,
                weekly_off, description, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                item["name"],
                str(item["code"]).strip().upper(),
                item["shift_start"],
                item["shift_end"],
                item["break_label"],
                int(item["grace_minutes"]),
                item["weekly_off"],
                item["description"],
                now,
                now,
            ),
        )
        inserted = True

    if inserted:
        db.commit()
    sync_staff_shift_links()


def list_shifts(
    search: str = "",
    *,
    include_inactive: bool = True,
) -> list[dict[str, Any]]:
    db = get_db()
    query = """
        SELECT *
        FROM work_shifts
        WHERE 1 = 1
    """
    params: list[Any] = []
    if not include_inactive:
        query += " AND is_active = 1"
    if search:
        wildcard = f"%{search.strip()}%"
        query += """
            AND (
                name LIKE ?
                OR code LIKE ?
                OR description LIKE ?
            )
        """
        params.extend([wildcard, wildcard, wildcard])
    query += " ORDER BY is_active DESC, name"
    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_shift(shift_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT *
        FROM work_shifts
        WHERE id = ?
        LIMIT 1
        """,
        (shift_id,),
    ).fetchone()
    return dict(row) if row else None


def create_shift(data: Mapping[str, Any]) -> int:
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    cursor = db.execute(
        """
        INSERT INTO work_shifts (
            name, code, shift_start, shift_end, break_label, grace_minutes,
            weekly_off, description, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["name"],
            str(data["code"]).strip().upper(),
            data["shift_start"],
            data["shift_end"],
            data.get("break_label", "Flexible"),
            int(data.get("grace_minutes", 15)),
            data.get("weekly_off", "Configured per department"),
            data.get("description", ""),
            int(bool(data.get("is_active", True))),
            now,
            now,
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def update_shift(shift_id: int, data: Mapping[str, Any]) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE work_shifts
        SET
            name = ?,
            code = ?,
            shift_start = ?,
            shift_end = ?,
            break_label = ?,
            grace_minutes = ?,
            weekly_off = ?,
            description = ?,
            is_active = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            data["name"],
            str(data["code"]).strip().upper(),
            data["shift_start"],
            data["shift_end"],
            data.get("break_label", "Flexible"),
            int(data.get("grace_minutes", 15)),
            data.get("weekly_off", "Configured per department"),
            data.get("description", ""),
            int(bool(data.get("is_active", True))),
            datetime.now().isoformat(timespec="seconds"),
            shift_id,
        ),
    )
    db.execute(
        """
        UPDATE staff
        SET
            shift_start = ?,
            shift_end = ?,
            grace_minutes = ?,
            updated_at = ?
        WHERE shift_id = ?
        """,
        (
            data["shift_start"],
            data["shift_end"],
            int(data.get("grace_minutes", 15)),
            datetime.now().isoformat(timespec="seconds"),
            shift_id,
        ),
    )
    db.commit()


def delete_shift(shift_id: int) -> None:
    db = get_db()
    assigned_count = db.execute(
        "SELECT COUNT(*) AS total FROM staff WHERE shift_id = ?",
        (shift_id,),
    ).fetchone()["total"]
    if int(assigned_count) > 0:
        raise ValueError("You cannot delete a shift that still has staff assigned to it.")
    db.execute("DELETE FROM work_shifts WHERE id = ?", (shift_id,))
    db.commit()


def set_shift_active(shift_id: int, is_active: bool) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE work_shifts
        SET is_active = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            int(bool(is_active)),
            datetime.now().isoformat(timespec="seconds"),
            shift_id,
        ),
    )
    db.commit()


def assign_shift_to_staff(
    shift_id: int,
    staff_id: int,
    *,
    department_scope: str = "",
) -> bool:
    db = get_db()
    shift = get_shift(shift_id)
    if not shift:
        return False
    params: list[Any] = [
        shift_id,
        shift["shift_start"],
        shift["shift_end"],
        int(shift["grace_minutes"]),
        datetime.now().isoformat(timespec="seconds"),
        staff_id,
    ]
    query = """
        UPDATE staff
        SET shift_id = ?, shift_start = ?, shift_end = ?, grace_minutes = ?, updated_at = ?
        WHERE id = ?
    """
    if department_scope:
        query += " AND department = ?"
        params.append(department_scope)
    cursor = db.execute(query, params)
    db.commit()
    return cursor.rowcount > 0


def unassign_shift_from_staff(
    staff_id: int,
    *,
    department_scope: str = "",
) -> bool:
    db = get_db()
    params: list[Any] = [
        datetime.now().isoformat(timespec="seconds"),
        staff_id,
    ]
    query = """
        UPDATE staff
        SET shift_id = NULL, updated_at = ?
        WHERE id = ?
    """
    if department_scope:
        query += " AND department = ?"
        params.append(department_scope)
    cursor = db.execute(query, params)
    db.commit()
    return cursor.rowcount > 0


def list_shift_assignments(
    shift_id: int | None = None,
    *,
    department_scope: str = "",
) -> list[dict[str, Any]]:
    db = get_db()
    query = """
        SELECT
            s.id,
            s.staff_code,
            s.first_name,
            s.last_name,
            s.department,
            s.role,
            s.photo_filename,
            s.shift_id,
            s.shift_start,
            s.shift_end
        FROM staff s
        WHERE s.is_active = 1
    """
    params: list[Any] = []
    if shift_id is not None:
        query += " AND s.shift_id = ?"
        params.append(shift_id)
    if department_scope:
        query += " AND s.department = ?"
        params.append(department_scope)
    query += " ORDER BY s.department, s.last_name, s.first_name"
    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def find_shift_id_by_window(
    shift_start: str,
    shift_end: str,
    grace_minutes: int,
) -> int | None:
    db = get_db()
    row = db.execute(
        """
        SELECT id
        FROM work_shifts
        WHERE shift_start = ? AND shift_end = ? AND grace_minutes = ?
        LIMIT 1
        """,
        (shift_start, shift_end, int(grace_minutes)),
    ).fetchone()
    return int(row["id"]) if row else None


def sync_staff_shift_links() -> None:
    db = get_db()
    shift_rows = db.execute(
        "SELECT id, shift_start, shift_end, grace_minutes FROM work_shifts"
    ).fetchall()
    lookup = {
        (
            str(row["shift_start"]).strip(),
            str(row["shift_end"]).strip(),
            int(row["grace_minutes"]),
        ): int(row["id"])
        for row in shift_rows
    }
    staff_rows = db.execute(
        """
        SELECT id, shift_id, shift_start, shift_end, grace_minutes
        FROM staff
        WHERE shift_id IS NULL
        """
    ).fetchall()
    updated = False
    for row in staff_rows:
        desired_shift_id = lookup.get(
            (
                str(row["shift_start"]).strip(),
                str(row["shift_end"]).strip(),
                int(row["grace_minutes"]),
            )
        )
        if not desired_shift_id:
            continue
        db.execute(
            "UPDATE staff SET shift_id = ?, updated_at = ? WHERE id = ?",
            (
                desired_shift_id,
                datetime.now().isoformat(timespec="seconds"),
                int(row["id"]),
            ),
        )
        updated = True
    if updated:
        db.commit()
