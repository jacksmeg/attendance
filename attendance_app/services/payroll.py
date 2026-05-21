from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any

from attendance_app.db import get_db

PAYROLL_STATUS_PENDING = "Pending"
PAYROLL_STATUS_PROCESSED = "Processed"
PAYROLL_STATUS_HOLD = "Hold"
PAYROLL_STATUSES = {
    PAYROLL_STATUS_PENDING,
    PAYROLL_STATUS_PROCESSED,
    PAYROLL_STATUS_HOLD,
}


def normalize_payroll_month(value: str) -> str:
    raw = value.strip()
    if not raw:
        today = date.today()
        return f"{today.year:04d}-{today.month:02d}"
    try:
        parsed = date.fromisoformat(f"{raw}-01")
    except ValueError:
        today = date.today()
        return f"{today.year:04d}-{today.month:02d}"
    return f"{parsed.year:04d}-{parsed.month:02d}"


def payroll_month_bounds(payroll_month: str) -> tuple[str, str]:
    normalized = normalize_payroll_month(payroll_month)
    year = int(normalized[:4])
    month = int(normalized[5:7])
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    return start.isoformat(), end.isoformat()


def get_payroll_status_map(payroll_month: str) -> dict[int, dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT staff_id, status, notes, processed_at
        FROM payroll_entries
        WHERE payroll_month = ?
        """,
        (normalize_payroll_month(payroll_month),),
    ).fetchall()
    return {
        int(row["staff_id"]): {
            "status": row["status"],
            "notes": row["notes"] or "",
            "processed_at": row["processed_at"] or "",
        }
        for row in rows
    }


def set_payroll_status(
    payroll_month: str,
    staff_id: int,
    status: str,
    *,
    notes: str = "",
) -> None:
    normalized_month = normalize_payroll_month(payroll_month)
    normalized_status = status.strip().title()
    if normalized_status not in PAYROLL_STATUSES:
        raise ValueError("Choose a valid payroll status.")
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    processed_at = now if normalized_status == PAYROLL_STATUS_PROCESSED else None
    db.execute(
        """
        INSERT INTO payroll_entries (
            payroll_month, staff_id, status, notes, processed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(payroll_month, staff_id)
        DO UPDATE SET
            status = excluded.status,
            notes = excluded.notes,
            processed_at = excluded.processed_at,
            updated_at = excluded.updated_at
        """,
        (
            normalized_month,
            staff_id,
            normalized_status,
            notes.strip(),
            processed_at,
            now,
        ),
    )
    db.commit()


def set_payroll_status_bulk(
    payroll_month: str,
    staff_ids: list[int],
    status: str,
) -> None:
    for staff_id in staff_ids:
        set_payroll_status(payroll_month, staff_id, status)
