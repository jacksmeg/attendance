from __future__ import annotations

from io import StringIO
import csv


def attendance_rows_to_csv(rows: list[dict[str, object]]) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Date",
            "Time",
            "Staff Code",
            "Name",
            "Department",
            "Role",
            "Event",
            "Status",
            "Method",
            "Latitude",
            "Longitude",
            "GPS Accuracy",
            "Score",
            "Device",
            "Notes",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row["attendance_date"],
                row["event_time"],
                row["staff_code"],
                f"{row['first_name']} {row['last_name']}",
                row["department"],
                row["role"],
                row["event_type"],
                row["status_label"],
                row["method"],
                row["latitude"] if row.get("latitude") is not None else "",
                row["longitude"] if row.get("longitude") is not None else "",
                row["gps_accuracy"] if row.get("gps_accuracy") is not None else "",
                row["match_score"] or "",
                row["device_name"] or "",
                row["notes"] or "",
            ]
        )
    return output.getvalue()


def build_report_snapshot(
    rows: list[dict[str, object]],
    active_staff_total: int,
) -> dict[str, object]:
    daily_map: dict[str, dict[str, object]] = {}
    department_map: dict[str, dict[str, object]] = {}
    staff_map: dict[tuple[object, object], dict[str, object]] = {}

    for row in rows:
        attendance_date = str(row["attendance_date"])
        department = str(row["department"])
        staff_id = row["staff_id"]
        status_label = str(row["status_label"])
        event_type = str(row["event_type"])
        event_time = str(row["event_time"])

        day_entry = daily_map.setdefault(
            attendance_date,
            {
                "attendance_date": attendance_date,
                "events": 0,
                "staff_seen_set": set(),
                "on_time": 0,
                "late": 0,
                "early_checkout": 0,
                "completed_shift": 0,
                "check_ins": 0,
                "check_outs": 0,
                "break_starts": 0,
                "break_ends": 0,
            },
        )
        day_entry["events"] += 1
        day_entry["staff_seen_set"].add(staff_id)
        _bump_status_counts(day_entry, status_label, event_type)

        dept_entry = department_map.setdefault(
            department,
            {
                "department": department,
                "events": 0,
                "staff_seen_set": set(),
                "on_time": 0,
                "late": 0,
                "early_checkout": 0,
                "completed_shift": 0,
                "break_starts": 0,
                "break_ends": 0,
            },
        )
        dept_entry["events"] += 1
        dept_entry["staff_seen_set"].add(staff_id)
        _bump_status_counts(dept_entry, status_label, event_type)

        staff_key = (staff_id, row["staff_code"])
        staff_entry = staff_map.setdefault(
            staff_key,
            {
                "staff_id": staff_id,
                "staff_code": row["staff_code"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "department": department,
                "role": row["role"],
                "check_ins": 0,
                "check_outs": 0,
                "on_time": 0,
                "late": 0,
                "early_checkout": 0,
                "completed_shift": 0,
                "break_starts": 0,
                "break_ends": 0,
                "last_seen": event_time,
            },
        )
        _bump_status_counts(staff_entry, status_label, event_type)
        if event_time > str(staff_entry["last_seen"]):
            staff_entry["last_seen"] = event_time

    daily_rows: list[dict[str, object]] = []
    for entry in daily_map.values():
        staff_seen = len(entry.pop("staff_seen_set"))
        daily_rows.append(
            {
                **entry,
                "staff_seen": staff_seen,
                "absent": max(active_staff_total - staff_seen, 0),
            }
        )
    daily_rows.sort(key=lambda item: item["attendance_date"], reverse=True)

    department_rows: list[dict[str, object]] = []
    for entry in department_map.values():
        department_rows.append(
            {
                **entry,
                "staff_seen": len(entry.pop("staff_seen_set")),
            }
        )
    department_rows.sort(key=lambda item: (-int(item["events"]), str(item["department"])))

    staff_rows = list(staff_map.values())
    staff_rows.sort(
        key=lambda item: (
            -int(item["late"]),
            str(item["last_name"]),
            str(item["first_name"]),
        )
    )

    return {
        "daily_rows": daily_rows,
        "department_rows": department_rows,
        "staff_rows": staff_rows,
    }


def report_snapshot_to_csv(snapshot: dict[str, object]) -> str:
    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(["Daily Summary"])
    writer.writerow(
        [
            "Date",
            "Events",
            "Staff Seen",
            "Absent",
            "On Time",
            "Late",
            "Early Checkout",
            "Completed Shift",
            "Break Starts",
            "Break Ends",
        ]
    )
    for row in snapshot.get("daily_rows", []):
        writer.writerow(
            [
                row["attendance_date"],
                row["events"],
                row["staff_seen"],
                row["absent"],
                row["on_time"],
                row["late"],
                row["early_checkout"],
                row["completed_shift"],
                row["break_starts"],
                row["break_ends"],
            ]
        )

    writer.writerow([])
    writer.writerow(["Department Summary"])
    writer.writerow(
        [
            "Department",
            "Events",
            "Staff Seen",
            "On Time",
            "Late",
            "Early Checkout",
            "Completed Shift",
            "Break Starts",
            "Break Ends",
        ]
    )
    for row in snapshot.get("department_rows", []):
        writer.writerow(
            [
                row["department"],
                row["events"],
                row["staff_seen"],
                row["on_time"],
                row["late"],
                row["early_checkout"],
                row["completed_shift"],
                row["break_starts"],
                row["break_ends"],
            ]
        )

    writer.writerow([])
    writer.writerow(["Staff Summary"])
    writer.writerow(
        [
            "Staff Code",
            "Name",
            "Department",
            "Role",
            "Check Ins",
            "Check Outs",
            "On Time",
            "Late",
            "Early Checkout",
            "Completed Shift",
            "Break Starts",
            "Break Ends",
            "Last Seen",
        ]
    )
    for row in snapshot.get("staff_rows", []):
        writer.writerow(
            [
                row["staff_code"],
                f"{row['first_name']} {row['last_name']}",
                row["department"],
                row["role"],
                row["check_ins"],
                row["check_outs"],
                row["on_time"],
                row["late"],
                row["early_checkout"],
                row["completed_shift"],
                row["break_starts"],
                row["break_ends"],
                row["last_seen"],
            ]
        )

    return output.getvalue()


def _bump_status_counts(
    target: dict[str, object],
    status_label: str,
    event_type: str,
) -> None:
    if event_type == "check_in":
        target["check_ins"] = int(target.get("check_ins", 0)) + 1
    if event_type == "check_out":
        target["check_outs"] = int(target.get("check_outs", 0)) + 1
    if event_type == "break_start":
        target["break_starts"] = int(target.get("break_starts", 0)) + 1
    if event_type == "break_end":
        target["break_ends"] = int(target.get("break_ends", 0)) + 1
    if status_label == "On time":
        target["on_time"] = int(target.get("on_time", 0)) + 1
    if status_label == "Late":
        target["late"] = int(target.get("late", 0)) + 1
    if status_label == "Early checkout":
        target["early_checkout"] = int(target.get("early_checkout", 0)) + 1
    if status_label == "Completed shift":
        target["completed_shift"] = int(target.get("completed_shift", 0)) + 1
