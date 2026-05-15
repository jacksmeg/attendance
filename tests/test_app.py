from __future__ import annotations

from datetime import date
from io import BytesIO
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import Mock, patch

from attendance_app import create_app
from attendance_app.services.attendance import list_attendance_events
from attendance_app.services.staff import create_staff, get_staff, upsert_fingerprint

TEST_SELFIE_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7Zf1cAAAAASUVORK5CYII="
)


class AttendanceAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = Path("tests") / "_tmp" / uuid.uuid4().hex
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.app = create_app(
            {
                "database_path": self.temp_root / "attendance.db",
                "instance_dir": self.temp_root / "instance",
                "mock_store_path": self.temp_root / "mock_store.json",
                "secret_key": "test-secret",
                "admin_username": "boss",
                "admin_password": "letmein",
                "fingerprint_backend": "mock",
                "debug": False,
            }
        )
        self.client = self.app.test_client()

        with self.app.app_context():
            staff_id = create_staff(
                {
                    "staff_code": "EMP-100",
                    "first_name": "Test",
                    "last_name": "User",
                    "email": "test@example.com",
                    "phone": "+233000000000",
                    "department": "Operations",
                    "role": "Coordinator",
                    "access_role": "Staff",
                    "portal_password": "Test@1234",
                    "portal_pin": "4321",
                    "shift_start": "09:00",
                    "shift_end": "17:00",
                    "grace_minutes": 10,
                    "is_active": True,
                }
            )
            upsert_fingerprint(
                staff_id=staff_id,
                adapter="mock",
                template_ref="MOCK-EMP-100-01",
                quality_score=100,
            )
            self.staff = get_staff(staff_id)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_kiosk_scan_auto_toggles_between_check_in_and_check_out(self) -> None:
        response = self.client.post(
            "/kiosk/scan",
            data={"mock_template_ref": self.staff["template_ref"]},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            rows = list_attendance_events(
                date_from=date.today().isoformat(),
                date_to=date.today().isoformat(),
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["event_type"], "check_in")

        self.client.post(
            "/kiosk/scan",
            data={"mock_template_ref": self.staff["template_ref"]},
            follow_redirects=True,
        )

        with self.app.app_context():
            rows = list_attendance_events(
                date_from=date.today().isoformat(),
                date_to=date.today().isoformat(),
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["event_type"], "check_out")
            self.assertEqual(rows[1]["event_type"], "check_in")

    def test_admin_login_and_csv_export(self) -> None:
        locked_response = self.client.get("/admin/dashboard")
        self.assertEqual(locked_response.status_code, 302)

        login_response = self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Attendance Overview", login_response.data)

        self.client.post(
            "/kiosk/scan",
            data={"mock_template_ref": self.staff["template_ref"]},
            follow_redirects=True,
        )

        csv_response = self.client.get("/admin/attendance/export.csv")
        self.assertEqual(csv_response.status_code, 200)
        payload = csv_response.get_data(as_text=True)
        self.assertIn("Staff Code", payload)
        self.assertIn("EMP-100", payload)

    def test_enroll_page_renders_for_admin(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get(f"/admin/staff/{self.staff['id']}/enroll")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Fingerprint Enrollment", response.data)
        self.assertIn(b"Start Enrollment", response.data)

    def test_kiosk_scan_blocks_morphosmart_backend_when_no_real_enrollments_exist(self) -> None:
        self.app.config["APP_SETTINGS"].fingerprint_backend = "morphosmart"
        fake_provider = Mock()
        fake_provider.name = "morphosmart"
        fake_provider.identify.side_effect = AssertionError("identify should not be called")

        with patch("attendance_app.views.build_provider", return_value=fake_provider):
            response = self.client.post("/kiosk/scan", data={}, follow_redirects=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No real MorphoSmart fingerprints are enrolled", response.data)
        fake_provider.identify.assert_not_called()

    def test_settings_save_updates_new_staff_defaults(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        save_response = self.client.post(
            "/admin/settings",
            data={
                "organization_name": "El Staff Hub",
                "default_shift_start": "08:30",
                "default_shift_end": "16:30",
                "default_grace_minutes": "18",
                "report_default_range_days": "14",
                "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"],
            },
            follow_redirects=True,
        )
        self.assertEqual(save_response.status_code, 200)
        self.assertIn(b"Attendance settings saved successfully", save_response.data)

        form_response = self.client.get("/admin/staff/new")
        self.assertEqual(form_response.status_code, 200)
        self.assertIn(b'value="08:30"', form_response.data)
        self.assertIn(b'value="16:30"', form_response.data)
        self.assertIn(b'value="18"', form_response.data)

    def test_settings_can_enable_allowed_work_location(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            "/admin/settings",
            data={
                "form_name": "attendance_settings",
                "organization_name": "Geo Locked Attendance",
                "default_shift_start": "09:00",
                "default_shift_end": "17:00",
                "default_grace_minutes": "15",
                "report_default_range_days": "30",
                "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "location_enforcement_enabled": "on",
                "allowed_location_name": "Head Office",
                "allowed_location_latitude": "5.60372",
                "allowed_location_longitude": "-0.18696",
                "allowed_location_radius_meters": "180",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Attendance settings saved successfully", response.data)
        self.assertIn(b"Head Office", response.data)

        with self.app.app_context():
            from attendance_app.services.settings import get_app_settings

            settings = get_app_settings(default_app_name="fallback")
            self.assertTrue(settings["location_enforcement_enabled"])
            self.assertEqual(settings["allowed_location_name"], "Head Office")
            self.assertAlmostEqual(settings["allowed_location_latitude"], 5.60372)
            self.assertAlmostEqual(settings["allowed_location_longitude"], -0.18696)
            self.assertEqual(settings["allowed_location_radius_meters"], 180)

    def test_settings_branding_name_and_logo_reflect_across_system(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            "/admin/settings",
            data={
                "form_name": "attendance_settings",
                "organization_name": "Bless Mum Attendance",
                "default_shift_start": "09:00",
                "default_shift_end": "17:00",
                "default_grace_minutes": "15",
                "report_default_range_days": "30",
                "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "system_logo": (BytesIO(b"logo-bytes"), "system-logo.png", "image/png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Bless Mum Attendance", response.data)

        dashboard_response = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"Bless Mum Attendance", dashboard_response.data)

        kiosk_response = self.client.get("/kiosk")
        self.assertEqual(kiosk_response.status_code, 200)
        self.assertIn(b"Bless Mum Attendance", kiosk_response.data)

        with self.app.app_context():
            from attendance_app.services.settings import get_app_settings

            settings = get_app_settings(default_app_name="fallback")
            logo_filename = settings["system_logo_filename"]

        self.assertTrue(logo_filename)

        admin_login_response = self.client.get("/admin/login")
        self.assertEqual(admin_login_response.status_code, 200)
        self.assertIn(b"Bless Mum Attendance", admin_login_response.data)
        self.assertIn(logo_filename.encode(), admin_login_response.data)

        staff_login_response = self.client.get("/staff/login")
        self.assertEqual(staff_login_response.status_code, 200)
        self.assertIn(b"Bless Mum Attendance", staff_login_response.data)
        self.assertIn(logo_filename.encode(), staff_login_response.data)

        logo_response = self.client.get(f"/media/system/{logo_filename}")
        self.assertEqual(logo_response.status_code, 200)
        self.assertEqual(logo_response.data, b"logo-bytes")
        logo_response.close()

    def test_admin_password_can_be_changed_from_settings(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            "/admin/settings",
            data={
                "form_name": "admin_password",
                "current_password": "letmein",
                "new_password": "NewAdmin@123",
                "confirm_password": "NewAdmin@123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin password changed successfully.", response.data)

        self.client.get("/logout", follow_redirects=True)

        old_login = self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid administrator credentials.", old_login.data)

        new_login = self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "NewAdmin@123"},
            follow_redirects=True,
        )
        self.assertEqual(new_login.status_code, 200)
        self.assertIn(b"Administrator session started.", new_login.data)

    def test_users_roles_page_updates_staff_access_role(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            "/admin/users-roles",
            data={
                "staff_id": str(self.staff["id"]),
                "access_role": "HR/Admin",
                "is_active": "on",
                "search": "",
                "department": "",
                "active_only": "0",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User role updated successfully.", response.data)

        with self.app.app_context():
            refreshed_staff = get_staff(self.staff["id"])
            self.assertEqual(refreshed_staff["access_role"], "HR/Admin")

    def test_staff_photo_upload_shows_in_kiosk_match_result(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            f"/admin/staff/{self.staff['id']}/edit",
            data={
                "staff_code": self.staff["staff_code"],
                "first_name": self.staff["first_name"],
                "last_name": self.staff["last_name"],
                "email": self.staff["email"],
                "phone": self.staff["phone"],
                "department": self.staff["department"],
                "role": self.staff["role"],
                "access_role": self.staff["access_role"],
                "shift_start": self.staff["shift_start"],
                "shift_end": self.staff["shift_end"],
                "grace_minutes": str(self.staff["grace_minutes"]),
                "is_active": "on",
                "allow_mobile_clock": "on",
                "allow_pin_clock": "on",
                "allow_qr_clock": "on",
                "staff_photo": (BytesIO(b"fake-image-bytes"), "test-user.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            refreshed_staff = get_staff(self.staff["id"])
            self.assertTrue(refreshed_staff["photo_filename"])
            photo_filename = refreshed_staff["photo_filename"]

        photo_response = self.client.get(f"/media/staff/{photo_filename}")
        self.assertEqual(photo_response.status_code, 200)
        self.assertEqual(photo_response.data, b"fake-image-bytes")
        photo_response.close()

        scan_response = self.client.post(
            "/kiosk/scan",
            data={"mock_template_ref": self.staff["template_ref"]},
            follow_redirects=True,
        )
        self.assertEqual(scan_response.status_code, 200)
        self.assertIn(photo_filename.encode("utf-8"), scan_response.data)

    def test_staff_form_saves_ghana_card_fields(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            f"/admin/staff/{self.staff['id']}/edit",
            data={
                "staff_code": self.staff["staff_code"],
                "first_name": self.staff["first_name"],
                "last_name": self.staff["last_name"],
                "email": self.staff["email"],
                "phone": self.staff["phone"],
                "ghana_card_number": "GHA-123456789-1",
                "nationality": "Ghanaian",
                "sex": "Male",
                "date_of_birth": "1995-01-20",
                "place_of_birth": "Accra",
                "residential_address": "Dansoman, Accra",
                "digital_address": "GA-123-4567",
                "department": self.staff["department"],
                "role": self.staff["role"],
                "access_role": self.staff["access_role"],
                "shift_start": self.staff["shift_start"],
                "shift_end": self.staff["shift_end"],
                "grace_minutes": str(self.staff["grace_minutes"]),
                "is_active": "on",
                "allow_mobile_clock": "on",
                "allow_pin_clock": "on",
                "allow_qr_clock": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            refreshed_staff = get_staff(self.staff["id"])
            self.assertEqual(refreshed_staff["ghana_card_number"], "GHA-123456789-1")
            self.assertEqual(refreshed_staff["nationality"], "Ghanaian")
            self.assertEqual(refreshed_staff["digital_address"], "GA-123-4567")

    def test_ghana_card_verification_page_renders(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get("/admin/ghana-card-verification")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ghana Card Verification", response.data)
        self.assertIn(b"Verify by Fingerprint", response.data)

    def test_ghana_card_verification_scan_marks_record_and_opens_card(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        self.client.post(
            f"/admin/staff/{self.staff['id']}/edit",
            data={
                "staff_code": self.staff["staff_code"],
                "first_name": self.staff["first_name"],
                "last_name": self.staff["last_name"],
                "email": self.staff["email"],
                "phone": self.staff["phone"],
                "ghana_card_number": "GHA-123456789-1",
                "nationality": "Ghanaian",
                "sex": "Male",
                "date_of_birth": "1995-01-20",
                "place_of_birth": "Accra",
                "residential_address": "Dansoman, Accra",
                "digital_address": "GA-123-4567",
                "department": self.staff["department"],
                "role": self.staff["role"],
                "access_role": self.staff["access_role"],
                "shift_start": self.staff["shift_start"],
                "shift_end": self.staff["shift_end"],
                "grace_minutes": str(self.staff["grace_minutes"]),
                "is_active": "on",
                "allow_mobile_clock": "on",
                "allow_pin_clock": "on",
                "allow_qr_clock": "on",
            },
            follow_redirects=True,
        )

        response = self.client.post(
            "/admin/ghana-card-verification/scan",
            data={"mock_template_ref": self.staff["template_ref"]},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Internal Ghana Card Verification Record", response.data)
        self.assertIn(b"GHA-123456789-1", response.data)

        with self.app.app_context():
            refreshed_staff = get_staff(self.staff["id"])
        self.assertTrue(refreshed_staff["ghana_card_verified_at"])
        self.assertEqual(refreshed_staff["ghana_card_verified_by"], "boss")

    def test_online_mode_redirects_fingerprint_enrollment_to_qr_page(self) -> None:
        self.app.config["APP_SETTINGS"].fingerprint_backend = "disabled"
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get(
            f"/admin/staff/{self.staff['id']}/enroll",
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Fingerprint enrollment is disabled in the online deployment.",
            response.data,
        )
        self.assertIn(b"Staff QR Access", response.data)

    def test_online_mode_pages_show_remote_access_copy(self) -> None:
        self.app.config["APP_SETTINGS"].fingerprint_backend = "disabled"
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        staff_response = self.client.get("/admin/staff")
        self.assertEqual(staff_response.status_code, 200)
        self.assertIn(b"Online mode is active.", staff_response.data)

        kiosk_response = self.client.get("/kiosk")
        self.assertEqual(kiosk_response.status_code, 200)
        self.assertIn(b"The online system is live for QR, PIN/password, and staff mobile access.", kiosk_response.data)

    def test_reports_page_and_export_render_for_admin(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.client.post(
            "/kiosk/scan",
            data={"mock_template_ref": self.staff["template_ref"]},
            follow_redirects=True,
        )

        response = self.client.get("/admin/reports")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Attendance Reports", response.data)
        self.assertIn(b"Daily Summary", response.data)

        export_response = self.client.get("/admin/reports/export.csv")
        self.assertEqual(export_response.status_code, 200)
        payload = export_response.get_data(as_text=True)
        self.assertIn("Daily Summary", payload)
        self.assertIn("Department Summary", payload)

    def test_staff_login_mobile_clock_flow_with_breaks_and_gps(self) -> None:
        login_response = self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321", "selfie_data": TEST_SELFIE_DATA_URL},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"attendance for today", login_response.data)

        for payload in (
            {
                "action": "check_in",
                "latitude": "5.60372",
                "longitude": "-0.18696",
                "gps_accuracy": "12.5",
            },
            {"action": "break_start"},
            {"action": "break_end"},
            {"action": "check_out"},
        ):
            response = self.client.post("/staff/clock", data=payload, follow_redirects=True)
            self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            rows = list_attendance_events(
                date_from=date.today().isoformat(),
                date_to=date.today().isoformat(),
            )
            self.assertEqual(
                [row["event_type"] for row in rows],
                ["check_out", "break_end", "break_start", "check_in"],
            )
            check_in_row = rows[-1]
            self.assertEqual(check_in_row["method"], "mobile_gps")
            self.assertAlmostEqual(check_in_row["latitude"], 5.60372)
            self.assertAlmostEqual(check_in_row["longitude"], -0.18696)

    def test_staff_mobile_clock_blocks_outside_allowed_location(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.client.post(
            "/admin/settings",
            data={
                "form_name": "attendance_settings",
                "organization_name": "Geo Locked Attendance",
                "default_shift_start": "09:00",
                "default_shift_end": "17:00",
                "default_grace_minutes": "15",
                "report_default_range_days": "30",
                "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "location_enforcement_enabled": "on",
                "allowed_location_name": "Head Office",
                "allowed_location_latitude": "5.60372",
                "allowed_location_longitude": "-0.18696",
                "allowed_location_radius_meters": "150",
            },
            follow_redirects=True,
        )
        self.client.get("/logout", follow_redirects=True)

        login_response = self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321", "selfie_data": TEST_SELFIE_DATA_URL},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)

        outside_response = self.client.post(
            "/staff/clock",
            data={
                "action": "check_in",
                "latitude": "5.65000",
                "longitude": "-0.24000",
                "gps_accuracy": "10",
            },
            follow_redirects=True,
        )
        self.assertEqual(outside_response.status_code, 200)
        self.assertIn(b"outside the allowed work location", outside_response.data)

        with self.app.app_context():
            rows = list_attendance_events(
                date_from=date.today().isoformat(),
                date_to=date.today().isoformat(),
            )
            self.assertEqual(rows, [])

    def test_staff_mobile_clock_requires_gps_when_location_restricted(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.client.post(
            "/admin/settings",
            data={
                "form_name": "attendance_settings",
                "organization_name": "Geo Locked Attendance",
                "default_shift_start": "09:00",
                "default_shift_end": "17:00",
                "default_grace_minutes": "15",
                "report_default_range_days": "30",
                "working_days": ["Mon", "Tue", "Wed", "Thu", "Fri"],
                "location_enforcement_enabled": "on",
                "allowed_location_name": "Head Office",
                "allowed_location_latitude": "5.60372",
                "allowed_location_longitude": "-0.18696",
                "allowed_location_radius_meters": "150",
            },
            follow_redirects=True,
        )
        self.client.get("/logout", follow_redirects=True)

        self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321", "selfie_data": TEST_SELFIE_DATA_URL},
            follow_redirects=True,
        )

        response = self.client.post(
            "/staff/clock",
            data={"action": "check_in"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Location access is required", response.data)

    def test_staff_login_accepts_email_address(self) -> None:
        login_response = self.client.post(
            "/staff/login",
            data={
                "staff_identifier": "test@example.com",
                "password": "Test@1234",
                "selfie_data": TEST_SELFIE_DATA_URL,
            },
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"attendance for today", login_response.data)

    def test_staff_login_requires_selfie_and_creates_audit_record(self) -> None:
        missing_selfie_response = self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321"},
            follow_redirects=True,
        )
        self.assertEqual(missing_selfie_response.status_code, 200)
        self.assertIn(b"Capture a selfie before signing in.", missing_selfie_response.data)

        login_response = self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321", "selfie_data": TEST_SELFIE_DATA_URL},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"attendance for today", login_response.data)

        self.client.get("/logout", follow_redirects=True)
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        audit_response = self.client.get("/admin/audit-logs")
        self.assertEqual(audit_response.status_code, 200)
        self.assertIn(b"Staff login selfie captured", audit_response.data)
        self.assertIn(b"Open image", audit_response.data)

    def test_kiosk_quick_access_supports_pin_and_qr(self) -> None:
        pin_response = self.client.post(
            "/kiosk/quick-access",
            data={"staff_code": "EMP-100", "secret": "4321", "secret_method": "pin"},
            follow_redirects=True,
        )
        self.assertEqual(pin_response.status_code, 200)

        qr_response = self.client.post(
            "/kiosk/quick-access",
            data={"qr_token": self.staff["qr_token"]},
            follow_redirects=True,
        )
        self.assertEqual(qr_response.status_code, 200)

        with self.app.app_context():
            rows = list_attendance_events(
                date_from=date.today().isoformat(),
                date_to=date.today().isoformat(),
            )
            methods = [row["method"] for row in rows]
            self.assertIn("pin_kiosk", methods)
            self.assertIn("qr_kiosk", methods)

    def test_department_manager_is_scoped_and_cannot_open_settings(self) -> None:
        with self.app.app_context():
            manager_id = create_staff(
                {
                    "staff_code": "MGR-200",
                    "first_name": "Mina",
                    "last_name": "Manager",
                    "email": "manager@example.com",
                    "department": "Operations",
                    "role": "Operations Manager",
                    "access_role": "Department Manager",
                    "portal_password": "Manager@123",
                    "portal_pin": "2200",
                    "shift_start": "08:00",
                    "shift_end": "17:00",
                    "grace_minutes": 10,
                    "is_active": True,
                }
            )
            manager = get_staff(manager_id)

        login_response = self.client.post(
            "/staff/login",
            data={
                "staff_code": manager["staff_code"],
                "password": "Manager@123",
                "selfie_data": TEST_SELFIE_DATA_URL,
            },
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Attendance Overview", login_response.data)

        qr_response = self.client.get(f"/admin/staff/{self.staff['id']}/qr")
        self.assertEqual(qr_response.status_code, 200)
        self.assertIn(b"Staff QR Access", qr_response.data)

        settings_response = self.client.get("/admin/settings", follow_redirects=True)
        self.assertEqual(settings_response.status_code, 200)
        self.assertIn(b"You are not authorized", settings_response.data)
        self.assertIn(b"attendance for today", settings_response.data)


if __name__ == "__main__":
    unittest.main()
