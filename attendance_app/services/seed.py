from __future__ import annotations

from datetime import date, datetime, time, timedelta

from attendance_app.db import get_db
from attendance_app.services.attendance import record_attendance
from attendance_app.services.staff import create_staff, get_staff, upsert_fingerprint


def seed_demo_data() -> bool:
    db = get_db()
    existing = db.execute("SELECT COUNT(*) AS count FROM staff").fetchone()["count"]
    if existing:
        return False

    demo_staff = [
        {
            "staff_code": "EMP-001",
            "first_name": "Amara",
            "last_name": "Okafor",
            "email": "amara@organization.local",
            "department": "Operations",
            "role": "Operations Lead",
            "access_role": "Department Manager",
            "portal_password": "Amara@123",
            "portal_pin": "1001",
            "shift_start": "08:30",
            "shift_end": "17:00",
            "grace_minutes": 10,
            "is_active": True,
        },
        {
            "staff_code": "EMP-002",
            "first_name": "David",
            "last_name": "Mensah",
            "email": "david@organization.local",
            "department": "Finance",
            "role": "Finance Officer",
            "access_role": "Staff",
            "portal_password": "David@123",
            "portal_pin": "1002",
            "shift_start": "09:00",
            "shift_end": "17:00",
            "grace_minutes": 15,
            "is_active": True,
        },
        {
            "staff_code": "EMP-003",
            "first_name": "Grace",
            "last_name": "Bello",
            "email": "grace@organization.local",
            "department": "Human Resources",
            "role": "HR Specialist",
            "access_role": "HR/Admin",
            "portal_password": "Grace@123",
            "portal_pin": "1003",
            "shift_start": "08:00",
            "shift_end": "16:00",
            "grace_minutes": 5,
            "is_active": True,
        },
        {
            "staff_code": "EMP-004",
            "first_name": "Ibrahim",
            "last_name": "Adeleke",
            "email": "ibrahim@organization.local",
            "department": "IT Support",
            "role": "Systems Analyst",
            "access_role": "Supervisor",
            "portal_password": "Ibrahim@123",
            "portal_pin": "1004",
            "shift_start": "09:00",
            "shift_end": "18:00",
            "grace_minutes": 10,
            "is_active": True,
        },
    ]

    created_staff = []
    for index, staff_data in enumerate(demo_staff, start=1):
        staff_id = create_staff(staff_data)
        upsert_fingerprint(
            staff_id,
            adapter="mock",
            template_ref=f"MOCK-{staff_data['staff_code']}-{index:02d}",
            quality_score=100,
            notes="Seeded demo fingerprint.",
        )
        created_staff.append(get_staff(staff_id))

    today = date.today()
    yesterday = today - timedelta(days=1)

    record_attendance(
        created_staff[0],
        template_ref=created_staff[0]["template_ref"],
        confidence=97,
        captured_at=datetime.combine(today, time(8, 29)),
    )
    record_attendance(
        created_staff[1],
        template_ref=created_staff[1]["template_ref"],
        confidence=91,
        captured_at=datetime.combine(today, time(9, 18)),
    )
    record_attendance(
        created_staff[2],
        template_ref=created_staff[2]["template_ref"],
        confidence=95,
        captured_at=datetime.combine(today, time(7, 59)),
    )
    record_attendance(
        created_staff[2],
        template_ref=created_staff[2]["template_ref"],
        confidence=95,
        captured_at=datetime.combine(today, time(16, 2)),
    )
    record_attendance(
        created_staff[3],
        template_ref=created_staff[3]["template_ref"],
        confidence=92,
        captured_at=datetime.combine(yesterday, time(9, 1)),
    )
    record_attendance(
        created_staff[3],
        template_ref=created_staff[3]["template_ref"],
        confidence=92,
        captured_at=datetime.combine(yesterday, time(18, 4)),
    )
    return True
