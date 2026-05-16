from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from attendance_app.db import get_db

INSIDE_EVENT_TYPES = {"check_in", "break_start", "break_end"}


def record_attendance(
    staff: dict[str, Any],
    template_ref: str,
    confidence: int | None,
    method: str = "fingerprint",
    device_name: str = "fingerprint",
    notes: str = "",
    event_type: str | None = None,
    captured_at: datetime | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    gps_accuracy: float | None = None,
) -> dict[str, Any]:
    db = get_db()
    event_dt = captured_at or datetime.now()
    attendance_day = _resolve_event_attendance_day(
        staff,
        event_dt,
        event_type=event_type,
    )
    attendance_date = attendance_day.isoformat()
    resolved_event_type = event_type or _resolve_event_type(staff["id"], attendance_date)
    status_label = _status_label(staff, resolved_event_type, event_dt, attendance_day)

    cursor = db.execute(
        """
        INSERT INTO attendance_events (
            staff_id, attendance_date, event_time, event_type, status_label,
            method, template_ref, match_score, device_name, latitude, longitude, gps_accuracy,
            notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            staff["id"],
            attendance_date,
            event_dt.isoformat(timespec="microseconds"),
            resolved_event_type,
            status_label,
            method,
            template_ref,
            confidence,
            device_name,
            latitude,
            longitude,
            gps_accuracy,
            notes,
            datetime.now().isoformat(timespec="microseconds"),
        ),
    )
    db.commit()
    return {
        "id": int(cursor.lastrowid),
        "staff_id": staff["id"],
        "staff_code": staff["staff_code"],
        "staff_first_name": staff["first_name"],
        "staff_last_name": staff["last_name"],
        "staff_name": f"{staff['first_name']} {staff['last_name']}",
        "department": staff["department"],
        "role": staff["role"],
        "photo_filename": staff.get("photo_filename"),
        "event_type": resolved_event_type,
        "status_label": status_label,
        "event_time": event_dt,
        "latitude": latitude,
        "longitude": longitude,
        "gps_accuracy": gps_accuracy,
    }


def list_attendance_events(
    date_from: str = "",
    date_to: str = "",
    department: str = "",
    search: str = "",
    department_scope: str = "",
) -> list[dict[str, Any]]:
    db = get_db()
    query = """
        SELECT
            e.*,
            s.staff_code,
            s.first_name,
            s.last_name,
            s.photo_filename,
            s.department,
            s.role,
            s.access_role,
            s.shift_start,
            s.shift_end
        FROM attendance_events e
        JOIN staff s ON s.id = e.staff_id
        WHERE 1 = 1
    """
    params: list[Any] = []

    if date_from:
        query += " AND e.attendance_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND e.attendance_date <= ?"
        params.append(date_to)
    if department_scope:
        query += " AND s.department = ?"
        params.append(department_scope)
    if department:
        query += " AND s.department = ?"
        params.append(department)
    if search:
        query += """
            AND (
                s.staff_code LIKE ?
                OR s.first_name LIKE ?
                OR s.last_name LIKE ?
                OR s.role LIKE ?
                OR s.access_role LIKE ?
            )
        """
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard, wildcard, wildcard])

    query += " ORDER BY e.event_time DESC, e.id DESC"
    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_dashboard_data(
    fingerprint_adapter: str | None = None,
    department_scope: str = "",
    search: str = "",
    target_date: date | None = None,
) -> dict[str, Any]:
    target_day = target_date or date.today()
    today = target_day.isoformat()
    active_staff_rows = _active_staff(
        fingerprint_adapter=fingerprint_adapter,
        department_scope=department_scope,
        search=search,
    )
    total_staff = len(active_staff_rows)
    staff_ids = {int(staff["id"]) for staff in active_staff_rows}

    today_events = list_attendance_events(
        date_from=today,
        date_to=today,
        search=search,
        department_scope=department_scope,
    )
    today_events = [row for row in today_events if int(row["staff_id"]) in staff_ids]
    recent_events = today_events[:8]
    late_staff = {row["staff_id"] for row in today_events if row["status_label"] == "Late"}
    checked_in_staff = {row["staff_id"] for row in today_events if row["event_type"] == "check_in"}
    on_break_staff = {
        row["staff_id"]
        for row in _latest_events_map(today, department_scope=department_scope).values()
        if int(row["staff_id"]) in staff_ids
        if row["event_type"] == "break_start"
    }

    latest_events = _latest_events_map(today, department_scope=department_scope)
    latest_events = {
        staff_id: event
        for staff_id, event in latest_events.items()
        if int(staff_id) in staff_ids
    }
    currently_inside = sum(
        1 for event in latest_events.values() if event["event_type"] in INSIDE_EVENT_TYPES
    )

    roster = []
    for staff in active_staff_rows:
        latest = latest_events.get(staff["id"])
        if not latest:
            staff_status = "Absent today"
        elif latest["event_type"] == "check_in":
            event_time = datetime.fromisoformat(latest["event_time"]).strftime("%I:%M %p")
            staff_status = f"Working since {event_time}"
        elif latest["event_type"] == "break_start":
            event_time = datetime.fromisoformat(latest["event_time"]).strftime("%I:%M %p")
            staff_status = f"On break since {event_time}"
        elif latest["event_type"] == "break_end":
            event_time = datetime.fromisoformat(latest["event_time"]).strftime("%I:%M %p")
            staff_status = f"Back from break at {event_time}"
        else:
            event_time = datetime.fromisoformat(latest["event_time"]).strftime("%I:%M %p")
            staff_status = f"Checked out at {event_time}"

        roster.append(
            {
                **staff,
                "today_status": staff_status,
                "latest_event_type": latest["event_type"] if latest else "",
                "latest_status_label": latest["status_label"] if latest else "",
            }
        )

    department_totals = defaultdict(int)
    for staff in active_staff_rows:
        department_totals[staff["department"]] += 1

    department_cards = []
    for department_name, count in sorted(department_totals.items()):
        present = sum(
            1
            for row in roster
            if row["department"] == department_name and row["latest_event_type"] in INSIDE_EVENT_TYPES
        )
        department_cards.append(
            {
                "department": department_name,
                "count": count,
                "present": present,
            }
        )

    return {
        "total_staff": total_staff,
        "checked_in_today": len(checked_in_staff),
        "late_today": len(late_staff),
        "currently_inside": currently_inside,
        "on_break_today": len(on_break_staff),
        "recent_events": recent_events,
        "roster": roster,
        "department_cards": department_cards,
    }


def get_recent_events(limit: int = 8, department_scope: str = "") -> list[dict[str, Any]]:
    rows = list_attendance_events(department_scope=department_scope)
    return rows[:limit]


def report_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    latest_by_staff_day = {}
    break_starts = 0
    break_ends = 0
    for row in rows:
        latest_by_staff_day.setdefault((row["attendance_date"], row["staff_id"]), row)
        if row["event_type"] == "break_start":
            break_starts += 1
        if row["event_type"] == "break_end":
            break_ends += 1

    on_time = 0
    late = 0
    checked_out = 0
    for row in latest_by_staff_day.values():
        if row["status_label"] == "Late":
            late += 1
        if row["status_label"] == "On time":
            on_time += 1
        if row["event_type"] == "check_out":
            checked_out += 1

    return {
        "events": len(rows),
        "staff_seen": len({row["staff_id"] for row in rows}),
        "on_time": on_time,
        "late": late,
        "checked_out": checked_out,
        "break_starts": break_starts,
        "break_ends": break_ends,
    }


def get_staff_today_status(
    staff_id: int,
    target_date: date | None = None,
    reference_dt: datetime | None = None,
) -> dict[str, Any]:
    current_reference = reference_dt or datetime.now()
    shift_row = _staff_shift_row(staff_id)
    if target_date is not None:
        attendance_day = target_date
    elif shift_row:
        attendance_day = resolve_shift_attendance_date(
            shift_row.get("shift_start"),
            shift_row.get("shift_end"),
            current_reference,
        )
    else:
        attendance_day = current_reference.date()

    rows = _staff_events_for_day(staff_id, attendance_day.isoformat())
    check_in_at = None
    check_out_at = None
    active_break_started_at = None
    break_pairs: list[tuple[datetime, datetime]] = []

    for row in rows:
        event_dt = datetime.fromisoformat(row["event_time"])
        if row["event_type"] == "check_in" and check_in_at is None:
            check_in_at = event_dt
        elif row["event_type"] == "check_out":
            check_out_at = event_dt
        elif row["event_type"] == "break_start":
            active_break_started_at = event_dt
        elif row["event_type"] == "break_end" and active_break_started_at:
            break_pairs.append((active_break_started_at, event_dt))
            active_break_started_at = None

    total_break_minutes = sum(
        max(int((end - start).total_seconds() // 60), 0)
        for start, end in break_pairs
    )
    latest = rows[-1] if rows else None
    if not latest:
        current_state = "Not checked in"
        next_actions = ["check_in"]
    elif latest["event_type"] == "check_out":
        current_state = "Checked out"
        next_actions = ["check_in"]
    elif latest["event_type"] == "break_start":
        current_state = "On break"
        next_actions = ["break_end"]
    else:
        current_state = "Working"
        next_actions = ["break_start", "check_out"]

    active_work_end = None
    if check_in_at:
        active_work_end = datetime.fromisoformat(latest["event_time"]) if latest else datetime.now()
        if latest and latest["event_type"] == "check_out":
            active_work_end = datetime.fromisoformat(latest["event_time"])
    worked_minutes = 0
    if check_in_at and active_work_end:
        worked_minutes = max(
            int((active_work_end - check_in_at).total_seconds() // 60) - total_break_minutes,
            0,
        )

    return {
        "attendance_date": attendance_day.isoformat(),
        "rows": rows,
        "check_in_at": check_in_at,
        "check_out_at": check_out_at,
        "active_break_started_at": active_break_started_at,
        "latest_event": latest,
        "current_state": current_state,
        "next_actions": next_actions,
        "total_break_minutes": total_break_minutes,
        "worked_minutes": worked_minutes,
        "currently_inside": bool(latest and latest["event_type"] in INSIDE_EVENT_TYPES),
    }


def _resolve_event_type(staff_id: int, attendance_date: str) -> str:
    row = _latest_event_for_staff_day(staff_id, attendance_date)
    if not row or row["event_type"] == "check_out":
        return "check_in"
    if row["event_type"] == "break_start":
        return "break_end"
    return "check_out"


def _status_label(
    staff: dict[str, Any],
    event_type: str,
    event_dt: datetime,
    attendance_day: date,
) -> str:
    if event_type == "check_in":
        shift_start, _ = shift_bounds_for_date(
            attendance_day,
            staff.get("shift_start"),
            staff.get("shift_end"),
        )
        grace_deadline = shift_start + timedelta(minutes=int(staff["grace_minutes"]))
        if event_dt <= grace_deadline:
            return "On time"
        return "Late"
    if event_type == "break_start":
        return "Break started"
    if event_type == "break_end":
        return "Break ended"

    _, shift_end = shift_bounds_for_date(
        attendance_day,
        staff.get("shift_start"),
        staff.get("shift_end"),
    )
    if event_dt < shift_end:
        return "Early checkout"
    return "Completed shift"


def _combine_date_and_clock(current_date: date, clock_value: str) -> datetime:
    clock = _parse_clock(clock_value, time(9, 0))
    return datetime.combine(current_date, clock)


def shift_spans_overnight(shift_start: str | None, shift_end: str | None) -> bool:
    start_clock = _parse_clock(shift_start, time(9, 0))
    end_clock = _parse_clock(shift_end, time(17, 0))
    return end_clock <= start_clock


def shift_bounds_for_date(
    attendance_day: date,
    shift_start: str | None,
    shift_end: str | None,
) -> tuple[datetime, datetime]:
    start_clock = _parse_clock(shift_start, time(9, 0))
    end_clock = _parse_clock(shift_end, time(17, 0))
    shift_start_dt = datetime.combine(attendance_day, start_clock)
    shift_end_dt = datetime.combine(attendance_day, end_clock)
    if shift_end_dt <= shift_start_dt:
        shift_end_dt += timedelta(days=1)
    return shift_start_dt, shift_end_dt


def resolve_shift_attendance_date(
    shift_start: str | None,
    shift_end: str | None,
    event_dt: datetime,
) -> date:
    start_clock = _parse_clock(shift_start, time(9, 0))
    end_clock = _parse_clock(shift_end, time(17, 0))
    if end_clock <= start_clock and event_dt.time() <= end_clock:
        return event_dt.date() - timedelta(days=1)
    return event_dt.date()


def _resolve_event_attendance_day(
    staff: dict[str, Any],
    event_dt: datetime,
    event_type: str | None = None,
) -> date:
    shift_start = staff.get("shift_start")
    shift_end = staff.get("shift_end")
    event_day = event_dt.date()
    resolved_day = resolve_shift_attendance_date(shift_start, shift_end, event_dt)
    if not shift_spans_overnight(shift_start, shift_end):
        return resolved_day

    if event_type == "check_in":
        return event_day

    previous_day = event_day - timedelta(days=1)
    previous_latest = _latest_event_for_staff_day(staff["id"], previous_day.isoformat())
    current_latest = _latest_event_for_staff_day(staff["id"], event_day.isoformat())
    shift_start_clock = _parse_clock(shift_start, time(9, 0))
    if (
        previous_latest
        and previous_latest["event_type"] != "check_out"
        and current_latest is None
        and event_dt.time() < shift_start_clock
    ):
        return previous_day
    return resolved_day


def _parse_clock(clock_value: str | None, fallback: time) -> time:
    try:
        return time.fromisoformat(clock_value or fallback.isoformat(timespec="minutes"))
    except ValueError:
        return fallback


def _latest_events_map(attendance_date: str, department_scope: str = "") -> dict[int, dict[str, Any]]:
    db = get_db()
    params: list[Any] = [attendance_date]
    scope_clause = ""
    if department_scope:
        scope_clause = " AND s.department = ?"
        params.append(department_scope)

    rows = db.execute(
        """
        SELECT e.*
        FROM attendance_events e
        JOIN staff s ON s.id = e.staff_id
        JOIN (
            SELECT staff_id, MAX(id) AS last_event_id
            FROM attendance_events
            WHERE attendance_date = ?
            GROUP BY staff_id
        ) latest
            ON latest.last_event_id = e.id
        WHERE 1 = 1
        """
        + scope_clause,
        params,
    ).fetchall()
    return {row["staff_id"]: dict(row) for row in rows}


def _active_staff(
    fingerprint_adapter: str | None = None,
    department_scope: str = "",
    search: str = "",
) -> list[dict[str, Any]]:
    db = get_db()
    join_clause = """
        LEFT JOIN fingerprint_templates f
            ON f.staff_id = s.id AND f.is_active = 1
    """
    params: list[Any] = []
    if fingerprint_adapter:
        join_clause += " AND f.adapter = ?"
        params.append(fingerprint_adapter)

    query = """
        SELECT
            s.*,
            f.template_ref
        FROM staff s
        """
    query += join_clause
    query += """
        WHERE s.is_active = 1
        """
    if search:
        query += """
            AND (
                s.staff_code LIKE ?
                OR s.first_name LIKE ?
                OR s.last_name LIKE ?
                OR s.department LIKE ?
                OR s.role LIKE ?
                OR s.access_role LIKE ?
            )
        """
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard, wildcard, wildcard, wildcard])
    if department_scope:
        query += " AND s.department = ?"
        params.append(department_scope)
    query += " ORDER BY s.department, s.last_name, s.first_name"
    rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _latest_event_for_staff_day(staff_id: int, attendance_date: str) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT *
        FROM attendance_events
        WHERE staff_id = ? AND attendance_date = ?
        ORDER BY event_time DESC, id DESC
        LIMIT 1
        """,
        (staff_id, attendance_date),
    ).fetchone()
    return dict(row) if row else None


def _staff_events_for_day(staff_id: int, attendance_date: str) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT *
        FROM attendance_events
        WHERE staff_id = ? AND attendance_date = ?
        ORDER BY event_time ASC, id ASC
        """,
        (staff_id, attendance_date),
    ).fetchall()
    return [dict(row) for row in rows]


def _staff_shift_row(staff_id: int) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT shift_start, shift_end
        FROM staff
        WHERE id = ?
        LIMIT 1
        """,
        (staff_id,),
    ).fetchone()
    return dict(row) if row else None
