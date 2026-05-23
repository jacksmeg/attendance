from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import Mock, patch

from attendance_app import create_app
from attendance_app.db import init_db
from attendance_app.services.attendance import get_staff_today_status, list_attendance_events, record_attendance
from attendance_app.services.settings import save_admin_credentials_for_database, save_app_settings
from attendance_app.services.settings import get_admin_security_for_database
from attendance_app.services.shifts import get_shift, list_shifts
from attendance_app.services.staff import count_active_staff, create_staff, get_staff, get_staff_by_code, upsert_fingerprint
from attendance_app.services.tenancy import (
    get_current_organization,
    get_organization_by_slug,
    list_organization_license_events,
    provision_organization,
)

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
                    "date_of_birth": "1990-01-02",
                    "department": "Operations",
                    "role": "Coordinator",
                    "access_role": "Staff",
                    "portal_password": "Test@1234",
                    "portal_pin": "4321",
                    "shift_start": "09:00",
                    "shift_end": "17:00",
                    "grace_minutes": 10,
                    "base_salary": 3200,
                    "overtime_hourly_rate": 18,
                    "tax_deduction": 120,
                    "provident_fund": 75,
                    "health_insurance": 40,
                    "other_deduction": 15,
                    "payment_method": "Bank Transfer",
                    "bank_name": "JHIMS Bank",
                    "account_name": "Test User",
                    "account_number": "0011223344",
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
        self.assertNotIn(b"Quick Actions", login_response.data)
        self.assertNotIn(b"Leave Summary", login_response.data)

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

    def test_admin_payroll_page_processes_status_and_exports_csv(self) -> None:
        payroll_month = date.today().strftime("%Y-%m")
        check_in_at = datetime.now().replace(day=1, hour=9, minute=0, second=0, microsecond=0)
        check_out_at = check_in_at.replace(hour=18)
        with self.app.app_context():
            fresh_staff = get_staff(self.staff["id"])
            assert fresh_staff is not None
            record_attendance(
                fresh_staff,
                template_ref=fresh_staff["template_ref"],
                confidence=100,
                method="manual",
                device_name="web",
                event_type="check_in",
                captured_at=check_in_at,
            )
            record_attendance(
                fresh_staff,
                template_ref=fresh_staff["template_ref"],
                confidence=100,
                method="manual",
                device_name="web",
                event_type="check_out",
                captured_at=check_out_at,
            )

        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        payroll_response = self.client.get(f"/admin/payroll?payroll_month={payroll_month}")
        self.assertEqual(payroll_response.status_code, 200)
        self.assertIn(b"Monthly Payroll", payroll_response.data)
        self.assertIn(b"EMP-100", payroll_response.data)
        self.assertIn(b"Pending", payroll_response.data)
        self.assertIn(b"Bank Transfer", payroll_response.data)

        process_response = self.client.post(
            f"/admin/payroll?payroll_month={payroll_month}",
            data={
                "action": "set_status",
                "staff_id": str(self.staff["id"]),
                "next_status": "Processed",
            },
            follow_redirects=True,
        )
        self.assertEqual(process_response.status_code, 200)
        self.assertIn(b"Payroll status updated successfully.", process_response.data)
        self.assertIn(b"Processed", process_response.data)

        export_response = self.client.get(f"/admin/payroll/export.csv?payroll_month={payroll_month}")
        self.assertEqual(export_response.status_code, 200)
        export_payload = export_response.get_data(as_text=True)
        self.assertIn("EMP-100", export_payload)
        self.assertIn("Processed", export_payload)

        summary_response = self.client.get(
            f"/admin/payroll/summary?payroll_month={payroll_month}"
        )
        self.assertEqual(summary_response.status_code, 200)
        self.assertIn(b"Payroll Summary", summary_response.data)
        self.assertIn(b"Top Net Pay", summary_response.data)

        payslip_response = self.client.get(
            f"/admin/payroll/{self.staff['id']}/payslip?payroll_month={payroll_month}"
        )
        self.assertEqual(payslip_response.status_code, 200)
        self.assertIn(b"Payslip", payslip_response.data)
        self.assertIn(b"Test User", payslip_response.data)
        self.assertIn(b"Print Payslip", payslip_response.data)

    def test_admin_notifications_and_audit_groups_render_live_data(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        notifications_response = self.client.get("/admin/notifications")
        self.assertEqual(notifications_response.status_code, 200)
        self.assertIn(b"Institution administrator signed in", notifications_response.data)
        self.assertIn(b"Security", notifications_response.data)

        mark_all_response = self.client.post(
            "/admin/notifications",
            data={"action": "mark_all_read"},
            follow_redirects=True,
        )
        self.assertEqual(mark_all_response.status_code, 200)
        self.assertIn(b"marked as read", mark_all_response.data)

        audit_response = self.client.get("/admin/audit-logs?group=users")
        self.assertEqual(audit_response.status_code, 200)
        self.assertIn(b"User Activity", audit_response.data)
        self.assertIn(b"Admin Login", audit_response.data)
        self.assertIn(b"Institution administrator signed in successfully.", audit_response.data)

    def test_platform_super_admin_can_provision_institution_from_web_ui(self) -> None:
        shared_host = "attendance.jhimssoftware.com"
        login_response = self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Organizations", login_response.data)

        create_response = self.client.post(
            "/platform/organizations",
            data={
                "action": "create",
                "display_name": "Mercy Hospital",
                "slug": "mercy-hospital",
                "hostnames": "attendance.mercy.example",
                "admin_username": "mercyadmin",
                "admin_password": "Mercy@1234",
                "confirm_admin_password": "Mercy@1234",
                "is_default": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertIn(b"Mercy Hospital was provisioned successfully", create_response.data)
        self.assertIn(b"attendance.mercy.example", create_response.data)
        self.assertIn(b"Copy Admin Link", create_response.data)
        self.assertIn(b"Copy Staff Link", create_response.data)
        self.assertIn(b"/portal/mercy-hospital/staff/login", create_response.data)
        self.assertIn(b"mercyadmin", create_response.data)
        self.assertIn(b"/portal/mercy-hospital/admin/login", create_response.data)

        with self.app.app_context():
            shared_front = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="shared-front",
                display_name="Shared Front Office",
                hostnames=[shared_host],
            )
            init_db(shared_front.database_path)

        with self.app.test_request_context("/", base_url=f"https://{shared_host}"):
            save_app_settings(
                {"organization_name": "Shared Front Office"},
                default_app_name=self.app.config["APP_SETTINGS"].app_name,
            )

        self.client.get("/logout", follow_redirects=True)

        institution_login = self.client.get(
            "/portal/mercy-hospital/admin/login",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(institution_login.status_code, 200)
        self.assertIn(b"Mercy Hospital", institution_login.data)
        self.assertNotIn(b"Shared Front Office", institution_login.data)
        self.assertIn(self.app.config["APP_SETTINGS"].app_name.encode(), institution_login.data)

        institution_login = self.client.post(
            "/admin/login",
            data={"username": "mercyadmin", "password": "Mercy@1234"},
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(institution_login.status_code, 200)
        self.assertIn(b"Attendance Overview", institution_login.data)
        self.assertIn(b"Mercy Hospital", institution_login.data)
        self.assertIn(self.app.config["APP_SETTINGS"].app_name.encode(), institution_login.data)

        branded_staff_login = self.client.get(
            "/portal/mercy-hospital/staff/login",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(branded_staff_login.status_code, 200)
        self.assertIn(b"Mercy Hospital", branded_staff_login.data)
        self.assertIn(b"/pwa/manifest.webmanifest?org=mercy-hospital", branded_staff_login.data)
        self.assertIn(b"/pwa/icon-180.png?org=mercy-hospital", branded_staff_login.data)

    def test_cli_create_organization_sets_institution_admin_credentials(self) -> None:
        runner = self.app.test_cli_runner()
        result = runner.invoke(
            args=[
                "create-organization",
                "--slug",
                "river-clinic",
                "--name",
                "River Clinic",
                "--hostname",
                "attendance.river.example",
                "--admin-username",
                "riveradmin",
                "--admin-password",
                "River@1234",
            ]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Organization created: River Clinic (river-clinic)", result.output)
        self.assertIn("Admin username: riveradmin", result.output)

        with self.app.app_context():
            organization = get_organization_by_slug(
                self.app.config["APP_SETTINGS"],
                "river-clinic",
            )
            self.assertIsNotNone(organization)
            admin_security = get_admin_security_for_database(
                organization.database_path,
                default_username=self.app.config["APP_SETTINGS"].admin_username,
            )
            self.assertEqual(admin_security["admin_username"], "riveradmin")

        login_response = self.client.get(
            "/portal/river-clinic/admin/login",
            base_url="https://attendance.jhimssoftware.com",
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"River Clinic", login_response.data)

        login_response = self.client.post(
            "/admin/login",
            data={"username": "riveradmin", "password": "River@1234"},
            base_url="https://attendance.jhimssoftware.com",
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Attendance Overview", login_response.data)

    def test_portal_admin_login_self_heals_organization_without_initialized_schema(self) -> None:
        with self.app.app_context():
            organization = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="rescue-clinic",
                display_name="Rescue Clinic",
            )
            save_admin_credentials_for_database(
                organization.database_path,
                username="rescueadmin",
                password="Rescue@1234",
            )

        login_page = self.client.get(
            "/portal/rescue-clinic/admin/login",
            base_url="https://attendance.jhimssoftware.com",
            follow_redirects=True,
        )
        self.assertEqual(login_page.status_code, 200)
        self.assertIn(b"Rescue Clinic", login_page.data)

        login_response = self.client.post(
            "/admin/login",
            data={"username": "rescueadmin", "password": "Rescue@1234"},
            base_url="https://attendance.jhimssoftware.com",
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Attendance Overview", login_response.data)

    def test_platform_super_admin_can_store_subscription_and_billing_fields(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        expiry_date = (date.today() + timedelta(days=30)).isoformat()
        renewal_date = (date.today() + timedelta(days=20)).isoformat()
        payment_date = date.today().isoformat()

        response = self.client.post(
            "/platform/organizations",
            data={
                "action": "create",
                "display_name": "Cedar Clinic",
                "slug": "cedar-clinic",
                "hostnames": "attendance.cedar.example",
                "admin_username": "cedaradmin",
                "plan_name": "Enterprise Care",
                "license_status": "trial",
                "expires_on": expiry_date,
                "billing_contact_name": "Ama Owusu",
                "billing_email": "billing@cedar.example",
                "billing_phone": "+233200000001",
                "billing_cycle": "yearly",
                "subscription_amount": "2499.99",
                "renewal_due_on": renewal_date,
                "last_payment_on": payment_date,
                "license_notes": "Annual hospital deployment with onboarding support.",
                "grace_days": "5",
                "admin_password": "Cedar@1234",
                "confirm_admin_password": "Cedar@1234",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enterprise Care", response.data)
        self.assertIn(b"billing@cedar.example", response.data)
        self.assertIn(b"Annual hospital deployment with onboarding support.", response.data)

        with self.app.app_context():
            organization = get_organization_by_slug(self.app.config["APP_SETTINGS"], "cedar-clinic")

        self.assertIsNotNone(organization)
        assert organization is not None
        self.assertEqual(organization.plan_name, "Enterprise Care")
        self.assertEqual(organization.license_status, "trial")
        self.assertEqual(organization.expires_on, expiry_date)
        self.assertEqual(organization.billing_contact_name, "Ama Owusu")
        self.assertEqual(organization.billing_email, "billing@cedar.example")
        self.assertEqual(organization.billing_cycle, "yearly")
        self.assertAlmostEqual(organization.subscription_amount, 2499.99)
        self.assertEqual(organization.renewal_due_on, renewal_date)
        self.assertEqual(organization.last_payment_on, payment_date)
        self.assertEqual(organization.license_notes, "Annual hospital deployment with onboarding support.")
        self.assertEqual(organization.grace_days, 5)

    def test_platform_license_form_updates_without_overwriting_access_fields(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        self.client.post(
            "/platform/organizations",
            data={
                "action": "create",
                "display_name": "North Ridge Clinic",
                "slug": "north-ridge-clinic",
                "hostnames": "attendance.northridge.example",
                "admin_username": "ridgeadmin",
                "plan_name": "Starter",
                "license_status": "trial",
                "expires_on": (date.today() + timedelta(days=14)).isoformat(),
                "billing_cycle": "monthly",
                "subscription_amount": "199.00",
                "admin_password": "North@1234",
                "confirm_admin_password": "North@1234",
            },
            follow_redirects=True,
        )

        renewal_date = (date.today() + timedelta(days=45)).isoformat()
        response = self.client.post(
            "/platform/organizations",
            data={
                "action": "update",
                "organization_slug": "north-ridge-clinic",
                "plan_name": "Enterprise",
                "license_status": "active",
                "expires_on": (date.today() + timedelta(days=60)).isoformat(),
                "billing_cycle": "quarterly",
                "subscription_amount": "899.00",
                "renewal_due_on": renewal_date,
                "grace_days": "7",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"North Ridge Clinic", response.data)
        self.assertIn(b"Enterprise", response.data)

        with self.app.app_context():
            organization = get_organization_by_slug(
                self.app.config["APP_SETTINGS"],
                "north-ridge-clinic",
            )
            self.assertIsNotNone(organization)
            assert organization is not None
            self.assertEqual(organization.display_name, "North Ridge Clinic")
            self.assertEqual(organization.hostnames, ("attendance.northridge.example",))
            self.assertEqual(organization.plan_name, "Enterprise")
            self.assertEqual(organization.license_status, "active")
            self.assertEqual(organization.billing_cycle, "quarterly")
            self.assertAlmostEqual(organization.subscription_amount, 899.0)
            self.assertEqual(organization.renewal_due_on, renewal_date)
            self.assertEqual(organization.grace_days, 7)

    def test_platform_super_admin_can_apply_license_quick_action(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        self.client.post(
            "/platform/organizations",
            data={
                "action": "create",
                "display_name": "Summit Hospital",
                "slug": "summit-hospital",
                "hostnames": "attendance.summit.example",
                "admin_username": "summitadmin",
                "plan_name": "Growth",
                "license_status": "active",
                "expires_on": date.today().isoformat(),
                "billing_cycle": "monthly",
                "subscription_amount": "320.00",
                "admin_password": "Summit@1234",
                "confirm_admin_password": "Summit@1234",
            },
            follow_redirects=True,
        )

        response = self.client.post(
            "/platform/organizations",
            data={
                "action": "license_action",
                "organization_slug": "summit-hospital",
                "license_action": "renew_cycle",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Renew Cycle applied", response.data)

        with self.app.app_context():
            organization = get_organization_by_slug(
                self.app.config["APP_SETTINGS"],
                "summit-hospital",
            )
            events = list_organization_license_events(
                self.app.config["APP_SETTINGS"],
                "summit-hospital",
            )

        assert organization is not None
        self.assertEqual(organization.license_status, "active")
        self.assertTrue(organization.expires_on)
        self.assertEqual(organization.last_payment_on, date.today().isoformat())
        self.assertTrue(events)
        self.assertEqual(events[0]["event_type"], "renew_cycle")

    def test_license_grace_period_allows_temporary_access(self) -> None:
        grace_host = "grace.attendance.local"

        with self.app.app_context():
            provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="grace-clinic",
                display_name="Grace Clinic",
                hostnames=[grace_host],
                plan_name="Starter",
                license_status="active",
                expires_on=(date.today() - timedelta(days=1)).isoformat(),
                billing_email="renewals@grace.example",
                grace_days=3,
            )

        login_response = self.client.get(
            "/staff/login",
            base_url=f"https://{grace_host}",
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Staff Login", login_response.data)
        self.assertNotIn(b"Expired License", login_response.data)

        health_response = self.client.get("/health", base_url=f"https://{grace_host}")
        payload = health_response.get_json()
        self.assertTrue(payload["license"]["access_allowed"])
        self.assertEqual(payload["license"]["state"], "grace")

    def test_platform_organizations_page_creates_automatic_backups(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get("/platform/organizations")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Backups &amp; Restore", response.data)

        backup_dir = self.app.config["APP_SETTINGS"].instance_dir / "backups"
        archives = list(backup_dir.glob("*-automatic-backup-*.zip"))
        self.assertTrue(archives)

    def test_platform_super_admin_can_create_and_download_manual_backup(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.post(
            "/platform/organizations",
            data={
                "action": "create_backup",
                "organization_slug": self.app.config["APP_SETTINGS"].default_organization_slug,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Backup created for", response.data)

        backup_dir = self.app.config["APP_SETTINGS"].instance_dir / "backups"
        archives = sorted(backup_dir.glob("*-manual-backup-*.zip"))
        self.assertTrue(archives)

        download_response = self.client.get(
            f"/platform/organizations/{self.app.config['APP_SETTINGS'].default_organization_slug}/backups/{archives[-1].name}"
        )
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.mimetype, "application/zip")
        download_response.close()

    def test_platform_super_admin_can_restore_institution_from_backup(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        backup_response = self.client.post(
            "/platform/organizations",
            data={
                "action": "create_backup",
                "organization_slug": self.app.config["APP_SETTINGS"].default_organization_slug,
            },
            follow_redirects=True,
        )
        self.assertEqual(backup_response.status_code, 200)

        backup_dir = self.app.config["APP_SETTINGS"].instance_dir / "backups"
        archives = sorted(backup_dir.glob("*-manual-backup-*.zip"))
        self.assertTrue(archives)
        selected_backup = archives[-1].name

        with self.app.app_context():
            create_staff(
                {
                    "staff_code": "EMP-RESTORE",
                    "first_name": "Restore",
                    "last_name": "Target",
                    "email": "restore@example.com",
                    "phone": "+233100000001",
                    "department": "Recovery",
                    "role": "Nurse",
                    "access_role": "Staff",
                    "portal_password": "Restore@123",
                    "portal_pin": "1111",
                    "shift_start": "08:00",
                    "shift_end": "16:00",
                    "grace_minutes": 10,
                    "is_active": True,
                }
            )
            self.assertIsNotNone(get_staff_by_code("EMP-RESTORE"))

        restore_response = self.client.post(
            "/platform/organizations",
            data={
                "action": "restore_backup",
                "organization_slug": self.app.config["APP_SETTINGS"].default_organization_slug,
                "backup_name": selected_backup,
            },
            follow_redirects=True,
        )
        self.assertEqual(restore_response.status_code, 200)
        self.assertIn(b"was restored from", restore_response.data)

        with self.app.app_context():
            self.assertIsNone(get_staff_by_code("EMP-RESTORE"))

    def test_expired_organization_is_redirected_to_license_page(self) -> None:
        expired_host = "expired.attendance.local"
        expired_on = (date.today() - timedelta(days=3)).isoformat()

        with self.app.app_context():
            provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="expired-clinic",
                display_name="Expired Clinic",
                hostnames=[expired_host],
                plan_name="Starter",
                license_status="active",
                expires_on=expired_on,
                billing_contact_name="License Desk",
                billing_email="renewals@expired.example",
                billing_cycle="monthly",
                subscription_amount="199.00",
                license_notes="Renewal required before staff can continue using the portal.",
            )

        blocked_response = self.client.get(
            "/staff/login",
            base_url=f"https://{expired_host}",
            follow_redirects=True,
        )
        self.assertEqual(blocked_response.status_code, 200)
        self.assertIn(b"Expired Clinic", blocked_response.data)
        self.assertIn(b"Expired License", blocked_response.data)
        self.assertIn(b"renewals@expired.example", blocked_response.data)

        health_response = self.client.get("/health", base_url=f"https://{expired_host}")
        self.assertEqual(health_response.status_code, 200)
        payload = health_response.get_json()
        self.assertEqual(payload["license"]["status"], "expired")
        self.assertFalse(payload["license"]["access_allowed"])

    def test_institution_admin_cannot_access_platform_organizations(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get("/platform/organizations", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Platform super admin", response.data)
        self.assertIn(b"Sign in as a platform super admin", response.data)

    def test_platform_super_admin_stays_in_platform_portal(self) -> None:
        self.client.post(
            "/platform/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        dashboard_response = self.client.get("/admin/dashboard", follow_redirects=True)
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"Standalone Platform", dashboard_response.data)
        self.assertIn(b"Platform sessions use the standalone platform control room", dashboard_response.data)
        self.assertNotIn(b"Attendance Overview", dashboard_response.data)

        home_response = self.client.get("/", follow_redirects=False)
        self.assertEqual(home_response.status_code, 302)
        self.assertIn("/platform/organizations", home_response.headers["Location"])

        logout_response = self.client.get("/platform/logout", follow_redirects=True)
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn(b"Platform sign in", logout_response.data)

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
                "allowed_location_address": "Ring Road Central, Accra, Ghana",
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
            self.assertEqual(settings["allowed_location_address"], "Ring Road Central, Accra, Ghana")
            self.assertAlmostEqual(settings["allowed_location_latitude"], 5.60372)
            self.assertAlmostEqual(settings["allowed_location_longitude"], -0.18696)
            self.assertEqual(settings["allowed_location_radius_meters"], 180)

    def test_settings_page_includes_gps_capture_button_for_location(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get("/admin/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Use My Current GPS Location", response.data)

    def test_pwa_endpoints_are_available(self) -> None:
        manifest_response = self.client.get("/pwa/manifest.webmanifest")
        self.assertEqual(manifest_response.status_code, 200)
        manifest = json.loads(manifest_response.get_data(as_text=True))
        self.assertEqual(manifest["display"], "standalone")
        self.assertIn("/pwa/icon-180.png", manifest["icons"][0]["src"])

        service_worker_response = self.client.get("/service-worker.js")
        self.assertEqual(service_worker_response.status_code, 200)
        self.assertIn("CACHE_NAME", service_worker_response.get_data(as_text=True))

        ios_icon_response = self.client.get("/pwa/icon-180.png")
        self.assertEqual(ios_icon_response.status_code, 200)
        self.assertEqual(ios_icon_response.mimetype, "image/png")

        icon_response = self.client.get("/pwa/icon-192.png")
        self.assertEqual(icon_response.status_code, 200)
        self.assertEqual(icon_response.mimetype, "image/png")
        self.assertTrue(icon_response.data.startswith(b"\x89PNG"))

        login_response = self.client.get("/staff/login")
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Add this app to your Home Screen", login_response.data)
        self.assertIn(b"/static/branding/jhims-attendance-system-mark.png", login_response.data)

        offline_response = self.client.get("/pwa/offline")
        self.assertEqual(offline_response.status_code, 200)
        self.assertIn(b"Offline Mode", offline_response.data)

    def test_institution_portal_uses_tenant_specific_pwa_manifest(self) -> None:
        shared_host = "attendance.jhimssoftware.com"

        with self.app.app_context():
            shared_front = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="shared-front",
                display_name="Shared Front Office",
                hostnames=[shared_host],
            )
            init_db(shared_front.database_path)
            provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="mercy-hospital",
                display_name="Mercy Hospital",
                hostnames=["attendance.mercy.example"],
            )
            mercy_org = get_organization_by_slug(self.app.config["APP_SETTINGS"], "mercy-hospital")
            self.assertIsNotNone(mercy_org)
            assert mercy_org is not None
            init_db(mercy_org.database_path)
            save_admin_credentials_for_database(
                mercy_org.database_path,
                username="mercyadmin",
                password="Mercy@1234",
            )

        self.client.get(
            "/portal/mercy-hospital/staff/login",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )

        manifest_response = self.client.get(
            "/pwa/manifest.webmanifest",
            base_url=f"https://{shared_host}",
        )
        self.assertEqual(manifest_response.status_code, 200)
        manifest = json.loads(manifest_response.get_data(as_text=True))
        self.assertEqual(manifest["name"], "Mercy Hospital Staff App")
        self.assertEqual(manifest["id"], "/portal/mercy-hospital/staff/login")
        self.assertEqual(manifest["start_url"], "/portal/mercy-hospital/staff/login")
        self.assertIn("/pwa/icon-180.png?org=mercy-hospital", manifest["icons"][0]["src"])
        self.assertEqual(manifest["shortcuts"][0]["url"], "/portal/mercy-hospital/staff/login")
        self.assertEqual(manifest["shortcuts"][2]["url"], "/portal/mercy-hospital/admin/login")

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
                "is_active": "1",
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
            self.assertTrue(refreshed_staff["is_active"])

    def test_users_roles_page_can_reactivate_staff_account(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        deactivate_response = self.client.post(
            "/admin/users-roles",
            data={
                "staff_id": str(self.staff["id"]),
                "access_role": "Staff",
                "search": "",
                "department": "",
                "active_only": "0",
            },
            follow_redirects=True,
        )
        self.assertEqual(deactivate_response.status_code, 200)

        reactivate_response = self.client.post(
            "/admin/users-roles",
            data={
                "staff_id": str(self.staff["id"]),
                "access_role": "Staff",
                "is_active": "1",
                "search": "",
                "department": "",
                "active_only": "0",
            },
            follow_redirects=True,
        )
        self.assertEqual(reactivate_response.status_code, 200)
        self.assertIn(b"User role updated successfully.", reactivate_response.data)

        with self.app.app_context():
            refreshed_staff = get_staff(self.staff["id"], fingerprint_adapter="mock")
            self.assertTrue(refreshed_staff["is_active"])

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

    def test_online_root_redirects_to_staff_login(self) -> None:
        self.app.config["APP_SETTINGS"].fingerprint_backend = "disabled"
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/staff/login", response.headers["Location"])

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

    def test_reports_page_switches_report_views(self) -> None:
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

        daily_response = self.client.get("/admin/reports?report_kind=daily")
        self.assertEqual(daily_response.status_code, 200)
        self.assertIn(b"Daily Attendance Summary", daily_response.data)
        self.assertIn(b"Checked In", daily_response.data)
        self.assertIn(b"Checked Out", daily_response.data)

        department_response = self.client.get("/admin/reports?report_kind=department")
        self.assertEqual(department_response.status_code, 200)
        self.assertIn(b"Department Performance", department_response.data)
        self.assertIn(b"Total Staff", department_response.data)
        self.assertIn(b"Absent", department_response.data)

    def test_reports_page_filters_staff_by_attendance_group(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        with self.app.app_context():
            late_staff_id = create_staff(
                {
                    "staff_code": "EMP-200",
                    "first_name": "Late",
                    "last_name": "Staff",
                    "email": "late@example.com",
                    "phone": "+233111111111",
                    "date_of_birth": "1991-03-04",
                    "department": "Finance",
                    "role": "Officer",
                    "access_role": "Staff",
                    "portal_password": "Late@1234",
                    "portal_pin": "9876",
                    "shift_start": "09:00",
                    "shift_end": "17:00",
                    "grace_minutes": 10,
                    "is_active": True,
                }
            )
            late_staff = get_staff(late_staff_id)
            today_value = date.today().isoformat()
            record_attendance(
                self.staff,
                template_ref=self.staff["template_ref"],
                confidence=100,
                method="mock",
                device_name="test",
                event_type="check_in",
                captured_at=datetime.fromisoformat(f"{today_value}T09:00:00"),
            )
            record_attendance(
                late_staff,
                template_ref="MANUAL-EMP-200",
                confidence=96,
                method="mobile_gps",
                device_name="test",
                event_type="check_in",
                captured_at=datetime.fromisoformat(f"{today_value}T09:35:00"),
            )

        response = self.client.get("/admin/reports?attendance_group=late")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Attendance Details", response.data)
        self.assertIn(b"EMP-200", response.data)
        self.assertNotIn(b"EMP-100", response.data)

    def test_shift_management_creates_shift_and_assigns_staff(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        create_response = self.client.post(
            "/admin/shift-management",
            data={
                "action": "create_shift",
                "name": "Ward Evening Shift",
                "code": "WEV",
                "shift_start": "16:00",
                "shift_end": "23:00",
                "break_label": "07:00 PM - 07:30 PM",
                "grace_minutes": "10",
                "weekly_off": "Sunday",
                "description": "Evening ward coverage",
                "is_active": "1",
            },
            follow_redirects=True,
        )
        self.assertEqual(create_response.status_code, 200)
        self.assertIn(b"Shift created successfully.", create_response.data)
        self.assertIn(b"Ward Evening Shift", create_response.data)

        with self.app.app_context():
            shift_rows = [row for row in list_shifts() if row["code"] == "WEV"]
            self.assertEqual(len(shift_rows), 1)
            shift_id = int(shift_rows[0]["id"])

        assign_response = self.client.post(
            "/admin/shift-management",
            data={
                "action": "assign_staff",
                "shift_id": str(shift_id),
                "staff_id": str(self.staff["id"]),
            },
            follow_redirects=True,
        )
        self.assertEqual(assign_response.status_code, 200)
        self.assertIn(b"Staff assigned to shift successfully.", assign_response.data)
        self.assertIn(b"EMP-100", assign_response.data)

        with self.app.app_context():
            updated_staff = get_staff(self.staff["id"])
            self.assertEqual(updated_staff["shift_id"], shift_id)
            self.assertEqual(updated_staff["shift_start"], "16:00")
            self.assertEqual(updated_staff["shift_end"], "23:00")

    def test_shift_management_can_toggle_shift_status(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.client.get("/admin/shift-management")

        with self.app.app_context():
            first_shift = list_shifts()[0]
            shift_id = int(first_shift["id"])

        deactivate_response = self.client.post(
            "/admin/shift-management",
            data={
                "action": "toggle_shift",
                "shift_id": str(shift_id),
                "next_state": "0",
            },
            follow_redirects=True,
        )
        self.assertEqual(deactivate_response.status_code, 200)
        self.assertIn(b"Shift deactivated successfully.", deactivate_response.data)

        with self.app.app_context():
            self.assertEqual(int(get_shift(shift_id)["is_active"]), 0)

    def test_staff_login_mobile_clock_flow_with_breaks_and_gps(self) -> None:
        login_response = self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321", "selfie_data": TEST_SELFIE_DATA_URL},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"LOCATION VERIFICATION", login_response.data)

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
            if payload["action"] == "check_in":
                self.assertIn(b"Attendance Recorded!", response.data)
                self.assertIn(b"Checked In for Today", response.data)
            if payload["action"] == "check_out":
                self.assertIn(b"Attendance Complete!", response.data)
                self.assertIn(b"Completed for Today", response.data)

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
                "allowed_location_address": "Ring Road Central, Accra, Ghana",
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
                "allowed_location_address": "Ring Road Central, Accra, Ghana",
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
        self.assertIn(b"LOCATION VERIFICATION", login_response.data)

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
        self.assertIn(b"LOCATION VERIFICATION", login_response.data)

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

    def test_staff_login_shows_recovery_links(self) -> None:
        response = self.client.get("/staff/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Forgot password or PIN?", response.data)

    def test_staff_can_reset_password_with_registered_details(self) -> None:
        response = self.client.post(
            "/staff/recover",
            data={
                "staff_identifier": "EMP-100",
                "date_of_birth": "1990-01-02",
                "phone": "+233000000000",
                "email": "",
                "reset_mode": "password",
                "new_password": "Fresh@1234",
                "confirm_new_password": "Fresh@1234",
                "new_pin": "",
                "confirm_new_pin": "",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"reset successfully", response.data)

        login_response = self.client.post(
            "/staff/login",
            data={
                "staff_identifier": "EMP-100",
                "password": "Fresh@1234",
                "selfie_data": TEST_SELFIE_DATA_URL,
            },
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"LOCATION VERIFICATION", login_response.data)

    def test_staff_can_reset_pin_with_registered_details(self) -> None:
        response = self.client.post(
            "/staff/recover",
            data={
                "staff_identifier": "test@example.com",
                "date_of_birth": "1990-01-02",
                "phone": "",
                "email": "test@example.com",
                "reset_mode": "pin",
                "new_password": "",
                "confirm_new_password": "",
                "new_pin": "6789",
                "confirm_new_pin": "6789",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"reset successfully", response.data)

        login_response = self.client.post(
            "/staff/login",
            data={
                "staff_identifier": "EMP-100",
                "pin": "6789",
                "selfie_data": TEST_SELFIE_DATA_URL,
            },
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"LOCATION VERIFICATION", login_response.data)

    def test_staff_logout_returns_to_staff_login_page(self) -> None:
        login_response = self.client.post(
            "/staff/login",
            data={"staff_code": "EMP-100", "pin": "4321", "selfie_data": TEST_SELFIE_DATA_URL},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)

        logout_response = self.client.get("/logout", follow_redirects=True)
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn(b"Staff Number or Email Address", logout_response.data)
        self.assertIn(b"Session closed.", logout_response.data)

    def test_portal_staff_logout_returns_to_same_institution_login_page(self) -> None:
        shared_host = "attendance.jhimssoftware.com"

        with self.app.app_context():
            shared_front = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="shared-front",
                display_name="Shared Front Office",
                hostnames=[shared_host],
            )
            init_db(shared_front.database_path)
            save_admin_credentials_for_database(
                shared_front.database_path,
                username="sharedadmin",
                password="Shared@1234",
            )
            mercy_org = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="mercy-hospital",
                display_name="Mercy Hospital",
                hostnames=["attendance.mercy.example"],
            )
            init_db(mercy_org.database_path)
            save_admin_credentials_for_database(
                mercy_org.database_path,
                username="mercyadmin",
                password="Mercy@1234",
            )
            with self.app.test_request_context("/", base_url=f"https://{shared_host}"):
                from attendance_app.services.tenancy import set_current_organization

                set_current_organization(mercy_org)
                create_staff(
                    {
                        "staff_code": "MERCY-100",
                        "first_name": "Mercy",
                        "last_name": "User",
                        "email": "mercy@example.com",
                        "phone": "+233000000111",
                        "department": "Nursing",
                        "role": "Nurse",
                        "access_role": "Staff",
                        "portal_password": "Mercy@1234",
                        "portal_pin": "1234",
                        "shift_start": "08:00",
                        "shift_end": "16:00",
                        "grace_minutes": 10,
                        "is_active": True,
                    }
                )

        self.client.get(
            "/portal/mercy-hospital/staff/login",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        login_response = self.client.post(
            "/staff/login",
            data={
                "staff_code": "MERCY-100",
                "pin": "1234",
                "selfie_data": TEST_SELFIE_DATA_URL,
            },
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Mercy Hospital", login_response.data)

        logout_response = self.client.get(
            "/logout",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn(b"Mercy Hospital", logout_response.data)
        self.assertNotIn(b"Shared Front Office", logout_response.data)
        self.assertIn(b"Staff Number or Email Address", logout_response.data)

    def test_admin_logout_returns_to_admin_login_page(self) -> None:
        login_response = self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)

        logout_response = self.client.get("/logout", follow_redirects=True)
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn(b"Administrator Access", logout_response.data)
        self.assertIn(b"Session closed.", logout_response.data)

    def test_portal_admin_logout_returns_to_same_institution_login_page(self) -> None:
        shared_host = "attendance.jhimssoftware.com"

        with self.app.app_context():
            shared_front = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="shared-front",
                display_name="Shared Front Office",
                hostnames=[shared_host],
            )
            init_db(shared_front.database_path)
            save_admin_credentials_for_database(
                shared_front.database_path,
                username="sharedadmin",
                password="Shared@1234",
            )
            mercy_org = provision_organization(
                self.app.config["APP_SETTINGS"],
                slug="mercy-hospital",
                display_name="Mercy Hospital",
                hostnames=["attendance.mercy.example"],
            )
            init_db(mercy_org.database_path)
            save_admin_credentials_for_database(
                mercy_org.database_path,
                username="mercyadmin",
                password="Mercy@1234",
            )

        self.client.get(
            "/portal/mercy-hospital/admin/login",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        login_response = self.client.post(
            "/admin/login",
            data={"username": "mercyadmin", "password": "Mercy@1234"},
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(login_response.status_code, 200)
        self.assertIn(b"Mercy Hospital", login_response.data)

        logout_response = self.client.get(
            "/logout",
            base_url=f"https://{shared_host}",
            follow_redirects=True,
        )
        self.assertEqual(logout_response.status_code, 200)
        self.assertIn(b"Mercy Hospital", logout_response.data)
        self.assertNotIn(b"Shared Front Office", logout_response.data)
        self.assertIn(b"Administrator Access", logout_response.data)

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
        self.assertIn(b"LOCATION VERIFICATION", settings_response.data)

    def test_staff_form_includes_hospital_shift_presets(self) -> None:
        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )

        response = self.client.get("/admin/staff/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hospital Shift Presets", response.data)
        self.assertIn(b"Morning 8AM - 2PM", response.data)
        self.assertIn(b"Night 8PM - 8AM", response.data)

    def test_theme_script_is_loaded_on_staff_login_and_dashboard(self) -> None:
        staff_login_response = self.client.get("/staff/login")
        self.assertEqual(staff_login_response.status_code, 200)
        self.assertIn(b"theme.js", staff_login_response.data)
        self.assertIn(b"root.dataset.theme = theme", staff_login_response.data)

        self.client.post(
            "/admin/login",
            data={"username": "boss", "password": "letmein"},
            follow_redirects=True,
        )
        dashboard_response = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertIn(b"theme.js", dashboard_response.data)

    def test_organization_hostnames_keep_staff_and_branding_separate(self) -> None:
        settings = self.app.config["APP_SETTINGS"]
        beta_host = "beta.attendance.local"

        with self.app.app_context():
            beta_org = provision_organization(
                settings,
                slug="beta-hospital",
                display_name="Beta Hospital",
                hostnames=[beta_host],
            )
            init_db(beta_org.database_path)

        with self.app.test_request_context("/", base_url=f"https://{beta_host}"):
            self.assertEqual(get_current_organization().slug, "beta-hospital")
            save_app_settings({"organization_name": "Beta Hospital"}, default_app_name=settings.app_name)
            beta_staff_id = create_staff(
                {
                    "staff_code": "BETA-200",
                    "first_name": "Beta",
                    "last_name": "Doctor",
                    "email": "beta@example.com",
                    "phone": "+233111111111",
                    "department": "Emergency",
                    "role": "Doctor",
                    "access_role": "Staff",
                    "portal_password": "Beta@123",
                    "portal_pin": "2200",
                    "shift_start": "08:00",
                    "shift_end": "16:00",
                    "grace_minutes": 10,
                    "is_active": True,
                }
            )
            self.assertEqual(count_active_staff(), 1)
            self.assertIsNotNone(get_staff(beta_staff_id))
            self.assertIsNotNone(get_staff_by_code("BETA-200"))
            self.assertIsNone(get_staff_by_code(self.staff["staff_code"]))

        with self.app.app_context():
            self.assertEqual(get_current_organization().slug, settings.default_organization_slug)
            self.assertEqual(count_active_staff(), 1)
            self.assertIsNotNone(get_staff_by_code(self.staff["staff_code"]))
            self.assertIsNone(get_staff_by_code("BETA-200"))

        beta_login_response = self.client.get("/staff/login", base_url=f"https://{beta_host}")
        self.assertEqual(beta_login_response.status_code, 200)
        self.assertIn(b"Beta Hospital", beta_login_response.data)

        default_login_response = self.client.get("/staff/login")
        self.assertEqual(default_login_response.status_code, 200)
        self.assertNotIn(b"Beta Hospital", default_login_response.data)

    def test_overnight_shift_auto_resolves_check_out_next_morning(self) -> None:
        with self.app.app_context():
            night_staff_id = create_staff(
                {
                    "staff_code": "NIGHT-300",
                    "first_name": "Night",
                    "last_name": "Nurse",
                    "email": "night@example.com",
                    "department": "Ward",
                    "role": "Nurse",
                    "access_role": "Staff",
                    "portal_password": "Night@123",
                    "portal_pin": "8300",
                    "shift_start": "20:00",
                    "shift_end": "08:00",
                    "grace_minutes": 15,
                    "is_active": True,
                }
            )
            night_staff = get_staff(night_staff_id)

            first_event = record_attendance(
                staff=night_staff,
                template_ref=night_staff["staff_code"],
                confidence=None,
                method="test",
                captured_at=datetime(2026, 5, 16, 20, 5),
            )
            active_status = get_staff_today_status(
                night_staff_id,
                reference_dt=datetime(2026, 5, 17, 7, 30),
            )
            second_event = record_attendance(
                staff=night_staff,
                template_ref=night_staff["staff_code"],
                confidence=None,
                method="test",
                captured_at=datetime(2026, 5, 17, 8, 2),
            )

            self.assertEqual(first_event["event_type"], "check_in")
            self.assertEqual(active_status["attendance_date"], "2026-05-16")
            self.assertEqual(active_status["current_state"], "Working")
            self.assertEqual(second_event["event_type"], "check_out")
            self.assertEqual(second_event["status_label"], "Completed shift")

            rows = list_attendance_events(date_from="2026-05-16", date_to="2026-05-16")
            night_rows = [row for row in rows if row["staff_id"] == night_staff_id]
            self.assertEqual([row["event_type"] for row in night_rows], ["check_out", "check_in"])
            self.assertTrue(all(row["attendance_date"] == "2026-05-16" for row in night_rows))


if __name__ == "__main__":
    unittest.main()
