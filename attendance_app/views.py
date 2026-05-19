from __future__ import annotations

from collections import defaultdict
from base64 import b64decode
from datetime import date, datetime, time, timedelta
from io import BytesIO
import json
import math
from pathlib import Path
import struct
from typing import Any
import sqlite3
from uuid import uuid4
import zlib

from PIL import Image
from flask import (
    Blueprint,
    Response,
    Flask,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .db import init_db
from .auth import (
    ACCESS_ROLE_CHOICES,
    DEPARTMENT_MANAGER,
    HR_ADMIN,
    REPORTING_ROLES,
    SETTINGS_ROLES,
    STAFF_MANAGEMENT_ROLES,
    STAFF,
    SUPER_ADMIN,
    clear_user_session,
    credentials_match,
    current_access_role,
    current_department_scope,
    current_display_name,
    is_platform_admin,
    platform_admin_required,
    roles_required,
    staff_required,
    start_institution_admin_session,
    start_platform_admin_session,
    start_staff_session,
)
from .fingerprint import build_provider
from .services.admin_activity import list_admin_activity_logs, log_admin_activity
from .services.backups import (
    create_organization_backup,
    ensure_automatic_backups,
    list_organization_backups,
    resolve_backup_archive_path,
    restore_organization_backup,
)
from .services.attendance import (
    get_dashboard_data,
    get_recent_events,
    get_staff_today_status,
    list_attendance_events,
    record_attendance,
    report_summary,
    resolve_shift_attendance_date,
    shift_bounds_for_date,
    shift_spans_overnight,
)
from .services.enrollment_sessions import (
    get_enrollment_preview_path,
    read_enrollment_session,
    start_enrollment_session,
)
from .services.qr_codes import build_qr_svg
from .services.reporting import attendance_rows_to_csv, build_report_snapshot, report_snapshot_to_csv
from .services.seed import seed_demo_data
from .services.selfie_audits import create_staff_selfie_audit, list_staff_selfie_audits
from .services.settings import (
    WORKDAY_OPTIONS,
    admin_password_matches,
    get_admin_security,
    get_app_settings,
    save_admin_password,
    save_admin_password_for_database,
    save_app_settings,
)
from .services.staff import (
    authenticate_staff,
    count_active_staff,
    create_staff,
    get_staff,
    get_staff_by_qr_token,
    get_staff_by_template_ref,
    list_departments,
    list_fingerprint_candidates,
    list_mock_scan_choices,
    list_staff,
    mark_ghana_card_verified,
    remove_fingerprint,
    rotate_staff_qr_token,
    update_staff_access_role,
    update_staff,
    upsert_fingerprint,
)
from .services.tenancy import get_current_organization
from .services.tenancy import (
    BILLING_CYCLE_MANUAL,
    BILLING_CYCLE_MONTHLY,
    BILLING_CYCLE_QUARTERLY,
    BILLING_CYCLE_YEARLY,
    LICENSE_STATUS_ACTIVE,
    LICENSE_STATUS_EXPIRED,
    LICENSE_STATUS_SUSPENDED,
    LICENSE_STATUS_TRIAL,
    count_organization_hostnames,
    count_organizations,
    count_organizations_by_license,
    get_current_organization_access_state,
    get_organization_access_state,
    get_organization_by_slug,
    list_organizations,
    provision_organization,
    update_organization,
)

STAFF_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_STAFF_PHOTO_BYTES = 4 * 1024 * 1024
SYSTEM_LOGO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_SYSTEM_LOGO_BYTES = 3 * 1024 * 1024
DEFAULT_LOGO_MARK_STATIC = "branding/jhims-attendance-logo-mark.png"
DEFAULT_LOGO_FULL_STATIC = "branding/jhims-attendance-logo-full.png"
AUDIT_SELFIE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_AUDIT_SELFIE_BYTES = 3 * 1024 * 1024

HOSPITAL_SHIFT_PRESETS = [
    {
        "key": "hospital_morning_8_2",
        "label": "Morning 8AM - 2PM",
        "summary": "Short clinical day shift",
        "shift_start": "08:00",
        "shift_end": "14:00",
        "badge_label": "Morning",
        "badge_tone": "blue",
    },
    {
        "key": "hospital_afternoon_2_8",
        "label": "Afternoon 2PM - 8PM",
        "summary": "Second six-hour hospital shift",
        "shift_start": "14:00",
        "shift_end": "20:00",
        "badge_label": "Afternoon",
        "badge_tone": "orange",
    },
    {
        "key": "hospital_night_8_8",
        "label": "Night 8PM - 8AM",
        "summary": "Three-shift overnight coverage",
        "shift_start": "20:00",
        "shift_end": "08:00",
        "badge_label": "Night",
        "badge_tone": "purple",
    },
    {
        "key": "hospital_day_8_6",
        "label": "Day 8AM - 6PM",
        "summary": "Two-shift daytime coverage",
        "shift_start": "08:00",
        "shift_end": "18:00",
        "badge_label": "Long Day",
        "badge_tone": "cyan",
    },
    {
        "key": "hospital_night_6_8",
        "label": "Night 6PM - 8AM",
        "summary": "Two-shift overnight coverage",
        "shift_start": "18:00",
        "shift_end": "08:00",
        "badge_label": "Night",
        "badge_tone": "purple",
    },
    {
        "key": "hospital_day_8_4",
        "label": "Day 8AM - 4PM",
        "summary": "Standard eight-hour day shift",
        "shift_start": "08:00",
        "shift_end": "16:00",
        "badge_label": "Day",
        "badge_tone": "blue",
    },
]


def register_routes(app: Flask) -> None:
    bp = Blueprint("app", __name__)

    @bp.app_context_processor
    def inject_globals() -> dict[str, Any]:
        settings = current_app.config["APP_SETTINGS"]
        live_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        organization = get_current_organization()
        return {
            "app_name": live_settings["organization_name"],
            "app_logo_url": _system_logo_url_for_filename(live_settings.get("system_logo_filename")),
            "fingerprint_backend": settings.fingerprint_backend,
            "is_cloud_fingerprint_mode": settings.fingerprint_backend == "disabled",
            "current_access_role": current_access_role(),
            "display_name": current_display_name(),
            "organization_slug": organization.slug,
        }

    @bp.before_app_request
    def enforce_organization_license() -> Response | None:
        if request.blueprint != bp.name:
            return None
        endpoint = request.endpoint or ""
        if endpoint.startswith("app.platform_"):
            return None
        if endpoint in {
            "app.health",
            "app.pwa_manifest",
            "app.pwa_service_worker",
            "app.pwa_icon_png",
            "app.pwa_offline",
            "app.organization_access_blocked",
            "app.system_logo",
            "app.staff_photo",
            "app.audit_selfie",
            "app.admin_enrollment_preview",
        }:
            return None
        if is_platform_admin():
            return None

        access_state = get_current_organization_access_state()
        if access_state["access_allowed"]:
            return None
        return redirect(url_for("app.organization_access_blocked"))

    @bp.app_template_filter("human_dt")
    def human_dt(value: str | datetime | None) -> str:
        if not value:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%d %b %Y, %I:%M %p")
        return datetime.fromisoformat(value).strftime("%d %b %Y, %I:%M %p")

    @bp.app_template_filter("human_time")
    def human_time(value: str | datetime | None) -> str:
        if not value:
            return ""
        if isinstance(value, datetime):
            return value.strftime("%I:%M %p")
        return datetime.fromisoformat(value).strftime("%I:%M %p")

    @bp.app_template_filter("title_event")
    def title_event(value: str) -> str:
        return value.replace("_", " ").title()

    @bp.route("/")
    def home():
        if session.get("staff_authenticated"):
            return redirect(url_for("app.staff_home"))
        if session.get("admin_authenticated"):
            if is_platform_admin():
                return redirect(url_for("app.platform_organizations"))
            return redirect(url_for("app.admin_dashboard"))
        if current_app.config["APP_SETTINGS"].fingerprint_backend == "disabled":
            return redirect(url_for("app.staff_login"))
        return redirect(url_for("app.kiosk"))

    @bp.route("/platform/login", methods=["GET", "POST"])
    def platform_login():
        settings = current_app.config["APP_SETTINGS"]
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            next_url = request.form.get("next") or url_for("app.platform_organizations")
            if credentials_match(settings.admin_username, settings.admin_password, username, password):
                start_platform_admin_session(username)
                flash("Platform super admin session started.", "success")
                return redirect(next_url)
            flash("Invalid platform credentials.", "error")

        return render_template(
            "platform/login.html",
            title="Platform Login",
            next_url=request.args.get("next", ""),
        )

    @bp.route("/health")
    def health():
        provider = build_provider(current_app.config["APP_SETTINGS"])
        organization = get_current_organization()
        live_settings = get_app_settings(
            default_app_name=_tenant_default_app_name()
        )
        return jsonify(
            {
                "status": "ok",
                "service": live_settings["organization_name"],
                "organization_slug": organization.slug,
                "license": access_state_summary(get_current_organization_access_state()),
                "fingerprint": provider.healthcheck(),
            }
        )

    @bp.route("/organization-access")
    def organization_access_blocked():
        organization = get_current_organization()
        access_state = get_current_organization_access_state()
        return render_template(
            "organization_access_blocked.html",
            title="License Required",
            organization=organization,
            access_state=access_state,
            body_class="staff-login-minimal-body",
        )

    @bp.route("/pwa/manifest.webmanifest")
    def pwa_manifest():
        live_settings = get_app_settings(
            default_app_name=_tenant_default_app_name()
        )
        app_name = live_settings["organization_name"]
        manifest = {
            "name": app_name,
            "short_name": _pwa_short_name(app_name),
            "id": url_for("app.home"),
            "start_url": url_for("app.home"),
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#11161f",
            "theme_color": "#2f6bff",
            "description": f"{app_name} mobile attendance portal for staff clocking, QR access, and attendance status.",
            "icons": [
                {
                    "src": url_for("app.pwa_icon_png", size=180),
                    "sizes": "180x180",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": url_for("app.pwa_icon_png", size=192),
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": url_for("app.pwa_icon_png", size=512),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
            "shortcuts": [
                {
                    "name": "Staff Login",
                    "short_name": "Login",
                    "url": url_for("app.staff_login"),
                    "icons": [{"src": url_for("app.pwa_icon_png", size=192), "sizes": "192x192"}],
                },
                {
                    "name": "My Attendance",
                    "short_name": "Attendance",
                    "url": url_for("app.staff_home"),
                    "icons": [{"src": url_for("app.pwa_icon_png", size=192), "sizes": "192x192"}],
                },
                {
                    "name": "Admin Login",
                    "short_name": "Admin",
                    "url": url_for("app.admin_login"),
                    "icons": [{"src": url_for("app.pwa_icon_png", size=192), "sizes": "192x192"}],
                },
            ],
        }
        return Response(
            json.dumps(manifest),
            mimetype="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @bp.route("/service-worker.js")
    def pwa_service_worker():
        precache_urls = [
            url_for("app.home"),
            url_for("app.staff_login"),
            url_for("app.admin_login"),
            url_for("app.pwa_offline"),
            url_for("static", filename="styles.css"),
            url_for("static", filename="admin_styles.css"),
              url_for("static", filename="pwa.js"),
              url_for("static", filename="theme.js"),
              url_for("app.pwa_icon_png", size=180),
              url_for("app.pwa_icon_png", size=192),
              url_for("app.pwa_icon_png", size=512),
          ]
        cache_name = f"attendance-pwa-{current_app.config['APP_SETTINGS'].fingerprint_backend}"
        script = f"""
const CACHE_NAME = {json.dumps(cache_name)};
const PRECACHE_URLS = {json.dumps(precache_urls)};

self.addEventListener("install", (event) => {{
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)).then(() => self.skipWaiting())
  );
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
}});

self.addEventListener("fetch", (event) => {{
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) {{
    return;
  }}

  if (request.mode === "navigate") {{
    event.respondWith(
      fetch(request)
        .then((response) => {{
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        }})
        .catch(async () => {{
          const cached = await caches.match(request);
          return cached || caches.match({json.dumps(url_for("app.pwa_offline"))});
        }})
    );
    return;
  }}

  if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/pwa/") || url.pathname === "/service-worker.js") {{
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request).then((response) => {{
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        return response;
      }}))
    );
  }}
}});
"""
        return Response(
            script,
            mimetype="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @bp.route("/pwa/offline")
    def pwa_offline():
        return render_template(
            "pwa/offline.html",
            title="Offline",
            body_class="staff-login-minimal-body",
        )

    @bp.route("/pwa/icon-<int:size>.png")
    def pwa_icon_png(size: int):
        if size not in {120, 152, 167, 180, 192, 512}:
            size = 180
        return Response(
            _generate_pwa_icon_png(size=size),
            mimetype="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @bp.route("/media/staff/<path:filename>")
    def staff_photo(filename: str):
        return send_from_directory(_staff_photo_directory(), filename, max_age=0)

    @bp.route("/media/system/<path:filename>")
    def system_logo(filename: str):
        return send_from_directory(_system_logo_directory(), filename, max_age=0)

    @bp.route("/media/audit-selfies/<path:filename>")
    @roles_required(*SETTINGS_ROLES)
    def audit_selfie_image(filename: str):
        return send_from_directory(_audit_selfie_directory(), filename, max_age=0)

    @bp.route("/kiosk")
    def kiosk():
        provider = build_provider(current_app.config["APP_SETTINGS"])
        last_result = session.pop("last_kiosk_result", None)
        staff_choices = []
        if current_app.config["APP_SETTINGS"].fingerprint_backend == "mock":
            staff_choices = list_mock_scan_choices()
        dashboard_snapshot = get_dashboard_data(fingerprint_adapter=provider.name)
        return render_template(
            "kiosk.html",
            title="Attendance Kiosk",
            last_result=last_result,
            staff_choices=staff_choices,
            recent_events=get_recent_events(limit=6),
            dashboard_snapshot=dashboard_snapshot,
            health=provider.healthcheck(),
            today=date.today(),
        )

    @bp.route("/kiosk/scan", methods=["POST"])
    def kiosk_scan():
        settings = current_app.config["APP_SETTINGS"]
        provider = build_provider(settings)
        hint = request.form.get("mock_template_ref") or None

        if provider.name == "disabled":
            flash(
                "Fingerprint scanning is disabled in the online deployment. Use QR, PIN/password, or the local kiosk hardware.",
                "warning",
            )
            return redirect(url_for("app.kiosk"))

        if settings.fingerprint_backend != "mock":
            candidates = list_fingerprint_candidates(provider.name)
            if not candidates:
                flash(
                    "No real MorphoSmart fingerprints are enrolled for this backend yet. "
                    "Open Staff and use Enroll Fingerprint first.",
                    "error",
                )
                return redirect(url_for("app.kiosk"))
        else:
            candidates = []

        try:
            if hasattr(provider, "identify_candidates"):
                match = provider.identify_candidates(candidates)
            else:
                match = provider.identify(hint=hint)
            if match is None:
                flash("Fingerprint was not recognized. Please try again.", "error")
                return redirect(url_for("app.kiosk"))

            staff = get_staff_by_template_ref(match.template_ref, adapter=provider.name)
            if not staff:
                flash(
                    "A fingerprint was matched on the device, but it is not linked to an active staff record.",
                    "error",
                )
                return redirect(url_for("app.kiosk"))

            result = record_attendance(
                staff=staff,
                template_ref=match.template_ref,
                confidence=match.confidence,
                method="fingerprint",
                device_name=provider.name,
            )
        except RuntimeError as exc:
            flash(str(exc), "error")
            return redirect(url_for("app.kiosk"))

        _store_last_kiosk_result(result)
        flash(
            f"{result['staff_name']} recorded a {result['event_type'].replace('_', ' ')} successfully.",
            "success",
        )
        return redirect(url_for("app.kiosk"))

    @bp.route("/kiosk/quick-access", methods=["POST"])
    def kiosk_quick_access():
        qr_value = _normalize_qr_value(request.form.get("qr_token", ""))
        staff_code = request.form.get("staff_code", "").strip().upper()
        secret = request.form.get("secret", "")
        method = request.form.get("secret_method", "pin").strip().lower()

        staff = None
        attendance_method = "pin_kiosk"
        if qr_value:
            staff = get_staff_by_qr_token(qr_value)
            attendance_method = "qr_kiosk"
            if not staff:
                flash("That QR code is not linked to an active staff member.", "error")
                return redirect(url_for("app.kiosk"))
            if not staff.get("allow_qr_clock"):
                flash("QR clock-in is disabled for this staff member.", "warning")
                return redirect(url_for("app.kiosk"))
        elif staff_code and secret:
            password = secret if method == "password" else ""
            pin = secret if method != "password" else ""
            staff = authenticate_staff(staff_code=staff_code, password=password, pin=pin)
            if not staff:
                flash("Staff code and PIN/password did not match.", "error")
                return redirect(url_for("app.kiosk"))
            if method == "password":
                attendance_method = "password_kiosk"
            elif not staff.get("allow_pin_clock"):
                flash("PIN clock-in is disabled for this staff member.", "warning")
                return redirect(url_for("app.kiosk"))
        else:
            flash("Use either QR code access or enter a staff code with PIN/password.", "warning")
            return redirect(url_for("app.kiosk"))

        result = record_attendance(
            staff=staff,
            template_ref=staff["staff_code"],
            confidence=None,
            method=attendance_method,
            device_name="kiosk_quick_access",
            notes="Quick access kiosk event",
        )
        _store_last_kiosk_result(result)
        flash(
            f"{result['staff_name']} recorded a {result['event_type'].replace('_', ' ')} successfully.",
            "success",
        )
        return redirect(url_for("app.kiosk"))

    @bp.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        settings = current_app.config["APP_SETTINGS"]
        live_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            next_url = request.form.get("next") or url_for("app.admin_dashboard")
            if (
                username == settings.admin_username
                and admin_password_matches(password, settings.admin_password)
            ):
                start_institution_admin_session(username, live_settings["organization_name"])
                log_admin_activity(
                    actor_type="user",
                    actor_name=live_settings["organization_name"] or username,
                    actor_role="Institution Admin",
                    event_type="admin_login",
                    details="Institution administrator signed in successfully.",
                    ip_address=_request_ip_address(),
                    device_name=_request_device_name(),
                )
                flash("Administrator session started.", "success")
                return redirect(next_url)
            flash("Invalid administrator credentials.", "error")

        return render_template(
            "admin/login.html",
            title="Admin Login",
            next_url=request.args.get("next", ""),
        )

    @bp.route("/staff/login", methods=["GET", "POST"])
    def staff_login():
        if request.method == "POST":
            staff_identifier = request.form.get("staff_identifier", "").strip()
            staff_code = request.form.get("staff_code", "").strip().upper()
            password = request.form.get("password", "")
            pin = request.form.get("pin", "")
            selfie_data = request.form.get("selfie_data", "").strip()
            next_url = request.form.get("next", "").strip()
            staff = authenticate_staff(
                staff_code=staff_code,
                login_identifier=staff_identifier,
                password=password,
                pin=pin,
            )
            if staff:
                auth_method = "password" if password else "pin"
                try:
                    selfie_capture = _store_login_selfie_capture(selfie_data)
                except ValueError as exc:
                    flash(str(exc), "error")
                    return render_template(
                        "staff/login.html",
                        title="Staff Login",
                        next_url=next_url,
                        body_class="staff-login-minimal-body",
                    )
                create_staff_selfie_audit(
                    staff_id=int(staff["id"]),
                    login_identifier=staff_identifier or staff_code,
                    auth_method=auth_method,
                    photo_filename=selfie_capture["filename"],
                    photo_mime_type=selfie_capture["mime_type"],
                    file_size_bytes=selfie_capture["file_size_bytes"],
                    ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "")[:255],
                    device_name=_request_device_name(),
                )
                start_staff_session(staff)
                flash("Welcome back. You are signed in.", "success")
                if next_url:
                    return redirect(next_url)
                if session.get("admin_authenticated"):
                    return redirect(url_for("app.admin_dashboard"))
                return redirect(url_for("app.staff_home"))
            flash("Invalid staff code or PIN/password.", "error")

        return render_template(
            "staff/login.html",
            title="Staff Login",
            next_url=request.args.get("next", ""),
            body_class="staff-login-minimal-body",
        )

    @bp.route("/logout")
    def logout():
        was_staff = bool(session.get("staff_authenticated"))
        was_admin = bool(session.get("admin_authenticated"))
        was_platform_admin = bool(session.get("is_platform_admin"))
        actor_name = current_display_name()
        actor_role = current_access_role()
        if was_admin and not was_platform_admin:
            log_admin_activity(
                actor_type="user",
                actor_name=actor_name,
                actor_role=actor_role or "Institution Admin",
                event_type="logout",
                details="Administrator session closed.",
                ip_address=_request_ip_address(),
                device_name=_request_device_name(),
            )
        clear_user_session()
        flash("Session closed.", "success")
        if was_platform_admin:
            return redirect(url_for("app.platform_login"))
        if was_staff:
            return redirect(url_for("app.staff_login"))
        if was_admin:
            return redirect(url_for("app.admin_login"))
        if current_app.config["APP_SETTINGS"].fingerprint_backend == "disabled":
            return redirect(url_for("app.staff_login"))
        return redirect(url_for("app.kiosk"))

    @bp.route("/platform/logout")
    def platform_logout():
        return redirect(url_for("app.logout"))

    @bp.route("/platform/organizations", methods=["GET", "POST"])
    @platform_admin_required
    def platform_organizations():
        settings = current_app.config["APP_SETTINGS"]
        create_form = {
            "slug": "",
            "display_name": "",
            "hostnames": "",
            "is_default": False,
            "admin_password": "",
            "confirm_admin_password": "",
            "plan_name": "Standard",
            "license_status": LICENSE_STATUS_TRIAL,
            "expires_on": "",
            "billing_contact_name": "",
            "billing_email": "",
            "billing_phone": "",
            "billing_cycle": BILLING_CYCLE_MONTHLY,
            "subscription_amount": "0.00",
            "renewal_due_on": "",
            "last_payment_on": "",
            "license_notes": "",
        }
        update_forms: dict[str, dict[str, Any]] = {}

        if request.method == "POST":
            action = request.form.get("action", "create").strip().lower()
            if action == "create":
                create_form = _read_platform_organization_form(request.form)
                validation_error = _validate_platform_organization_form(create_form, creating=True)
                if validation_error:
                    flash(validation_error, "error")
                else:
                    try:
                        created_organization = provision_organization(
                            settings,
                            slug=create_form["slug"],
                            display_name=create_form["display_name"],
                            hostnames=create_form["hostnames_list"],
                            is_default=create_form["is_default"],
                            plan_name=create_form["plan_name"],
                            license_status=create_form["license_status"],
                            expires_on=create_form["expires_on"],
                            billing_contact_name=create_form["billing_contact_name"],
                            billing_email=create_form["billing_email"],
                            billing_phone=create_form["billing_phone"],
                            billing_cycle=create_form["billing_cycle"],
                            subscription_amount=create_form["subscription_amount"],
                            renewal_due_on=create_form["renewal_due_on"],
                            last_payment_on=create_form["last_payment_on"],
                            license_notes=create_form["license_notes"],
                        )
                        init_db(created_organization.database_path)
                        save_admin_password_for_database(
                            created_organization.database_path,
                            create_form["admin_password"],
                        )
                    except ValueError as exc:
                        flash(str(exc), "error")
                    else:
                        flash(
                            f"{create_form['display_name']} was provisioned successfully.",
                            "success",
                        )
                        return redirect(url_for("app.platform_organizations"))
            elif action == "update":
                slug = request.form.get("organization_slug", "").strip()
                update_form = _read_platform_organization_form(request.form)
                update_form["slug"] = slug
                update_forms[slug] = update_form
                validation_error = _validate_platform_organization_form(update_form, creating=False)
                if validation_error:
                    flash(validation_error, "error")
                else:
                    try:
                        updated_organization = update_organization(
                            settings,
                            slug=slug,
                            display_name=update_form["display_name"],
                            hostnames=update_form["hostnames_list"],
                            is_default=update_form["is_default"],
                            plan_name=update_form["plan_name"],
                            license_status=update_form["license_status"],
                            expires_on=update_form["expires_on"],
                            billing_contact_name=update_form["billing_contact_name"],
                            billing_email=update_form["billing_email"],
                            billing_phone=update_form["billing_phone"],
                            billing_cycle=update_form["billing_cycle"],
                            subscription_amount=update_form["subscription_amount"],
                            renewal_due_on=update_form["renewal_due_on"],
                            last_payment_on=update_form["last_payment_on"],
                            license_notes=update_form["license_notes"],
                        )
                        if update_form["admin_password"]:
                            save_admin_password_for_database(
                                updated_organization.database_path,
                                update_form["admin_password"],
                            )
                    except ValueError as exc:
                        flash(str(exc), "error")
                    else:
                        flash(
                            f"{update_form['display_name']} was updated successfully.",
                            "success",
                        )
                        return redirect(url_for("app.platform_organizations"))
            elif action == "create_backup":
                slug = request.form.get("organization_slug", "").strip()
                target_organization = get_organization_by_slug(settings, slug)
                if not target_organization:
                    flash("The selected institution could not be found.", "error")
                else:
                    backup_path = create_organization_backup(
                        target_organization,
                        reason="manual",
                        note="Created from the platform super admin portal.",
                    )
                    flash(
                        f"Backup created for {target_organization.display_name}: {backup_path.name}",
                        "success",
                    )
                    return redirect(url_for("app.platform_organizations"))
            elif action == "restore_backup":
                slug = request.form.get("organization_slug", "").strip()
                backup_name = request.form.get("backup_name", "").strip()
                target_organization = get_organization_by_slug(settings, slug)
                if not target_organization:
                    flash("The selected institution could not be found.", "error")
                elif not backup_name:
                    flash("Choose a backup snapshot before restoring.", "error")
                else:
                    try:
                        existing_db = g.pop("db", None)
                        if existing_db is not None:
                            existing_db.close()
                        pre_restore_backup = restore_organization_backup(
                            target_organization,
                            backup_name,
                        )
                    except ValueError as exc:
                        flash(str(exc), "error")
                    else:
                        flash(
                            f"{target_organization.display_name} was restored from {backup_name}. "
                            f"A safety snapshot was saved as {pre_restore_backup.name}.",
                            "success",
                        )
                        return redirect(url_for("app.platform_organizations"))

        organizations = list_organizations(settings)
        ensure_automatic_backups(organizations)
        organizations = list_organizations(settings)
        rows = _platform_organization_rows(organizations, update_forms=update_forms)
        stats = {
            "organizations": count_organizations(settings),
            "hostnames": count_organization_hostnames(settings),
            "default_slug": next((org.slug for org in organizations if org.is_default), settings.default_organization_slug),
            "default_name": next((org.display_name for org in organizations if org.is_default), settings.app_name),
            "active": count_organizations_by_license(settings, LICENSE_STATUS_ACTIVE),
            "trial": count_organizations_by_license(settings, LICENSE_STATUS_TRIAL),
            "blocked": count_organizations_by_license(
                settings,
                LICENSE_STATUS_EXPIRED,
                LICENSE_STATUS_SUSPENDED,
            ),
        }
        return render_template(
            "platform/organizations.html",
            title="Platform Organizations",
            create_form=create_form,
            organizations=rows,
            platform_stats=stats,
            license_status_options=_platform_license_status_options(),
            billing_cycle_options=_platform_billing_cycle_options(),
            **_platform_context("Organizations", "organizations", ["Platform", "Organizations"]),
        )

    @bp.route("/platform/organizations/<slug>/backups/<path:backup_name>")
    @platform_admin_required
    def platform_download_backup(slug: str, backup_name: str):
        settings = current_app.config["APP_SETTINGS"]
        organization = get_organization_by_slug(settings, slug)
        if not organization:
            flash("The selected institution could not be found.", "error")
            return redirect(url_for("app.platform_organizations"))

        try:
            archive_path = resolve_backup_archive_path(organization, backup_name)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("app.platform_organizations"))

        return send_file(
            archive_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=archive_path.name,
            max_age=0,
        )

    @bp.route("/admin/logout")
    def admin_logout():
        return redirect(url_for("app.logout"))

    @bp.route("/staff/logout")
    def staff_logout():
        return redirect(url_for("app.logout"))

    @bp.route("/admin/dashboard")
    @roles_required(*REPORTING_ROLES)
    def admin_dashboard():
        fingerprint_backend = current_app.config["APP_SETTINGS"].fingerprint_backend
        department_scope = current_department_scope()
        dashboard_search = request.args.get("search", "").strip()
        selected_date_raw = request.args.get("target_date", "").strip()
        try:
            selected_date = date.fromisoformat(selected_date_raw) if selected_date_raw else date.today()
        except ValueError:
            selected_date = date.today()
        dashboard = get_dashboard_data(
            fingerprint_adapter=fingerprint_backend,
            department_scope=department_scope,
            search=dashboard_search,
            target_date=selected_date,
        )
        recent_events = list_attendance_events(
            date_from=(selected_date - timedelta(days=6)).isoformat(),
            date_to=selected_date.isoformat(),
            search=dashboard_search,
            department_scope=department_scope,
        )
        recent_rows = _attendance_activity_rows(recent_events)
        return render_template(
            "admin/dashboard.html",
            title="Dashboard",
            dashboard=dashboard,
            dashboard_model=_dashboard_live_model(
                dashboard=dashboard,
                activity_rows=recent_rows,
                target_date=selected_date,
            ),
            recent_rows=recent_rows[:5],
            today=selected_date,
            dashboard_search=dashboard_search,
            dashboard_target_date=selected_date.isoformat(),
            **_admin_context("Dashboard", "dashboard", ["Dashboard"]),
        )

    @bp.route("/admin/staff")
    @roles_required(*STAFF_MANAGEMENT_ROLES, HR_ADMIN, SUPER_ADMIN)
    def admin_staff():
        provider = build_provider(current_app.config["APP_SETTINGS"])
        search = request.args.get("search", "").strip()
        department_scope = current_department_scope()
        department = _resolve_department_filter(
            request.args.get("department", "").strip(),
            department_scope,
        )
        show_active_only = request.args.get("active_only", "1") == "1"
        staff_rows = list_staff(
            search=search,
            department=department,
            active_only=show_active_only,
            fingerprint_adapter=provider.name,
            department_scope=department_scope,
        )
        display_rows = _staff_display_rows(staff_rows)
        total_count = len(staff_rows)
        active_count = sum(1 for row in staff_rows if row.get("is_active"))
        on_leave_count = sum(1 for row in display_rows if row.get("status_text") == "On Leave")
        inactive_count = total_count - active_count
        return render_template(
            "admin/staff_list.html",
            title="Staff Directory",
            staff_rows=staff_rows,
            display_rows=display_rows,
            display_total=total_count,
            staff_photo_urls={},
            staff_stats=[
                {"icon": "staff", "tone": "blue", "label": "Total Staff", "value": str(total_count), "sub": "All Employees"},
                {"icon": "staff", "tone": "green", "label": "Active Staff", "value": str(active_count), "sub": f"{round((active_count / max(total_count, 1)) * 100, 2)}%"},
                {"icon": "staff", "tone": "orange", "label": "On Leave", "value": str(on_leave_count), "sub": "4.69%"},
                {"icon": "staff", "tone": "red", "label": "Inactive Staff", "value": str(max(inactive_count, 0)), "sub": "2.34%"},
            ],
            departments=list_departments(department_scope=department_scope),
            search=search,
            selected_department=department,
            active_only=show_active_only,
            backend=current_app.config["APP_SETTINGS"].fingerprint_backend,
            **_admin_context("Staff", "staff", ["Dashboard", "Staff"]),
        )

    @bp.route("/admin/staff/new", methods=["GET", "POST"])
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_staff_new():
        form_values = _staff_form_defaults()
        if request.method == "POST":
            form_values = _read_staff_form(request.form)
            form_values["photo_filename"] = ""
            validation_error = _validate_staff_form(form_values, creating=True)
            if validation_error:
                flash(validation_error, "error")
            else:
                saved_photo_filename = None
                try:
                    saved_photo_filename = _store_staff_photo_upload(request.files.get("staff_photo"))
                    form_values["photo_filename"] = saved_photo_filename
                    create_staff(form_values)
                except sqlite3.IntegrityError:
                    if saved_photo_filename:
                        _delete_staff_photo(saved_photo_filename)
                    flash("Staff code or QR token already exists. Use unique staff details.", "error")
                except ValueError as exc:
                    flash(str(exc), "error")
                else:
                    flash("Staff member created successfully.", "success")
                    return redirect(url_for("app.admin_staff"))

        return render_template(
            "admin/staff_form.html",
            title="Add Staff Member",
            form_values=form_values,
            staff=None,
            form_action=url_for("app.admin_staff_new"),
            access_role_choices=ACCESS_ROLE_CHOICES,
            shift_presets=_hospital_shift_presets(),
            **_admin_context("Staff", "staff", ["Dashboard", "Staff", "Add New Staff"]),
        )

    @bp.route("/admin/staff/<int:staff_id>/edit", methods=["GET", "POST"])
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_staff_edit(staff_id: int):
        staff = _get_manageable_staff(staff_id)
        if not staff:
            return redirect(url_for("app.admin_staff"))

        form_values = _staff_form_defaults(staff)
        if request.method == "POST":
            form_values = _read_staff_form(request.form)
            form_values["photo_filename"] = staff.get("photo_filename", "")
            validation_error = _validate_staff_form(form_values, creating=False)
            if validation_error:
                flash(validation_error, "error")
            else:
                previous_photo_filename = staff.get("photo_filename")
                saved_photo_filename = None
                try:
                    saved_photo_filename = _store_staff_photo_upload(request.files.get("staff_photo"))
                    if saved_photo_filename:
                        form_values["photo_filename"] = saved_photo_filename
                    elif request.form.get("remove_photo") == "on":
                        form_values["photo_filename"] = None
                    else:
                        form_values["photo_filename"] = previous_photo_filename
                    update_staff(staff_id, form_values)
                except sqlite3.IntegrityError:
                    if saved_photo_filename:
                        _delete_staff_photo(saved_photo_filename)
                    flash("Staff code or QR token already exists. Use unique staff details.", "error")
                except ValueError as exc:
                    if saved_photo_filename:
                        _delete_staff_photo(saved_photo_filename)
                    flash(str(exc), "error")
                else:
                    if saved_photo_filename and previous_photo_filename and previous_photo_filename != saved_photo_filename:
                        _delete_staff_photo(previous_photo_filename)
                    elif request.form.get("remove_photo") == "on" and previous_photo_filename:
                        _delete_staff_photo(previous_photo_filename)
                    flash("Staff member updated successfully.", "success")
                    return redirect(url_for("app.admin_staff"))

        return render_template(
            "admin/staff_form.html",
            title="Edit Staff Member",
            form_values=form_values,
            staff=staff,
            form_action=url_for("app.admin_staff_edit", staff_id=staff_id),
            access_role_choices=ACCESS_ROLE_CHOICES,
            shift_presets=_hospital_shift_presets(),
            **_admin_context("Staff", "staff", ["Dashboard", "Staff", "Edit Staff"]),
        )

    @bp.route("/admin/staff/<int:staff_id>/enroll", methods=["GET", "POST"])
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_staff_enroll(staff_id: int):
        provider = build_provider(current_app.config["APP_SETTINGS"])
        staff = get_staff(
            staff_id,
            fingerprint_adapter=provider.name,
            department_scope=current_department_scope(),
        )
        if not staff:
            flash("Staff member not found.", "error")
            return redirect(url_for("app.admin_staff"))

        if provider.name == "disabled":
            flash(
                "Fingerprint enrollment is disabled in the online deployment. "
                "Use QR, PIN/password, or enroll from the local kiosk installation.",
                "warning",
            )
            return redirect(url_for("app.admin_staff_qr", staff_id=staff_id))

        session_id = request.args.get("session_id", "").strip()
        enrollment_session = None
        if session_id:
            enrollment_session = read_enrollment_session(
                _tenant_instance_dir(),
                session_id,
            )

        if request.method == "GET":
            return render_template(
                "admin/enroll_fingerprint.html",
                title="Enroll Fingerprint",
                staff=staff,
                health={
                    "backend": provider.name,
                    "status": "ready",
                    "details": "Start enrollment to open the live MorphoSmart capture session.",
                },
                enrollment_session=enrollment_session,
                session_id=session_id,
                **_admin_context("Staff", "staff", ["Dashboard", "Staff", "Enroll Fingerprint"]),
            )

        if hasattr(provider, "enroll_with_progress"):
            session_id = start_enrollment_session(
                instance_dir=_tenant_instance_dir(),
                provider=provider,
                app=current_app._get_current_object(),
                staff_id=staff_id,
                staff_code=staff["staff_code"],
                staff_name=f"{staff['first_name']} {staff['last_name']}",
            )
            flash(
                "Enrollment started. Follow the on-screen scanner instructions and keep your finger steady.",
                "success",
            )
            return redirect(url_for("app.admin_staff_enroll", staff_id=staff_id, session_id=session_id))

        try:
            enrollment = provider.enroll(staff["staff_code"])
            upsert_fingerprint(
                staff_id=staff_id,
                adapter=provider.name,
                template_ref=enrollment.template_ref,
                template_format=str(enrollment.raw_payload.get("template_format", "")),
                template_data=_decode_template_data(enrollment.raw_payload.get("template_data_base64")),
                quality_score=enrollment.quality_score,
                notes=enrollment.message,
            )
        except RuntimeError as exc:
            flash(str(exc), "error")
            return redirect(url_for("app.admin_staff_enroll", staff_id=staff_id))
        else:
            flash(
                f"Fingerprint enrolled for {staff['first_name']} {staff['last_name']}.",
                "success",
            )
        return redirect(url_for("app.admin_staff"))

    @bp.route("/admin/enrollment/<session_id>/status")
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_enrollment_status(session_id: str):
        payload = read_enrollment_session(
            _tenant_instance_dir(),
            session_id,
        )
        if not payload:
            return jsonify({"state": "missing", "complete": True, "message": "Enrollment session was not found."}), 404
        return jsonify(payload)

    @bp.route("/admin/enrollment/<session_id>/preview.png")
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_enrollment_preview(session_id: str):
        preview_path = get_enrollment_preview_path(
            _tenant_instance_dir(),
            session_id,
        )
        if not preview_path.exists():
            return Response(status=404)
        return send_file(preview_path, mimetype="image/png", max_age=0)

    @bp.route("/admin/staff/<int:staff_id>/remove-fingerprint", methods=["POST"])
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_staff_remove_fingerprint(staff_id: int):
        staff = _get_manageable_staff(staff_id)
        if not staff:
            return redirect(url_for("app.admin_staff"))

        provider = build_provider(current_app.config["APP_SETTINGS"])
        if staff.get("template_ref") and staff.get("fingerprint_adapter") == provider.name:
            try:
                provider.delete(staff["template_ref"])
            except RuntimeError as exc:
                flash(
                    f"Hardware record could not be deleted cleanly: {exc}. The database link will still be removed.",
                    "warning",
                )

        remove_fingerprint(staff_id)
        flash("Fingerprint link removed from this staff record.", "success")
        return redirect(url_for("app.admin_staff"))

    @bp.route("/admin/staff/<int:staff_id>/qr")
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_staff_qr(staff_id: int):
        staff = _get_manageable_staff(staff_id)
        if not staff:
            return redirect(url_for("app.admin_staff"))

        quick_url = request.url_root.rstrip("/") + url_for("app.staff_quick_access", qr_token=staff["qr_token"])
        return render_template(
            "admin/staff_qr.html",
            title="Staff QR Badge",
            staff=staff,
            quick_url=quick_url,
            mobile_qr_svg=build_qr_svg(quick_url),
            kiosk_qr_svg=build_qr_svg(staff["qr_token"]),
            **_admin_context("Staff", "staff", ["Dashboard", "Staff", "QR Badge"]),
        )

    @bp.route("/admin/staff/<int:staff_id>/rotate-qr", methods=["POST"])
    @roles_required(*STAFF_MANAGEMENT_ROLES)
    def admin_staff_rotate_qr(staff_id: int):
        staff = _get_manageable_staff(staff_id)
        if not staff:
            return redirect(url_for("app.admin_staff"))
        rotate_staff_qr_token(staff_id)
        flash("A new QR token was generated for this staff member.", "success")
        return redirect(url_for("app.admin_staff_qr", staff_id=staff_id))

    @bp.route("/admin/ghana-card-verification")
    @roles_required(*STAFF_MANAGEMENT_ROLES, HR_ADMIN, SUPER_ADMIN)
    def admin_ghana_card_verification():
        settings = current_app.config["APP_SETTINGS"]
        provider = build_provider(settings)
        department_scope = current_department_scope()
        search = request.args.get("search", "").strip()
        staff_rows = list_staff(
            search=search,
            active_only=False,
            fingerprint_adapter=provider.name,
            department_scope=department_scope,
        )
        verification_rows = _ghana_card_verification_rows(staff_rows)
        mock_scan_choices = []
        if settings.fingerprint_backend == "mock":
            mock_scan_choices = list_mock_scan_choices()
        return render_template(
            "admin/ghana_card_verification.html",
            title="Ghana Card Verification",
            verification_rows=verification_rows,
            verification_stats=_ghana_card_verification_stats(verification_rows),
            search=search,
            mock_scan_choices=mock_scan_choices,
            last_verification=session.pop("last_ghana_card_verification", None),
            health=provider.healthcheck(),
            **_admin_context(
                "Ghana Card Verification",
                "ghana-card",
                ["Dashboard", "Ghana Card Verification"],
            ),
        )

    @bp.route("/admin/ghana-card-verification/scan", methods=["POST"])
    @roles_required(*STAFF_MANAGEMENT_ROLES, HR_ADMIN, SUPER_ADMIN)
    def admin_ghana_card_verification_scan():
        settings = current_app.config["APP_SETTINGS"]
        provider = build_provider(settings)
        hint = request.form.get("mock_template_ref") or None

        if provider.name == "disabled":
            flash(
                "Fingerprint scanning is disabled in the online deployment. Use the local kiosk hardware for verification.",
                "warning",
            )
            return redirect(url_for("app.admin_ghana_card_verification"))

        if settings.fingerprint_backend != "mock":
            candidates = list_fingerprint_candidates(provider.name)
            if not candidates:
                flash(
                    "No real fingerprints are enrolled for this backend yet. Enroll a fingerprint before using Ghana Card verification.",
                    "error",
                )
                return redirect(url_for("app.admin_ghana_card_verification"))
        else:
            candidates = []

        try:
            if hasattr(provider, "identify_candidates"):
                match = provider.identify_candidates(candidates)
            else:
                match = provider.identify(hint=hint)
            if match is None:
                flash("Fingerprint was not recognized. Please try the verification scan again.", "error")
                return redirect(url_for("app.admin_ghana_card_verification"))
        except RuntimeError as exc:
            flash(str(exc), "error")
            return redirect(url_for("app.admin_ghana_card_verification"))

        matched_staff = get_staff_by_template_ref(match.template_ref, adapter=provider.name)
        if not matched_staff:
            flash(
                "A fingerprint was matched on the device, but it is not linked to an active staff record.",
                "error",
            )
            return redirect(url_for("app.admin_ghana_card_verification"))

        staff = get_staff(
            int(matched_staff["id"]),
            fingerprint_adapter=provider.name,
            department_scope=current_department_scope(),
        )
        if not staff:
            flash("That staff record is outside your department scope or no longer active.", "warning")
            return redirect(url_for("app.admin_ghana_card_verification"))

        missing_fields = _missing_ghana_card_fields(staff)
        if missing_fields:
            flash(
                "Complete the Ghana Card fields first: " + ", ".join(missing_fields) + ".",
                "error",
            )
            return redirect(url_for("app.admin_staff_edit", staff_id=staff["id"]))

        verified_at = datetime.now().isoformat(timespec="seconds")
        mark_ghana_card_verified(
            int(staff["id"]),
            verified_at=verified_at,
            verified_by=str(session.get("admin_username") or current_display_name()),
        )
        refreshed_staff = get_staff(
            int(staff["id"]),
            fingerprint_adapter=provider.name,
            department_scope=current_department_scope(),
        )
        _store_ghana_card_verification_result(
            refreshed_staff or staff,
            confidence=match.confidence,
            backend=provider.name,
            verified_at=verified_at,
        )
        flash(
            f"{staff['first_name']} {staff['last_name']} was verified successfully. You can now print the internal verification card.",
            "success",
        )
        return redirect(url_for("app.admin_ghana_card_card", staff_id=staff["id"]))

    @bp.route("/admin/ghana-card-verification/<int:staff_id>/card")
    @roles_required(*STAFF_MANAGEMENT_ROLES, HR_ADMIN, SUPER_ADMIN)
    def admin_ghana_card_card(staff_id: int):
        staff = get_staff(
            staff_id,
            fingerprint_adapter=current_app.config["APP_SETTINGS"].fingerprint_backend,
            department_scope=current_department_scope(),
        )
        if not staff:
            flash("Staff member not found or outside your department scope.", "warning")
            return redirect(url_for("app.admin_ghana_card_verification"))

        missing_fields = _missing_ghana_card_fields(staff)
        if missing_fields:
            flash(
                "Complete the Ghana Card fields first: " + ", ".join(missing_fields) + ".",
                "error",
            )
            return redirect(url_for("app.admin_staff_edit", staff_id=staff_id))

        return render_template(
            "admin/ghana_card_card.html",
            title="Ghana Card Verification Card",
            staff=staff,
            card_model=_ghana_card_print_model(staff),
            **_admin_context(
                "Ghana Card Verification",
                "ghana-card",
                ["Dashboard", "Ghana Card Verification", "Verification Card"],
                body_class="ghana-card-print-page",
            ),
        )

    @bp.route("/admin/attendance")
    @roles_required(*REPORTING_ROLES)
    def admin_attendance():
        department_scope = current_department_scope()
        date_from = request.args.get("date_from", "")
        date_to = request.args.get("date_to", "")
        department = _resolve_department_filter(request.args.get("department", ""), department_scope)
        search = request.args.get("search", "").strip()
        rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=department,
            search=search,
            department_scope=department_scope,
        )
        activity_rows = _attendance_activity_rows(rows)
        active_staff_total = count_active_staff(department_scope=department_scope)
        return render_template(
            "admin/attendance_list.html",
            title="Attendance Logs",
            rows=rows,
            summary=report_summary(rows),
            attendance_model=_attendance_live_model(
                activity_rows=activity_rows,
                active_staff_total=active_staff_total,
                labels=_default_recent_date_labels(),
            ),
            attendance_rows=activity_rows,
            departments=list_departments(department_scope=department_scope),
            filters={
                "date_from": date_from,
                "date_to": date_to,
                "department": department,
                "search": search,
            },
            **_admin_context("Attendance", "attendance", ["Dashboard", "Attendance"]),
        )

    @bp.route("/admin/shift-management")
    @roles_required(*REPORTING_ROLES)
    def admin_shift_management():
        department_scope = current_department_scope()
        return render_template(
            "admin/shift_management.html",
            title="Shift Management",
            shift_model=_hospital_shift_management_model(department_scope=department_scope),
            **_admin_context("Shift Management", "shift", ["Dashboard", "Shift Management", "Shifts"], nav_secondary="shifts"),
        )

    @bp.route("/admin/leave-management")
    @roles_required(*REPORTING_ROLES)
    def admin_leave_management():
        return render_template(
            "admin/leave_management.html",
            title="Leave Management",
            leave_model=_leave_empty_model(),
            leave_rows=[],
            **_admin_context("Leave Management", "leave", ["Dashboard", "Leave Management", "Leave Requests"], nav_secondary="requests"),
        )

    @bp.route("/admin/overtime")
    @roles_required(*REPORTING_ROLES)
    def admin_overtime():
        return render_template(
            "admin/overtime.html",
            title="Overtime",
            overtime_model=_overtime_empty_model(),
            overtime_rows=[],
            **_admin_context("Overtime", "overtime", ["Dashboard", "Overtime"]),
        )

    @bp.route("/admin/payroll")
    @roles_required(*REPORTING_ROLES)
    def admin_payroll():
        return render_template(
            "admin/payroll.html",
            title="Payroll",
            payroll_model=_payroll_empty_model(),
            payroll_rows=[],
            **_admin_context("Payroll", "payroll", ["Dashboard", "Payroll", "May 2024 Payroll"], nav_secondary="dashboard"),
        )

    @bp.route("/admin/attendance-correction")
    @roles_required(*REPORTING_ROLES)
    def admin_attendance_correction():
        return render_template(
            "admin/attendance_correction.html",
            title="Attendance Correction",
            correction_model=_correction_empty_model(),
            correction_rows=[],
            **_admin_context("Attendance Correction", "correction", ["Dashboard", "Attendance Correction", "Correction Requests"], nav_secondary="requests"),
        )

    @bp.route("/admin/holidays")
    @roles_required(*REPORTING_ROLES)
    def admin_holidays():
        return render_template(
            "admin/holidays.html",
            title="Holidays",
            holiday_rows=[],
            **_admin_context("Holidays", "holidays", ["Dashboard", "Holidays"]),
        )

    @bp.route("/admin/notifications")
    @roles_required(*REPORTING_ROLES)
    def admin_notifications():
        notification_rows = _build_admin_notification_rows(limit=20)
        return render_template(
            "admin/notifications.html",
            title="Notifications",
            notification_rows=notification_rows,
            **_admin_context("Notifications", "notifications", ["Dashboard", "Notifications"]),
        )

    @bp.route("/admin/users-roles", methods=["GET", "POST"])
    @roles_required(*SETTINGS_ROLES)
    def admin_users_roles():
        department_scope = current_department_scope()
        search = request.values.get("search", "").strip()
        department = _resolve_department_filter(
            request.values.get("department", "").strip(),
            department_scope,
        )
        show_active_only = request.values.get("active_only", "0") == "1"

        if request.method == "POST":
            staff_id_raw = request.form.get("staff_id", "").strip()
            access_role = request.form.get("access_role", "").strip()
            redirect_params = {
                "search": search,
                "department": department,
                "active_only": "1" if show_active_only else "0",
            }
            try:
                staff_id = int(staff_id_raw)
            except ValueError:
                flash("Choose a valid staff account before saving a role change.", "error")
            else:
                updated = update_staff_access_role(
                    staff_id,
                    access_role,
                    is_active=request.form.get("is_active") == "on",
                    department_scope=department_scope,
                )
                if updated:
                    target_staff = get_staff(staff_id, department_scope=department_scope)
                    log_admin_activity(
                        actor_type="user",
                        actor_name=current_display_name(),
                        actor_role=current_access_role() or "Institution Admin",
                        event_type="user_role_updated",
                        target_name=(
                            f"{target_staff.get('first_name', '')} {target_staff.get('last_name', '')}".strip()
                            if target_staff
                            else f"Staff #{staff_id}"
                        ),
                        details=f"Access role changed to {access_role or 'Staff'} and account status was updated.",
                        ip_address=_request_ip_address(),
                        device_name=_request_device_name(),
                    )
                    flash("User role updated successfully.", "success")
                else:
                    flash("That user account could not be updated.", "error")
            return redirect(url_for("app.admin_users_roles", **redirect_params))

        user_rows = list_staff(
            search=search,
            department=department,
            active_only=show_active_only,
            department_scope=department_scope,
        )
        return render_template(
            "admin/users_roles.html",
            title="Users & Roles",
            user_rows=user_rows,
            role_rows=_live_role_rows(user_rows),
            access_role_choices=ACCESS_ROLE_CHOICES,
            user_stats=_users_role_stats(user_rows),
            departments=list_departments(department_scope=department_scope),
            search=search,
            selected_department=department,
            active_only=show_active_only,
            admin_security=get_admin_security(default_username=current_app.config["APP_SETTINGS"].admin_username),
            **_admin_context("Users & Roles", "users", ["Dashboard", "Users & Roles"]),
        )

    @bp.route("/admin/audit-logs")
    @roles_required(*SETTINGS_ROLES)
    def admin_audit_logs():
        active_group = request.args.get("group", "all").strip().lower()
        if active_group not in {"all", "staff", "users"}:
            active_group = "all"
        staff_audit_rows = _selfie_audit_rows(list_staff_selfie_audits(limit=120))
        user_activity_rows = _admin_activity_rows(list_admin_activity_logs(limit=120))
        return render_template(
            "admin/audit_logs.html",
            title="Audit Logs",
            active_group=active_group,
            staff_audit_rows=staff_audit_rows,
            user_activity_rows=user_activity_rows,
            **_admin_context("Audit Logs", "audit", ["Dashboard", "Audit Logs"]),
        )

    @bp.route("/admin/reports")
    @roles_required(*REPORTING_ROLES)
    def admin_reports():
        app_settings = get_app_settings(
            default_app_name=_tenant_default_app_name()
        )
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        if not date_from and not date_to:
            end_date = date.today()
            days = max(1, int(app_settings["report_default_range_days"]))
            start_date = end_date - timedelta(days=days - 1)
            date_from = start_date.isoformat()
            date_to = end_date.isoformat()

        department_scope = current_department_scope()
        department = _resolve_department_filter(request.args.get("department", "").strip(), department_scope)
        search = request.args.get("search", "").strip()
        rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=department,
            search=search,
            department_scope=department_scope,
        )
        snapshot = build_report_snapshot(
            rows=rows,
            active_staff_total=count_active_staff(department_scope=department_scope),
        )
        activity_rows = _attendance_activity_rows(rows)
        detail_rows = _report_detail_rows_from_activity(
            activity_rows,
            date_from=date_from,
            date_to=date_to,
        )
        return render_template(
            "admin/reports.html",
            title="Attendance Reports",
            rows=rows,
            summary=report_summary(rows),
            report_snapshot=snapshot,
            report_model=_report_live_model(
                snapshot=snapshot,
                detail_rows=detail_rows,
                active_staff_total=count_active_staff(department_scope=department_scope),
                labels=_date_labels_from_range(date_from, date_to),
            ),
            detail_rows=detail_rows,
            departments=list_departments(department_scope=department_scope),
            filters={
                "date_from": date_from,
                "date_to": date_to,
                "department": department,
                "search": search,
            },
            app_settings=app_settings,
            **_admin_context("Reports", "reports", ["Dashboard", "Reports", "Attendance Report"], nav_secondary="attendance"),
        )

    @bp.route("/admin/reports/export.csv")
    @roles_required(*REPORTING_ROLES)
    def admin_reports_export():
        app_settings = get_app_settings(
            default_app_name=_tenant_default_app_name()
        )
        date_from = request.args.get("date_from", "").strip()
        date_to = request.args.get("date_to", "").strip()
        if not date_from and not date_to:
            end_date = date.today()
            days = max(1, int(app_settings["report_default_range_days"]))
            start_date = end_date - timedelta(days=days - 1)
            date_from = start_date.isoformat()
            date_to = end_date.isoformat()

        department_scope = current_department_scope()
        rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=_resolve_department_filter(request.args.get("department", "").strip(), department_scope),
            search=request.args.get("search", "").strip(),
            department_scope=department_scope,
        )
        snapshot = build_report_snapshot(
            rows=rows,
            active_staff_total=count_active_staff(department_scope=department_scope),
        )
        payload = report_snapshot_to_csv(snapshot)
        return Response(
            payload,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=attendance-summary-report.csv"},
        )

    @bp.route("/admin/attendance/export.csv")
    @roles_required(*REPORTING_ROLES)
    def admin_attendance_export():
        department_scope = current_department_scope()
        rows = list_attendance_events(
            date_from=request.args.get("date_from", ""),
            date_to=request.args.get("date_to", ""),
            department=_resolve_department_filter(request.args.get("department", ""), department_scope),
            search=request.args.get("search", "").strip(),
            department_scope=department_scope,
        )
        payload = attendance_rows_to_csv(rows)
        return Response(
            payload,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=attendance-report.csv"},
        )

    @bp.route("/admin/settings", methods=["GET", "POST"])
    @roles_required(*SETTINGS_ROLES)
    def admin_settings():
        app_defaults = get_app_settings(
            default_app_name=_tenant_default_app_name()
        )
        form_values = dict(app_defaults)
        admin_security = get_admin_security(
            default_username=current_app.config["APP_SETTINGS"].admin_username
        )
        password_form = {
            "current_password": "",
            "new_password": "",
            "confirm_password": "",
        }

        if request.method == "POST":
            form_name = request.form.get("form_name", "attendance_settings").strip()
            if form_name == "admin_password":
                password_form = _read_admin_password_form(request.form)
                validation_error = _validate_admin_password_form(password_form)
                if validation_error:
                    flash(validation_error, "error")
                elif not admin_password_matches(
                    password_form["current_password"],
                    current_app.config["APP_SETTINGS"].admin_password,
                ):
                    flash("Current admin password is incorrect.", "error")
                else:
                    save_admin_password(password_form["new_password"])
                    admin_security = get_admin_security(
                        default_username=current_app.config["APP_SETTINGS"].admin_username
                    )
                    log_admin_activity(
                        actor_type="user",
                        actor_name=current_display_name(),
                        actor_role=current_access_role() or "Institution Admin",
                        event_type="admin_password_changed",
                        details="Institution administrator password was updated from Settings.",
                        ip_address=_request_ip_address(),
                        device_name=_request_device_name(),
                    )
                    password_form = {
                        "current_password": "",
                        "new_password": "",
                        "confirm_password": "",
                    }
                    flash("Admin password changed successfully.", "success")
            else:
                form_values = _read_settings_form(request.form)
                form_values["system_logo_filename"] = app_defaults.get("system_logo_filename", "")
                validation_error = _validate_settings_form(form_values)
                if validation_error:
                    flash(validation_error, "error")
                else:
                    previous_logo_filename = app_defaults.get("system_logo_filename")
                    saved_logo_filename = None
                    try:
                        saved_logo_filename = _store_system_logo_upload(request.files.get("system_logo"))
                        if saved_logo_filename:
                            form_values["system_logo_filename"] = saved_logo_filename
                        elif request.form.get("remove_system_logo") == "on":
                            form_values["system_logo_filename"] = ""
                        else:
                            form_values["system_logo_filename"] = previous_logo_filename or ""
                        form_values = save_app_settings(
                            form_values,
                            default_app_name=_tenant_default_app_name(),
                        )
                    except ValueError as exc:
                        if saved_logo_filename:
                            _delete_system_logo(saved_logo_filename)
                        flash(str(exc), "error")
                    else:
                        if saved_logo_filename and previous_logo_filename and previous_logo_filename != saved_logo_filename:
                            _delete_system_logo(previous_logo_filename)
                        elif request.form.get("remove_system_logo") == "on" and previous_logo_filename:
                            _delete_system_logo(previous_logo_filename)
                        log_admin_activity(
                            actor_type="user",
                            actor_name=current_display_name(),
                            actor_role=current_access_role() or "Institution Admin",
                            event_type="attendance_settings_saved",
                            details="Organization settings, branding, or location policy were updated.",
                            ip_address=_request_ip_address(),
                            device_name=_request_device_name(),
                        )
                        flash("Attendance settings saved successfully.", "success")

        return render_template(
            "admin/settings.html",
            title="Settings",
            form_values=form_values,
            workday_options=WORKDAY_OPTIONS,
            admin_security=admin_security,
            password_form=password_form,
            location_policy=_location_policy_view_model(app_defaults),
            **_admin_context("Settings", "settings", ["Dashboard", "Settings"]),
        )

    @bp.route("/admin/seed-demo", methods=["POST"])
    @roles_required(*SETTINGS_ROLES)
    def admin_seed_demo():
        created = seed_demo_data()
        if created:
            flash("Demo data loaded successfully.", "success")
        else:
            flash("Demo data was skipped because staff records already exist.", "warning")
        return redirect(url_for("app.admin_dashboard"))

    @bp.route("/staff")
    @staff_required
    def staff_root():
        return redirect(url_for("app.staff_home"))

    @bp.route("/staff/home")
    @staff_required
    def staff_home():
        staff = get_staff(session["staff_id"])
        if not staff:
            clear_user_session()
            flash("Your staff record is no longer active.", "warning")
            return redirect(url_for("app.staff_login"))

        quick_url = request.url_root.rstrip("/") + url_for("app.staff_quick_access", qr_token=staff["qr_token"])
        today_status = get_staff_today_status(staff["id"])
        location_policy = _location_policy_view_model(
            get_app_settings(default_app_name=_tenant_default_app_name())
        )
        return render_template(
            "staff/home.html",
            title="Staff Portal",
            staff=staff,
            today=date.today(),
            today_status=today_status,
            quick_url=quick_url,
            mobile_qr_svg=build_qr_svg(quick_url),
            location_policy=location_policy,
            body_class="staff-mobile-app-body",
        )

    @bp.route("/staff/clock", methods=["POST"])
    @staff_required
    def staff_clock():
        staff = get_staff(session["staff_id"])
        if not staff:
            clear_user_session()
            flash("Your staff record is no longer active.", "warning")
            return redirect(url_for("app.staff_login"))
        if not staff.get("allow_mobile_clock"):
            flash("Mobile clocking is disabled for your account.", "warning")
            return redirect(url_for("app.staff_home"))

        action = request.form.get("action", "").strip()
        today_status = get_staff_today_status(staff["id"])
        if action not in {"check_in", "break_start", "break_end", "check_out"}:
            flash("Choose a valid attendance action.", "error")
            return redirect(url_for("app.staff_home"))
        if action not in today_status["next_actions"]:
            flash("That action is not available right now based on today's attendance state.", "warning")
            return redirect(url_for("app.staff_home"))

        latitude = _coerce_float(request.form.get("latitude"))
        longitude = _coerce_float(request.form.get("longitude"))
        gps_accuracy = _coerce_float(request.form.get("gps_accuracy"))
        notes = request.form.get("notes", "").strip()
        location_error = _attendance_location_error(
            latitude=latitude,
            longitude=longitude,
            gps_accuracy=gps_accuracy,
        )
        if location_error:
            flash(location_error, "error")
            return redirect(url_for("app.staff_home"))
        result = record_attendance(
            staff=staff,
            template_ref=staff["staff_code"],
            confidence=None,
            method="mobile_gps" if latitude is not None and longitude is not None else "mobile_web",
            device_name=_request_device_name(),
            notes=notes,
            event_type=action,
            latitude=latitude,
            longitude=longitude,
            gps_accuracy=gps_accuracy,
        )
        flash(
            f"{result['event_type'].replace('_', ' ').title()} recorded successfully.",
            "success",
        )
        return redirect(url_for("app.staff_home"))

    @bp.route("/staff/quick/<qr_token>", methods=["GET", "POST"])
    def staff_quick_access(qr_token: str):
        staff = get_staff_by_qr_token(_normalize_qr_value(qr_token))
        if not staff or not staff.get("allow_qr_clock"):
            flash("That QR access link is not available.", "error")
            return redirect(url_for("app.kiosk"))

        today_status = get_staff_today_status(staff["id"])
        location_policy = _location_policy_view_model(
            get_app_settings(default_app_name=_tenant_default_app_name())
        )
        if request.method == "POST":
            action = request.form.get("action", "").strip()
            if action not in today_status["next_actions"]:
                flash("That QR action is not available right now.", "warning")
                return redirect(url_for("app.staff_quick_access", qr_token=qr_token))

            latitude = _coerce_float(request.form.get("latitude"))
            longitude = _coerce_float(request.form.get("longitude"))
            gps_accuracy = _coerce_float(request.form.get("gps_accuracy"))
            location_error = _attendance_location_error(
                latitude=latitude,
                longitude=longitude,
                gps_accuracy=gps_accuracy,
            )
            if location_error:
                flash(location_error, "error")
                return redirect(url_for("app.staff_quick_access", qr_token=qr_token))
            result = record_attendance(
                staff=staff,
                template_ref=staff["staff_code"],
                confidence=None,
                method="qr_mobile",
                device_name=_request_device_name(),
                notes="QR quick access",
                event_type=action,
                latitude=latitude,
                longitude=longitude,
                gps_accuracy=gps_accuracy,
            )
            flash(
                f"{result['staff_name']} recorded a {result['event_type'].replace('_', ' ')} successfully.",
                "success",
            )
            return redirect(url_for("app.staff_quick_access", qr_token=qr_token))

        return render_template(
            "staff/quick_access.html",
            title="QR Quick Access",
            staff=staff,
            today_status=today_status,
            today=date.today(),
            location_policy=location_policy,
            body_class="staff-mobile-app-body",
        )

    app.register_blueprint(bp)


def _store_last_kiosk_result(result: dict[str, Any]) -> None:
    session["last_kiosk_result"] = {
        "staff_code": result["staff_code"],
        "staff_first_name": result["staff_first_name"],
        "staff_last_name": result["staff_last_name"],
        "staff_name": result["staff_name"],
        "department": result["department"],
        "role": result["role"],
        "photo_filename": result.get("photo_filename") or "",
        "event_type": result["event_type"],
        "status_label": result["status_label"],
        "event_time": result["event_time"].strftime("%I:%M %p"),
    }


def _store_ghana_card_verification_result(
    staff: dict[str, Any],
    *,
    confidence: int | None,
    backend: str,
    verified_at: str,
) -> None:
    session["last_ghana_card_verification"] = {
        "staff_id": int(staff["id"]),
        "staff_code": staff["staff_code"],
        "staff_name": f"{staff['first_name']} {staff['last_name']}",
        "photo_url": _photo_url_for_filename(staff.get("photo_filename")),
        "ghana_card_number": staff.get("ghana_card_number", ""),
        "backend": backend,
        "confidence": confidence,
        "verified_at": _display_ghana_datetime(verified_at),
    }


def _staff_form_defaults(staff: dict[str, Any] | None = None) -> dict[str, Any]:
    if staff:
        return {
            "staff_code": staff["staff_code"],
            "first_name": staff["first_name"],
            "last_name": staff["last_name"],
            "email": staff["email"] or "",
            "phone": staff.get("phone") or "",
            "photo_filename": staff.get("photo_filename") or "",
            "ghana_card_number": staff.get("ghana_card_number") or "",
            "nationality": staff.get("nationality") or "Ghanaian",
            "sex": staff.get("sex") or "",
            "date_of_birth": staff.get("date_of_birth") or "",
            "place_of_birth": staff.get("place_of_birth") or "",
            "residential_address": staff.get("residential_address") or "",
            "digital_address": staff.get("digital_address") or "",
            "department": staff["department"],
            "role": staff["role"],
            "access_role": staff.get("access_role", STAFF),
            "shift_start": staff["shift_start"],
            "shift_end": staff["shift_end"],
            "shift_preset_key": _match_shift_preset_key(
                staff.get("shift_start"),
                staff.get("shift_end"),
            ),
            "grace_minutes": staff["grace_minutes"],
            "is_active": bool(staff["is_active"]),
            "allow_mobile_clock": bool(staff.get("allow_mobile_clock", 1)),
            "allow_pin_clock": bool(staff.get("allow_pin_clock", 1)),
            "allow_qr_clock": bool(staff.get("allow_qr_clock", 1)),
            "portal_password": "",
            "portal_pin": "",
            "regenerate_qr": False,
            "qr_token": staff.get("qr_token", ""),
        }

    app_defaults = get_app_settings(
        default_app_name=_tenant_default_app_name()
    )
    return {
        "staff_code": "",
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "photo_filename": "",
        "ghana_card_number": "",
        "nationality": "Ghanaian",
        "sex": "",
        "date_of_birth": "",
        "place_of_birth": "",
        "residential_address": "",
        "digital_address": "",
        "department": "",
        "role": "",
        "access_role": STAFF,
        "shift_start": app_defaults["default_shift_start"],
        "shift_end": app_defaults["default_shift_end"],
        "shift_preset_key": _match_shift_preset_key(
            app_defaults["default_shift_start"],
            app_defaults["default_shift_end"],
        ),
        "grace_minutes": app_defaults["default_grace_minutes"],
        "is_active": True,
        "allow_mobile_clock": True,
        "allow_pin_clock": True,
        "allow_qr_clock": True,
        "portal_password": "",
        "portal_pin": "",
        "regenerate_qr": False,
        "qr_token": "",
    }


def _read_staff_form(form) -> dict[str, Any]:
    grace_minutes = form.get("grace_minutes", "15").strip() or "15"
    return {
        "staff_code": form.get("staff_code", "").strip().upper(),
        "first_name": form.get("first_name", "").strip(),
        "last_name": form.get("last_name", "").strip(),
        "email": form.get("email", "").strip(),
        "phone": form.get("phone", "").strip(),
        "ghana_card_number": form.get("ghana_card_number", "").strip().upper(),
        "nationality": form.get("nationality", "").strip(),
        "sex": form.get("sex", "").strip(),
        "date_of_birth": form.get("date_of_birth", "").strip(),
        "place_of_birth": form.get("place_of_birth", "").strip(),
        "residential_address": form.get("residential_address", "").strip(),
        "digital_address": form.get("digital_address", "").strip().upper(),
        "department": form.get("department", "").strip(),
        "role": form.get("role", "").strip(),
        "access_role": form.get("access_role", STAFF).strip(),
        "shift_start": form.get("shift_start", "09:00"),
        "shift_end": form.get("shift_end", "17:00"),
        "shift_preset_key": form.get("shift_preset_key", "").strip(),
        "grace_minutes": int(grace_minutes),
        "is_active": form.get("is_active") == "on",
        "allow_mobile_clock": form.get("allow_mobile_clock") == "on",
        "allow_pin_clock": form.get("allow_pin_clock") == "on",
        "allow_qr_clock": form.get("allow_qr_clock") == "on",
        "portal_password": form.get("portal_password", ""),
        "portal_pin": form.get("portal_pin", "").strip(),
        "regenerate_qr": form.get("regenerate_qr") == "on",
    }


def _validate_staff_form(form_values: dict[str, Any], creating: bool) -> str | None:
    for key in ("staff_code", "first_name", "last_name", "department", "role"):
        if not form_values.get(key):
            return "Staff code, first name, last name, department, and job title are required."

    if form_values.get("access_role") not in ACCESS_ROLE_CHOICES:
        return "Choose a valid platform access role."

    if not form_values.get("shift_start") or not form_values.get("shift_end"):
        return "Shift start and shift end are required."
    try:
        time.fromisoformat(str(form_values.get("shift_start")))
        time.fromisoformat(str(form_values.get("shift_end")))
    except ValueError:
        return "Shift start and shift end must use valid time values."

    pin_value = str(form_values.get("portal_pin", ""))
    if pin_value and (not pin_value.isdigit() or len(pin_value) < 4):
        return "PIN must be numeric and at least 4 digits long."

    date_of_birth = str(form_values.get("date_of_birth", "")).strip()
    if date_of_birth:
        try:
            date.fromisoformat(date_of_birth)
        except ValueError:
            return "Date of birth must use a valid date."

    sex = str(form_values.get("sex", "")).strip()
    if sex and sex not in {"Male", "Female"}:
        return "Choose a valid sex value for Ghana Card details."

    if creating and not form_values.get("allow_qr_clock") and not form_values.get("portal_password") and not pin_value:
        return "Provide a password, PIN, or enable QR access so the staff member can use the system."

    return None


def _store_staff_photo_upload(upload: FileStorage | None) -> str | None:
    if upload is None or not upload.filename:
        return None

    original_name = secure_filename(upload.filename)
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in STAFF_PHOTO_EXTENSIONS:
        raise ValueError("Upload a JPG, PNG, or WEBP image for the staff photo.")

    mimetype = (upload.mimetype or "").lower()
    if mimetype and (not mimetype.startswith("image/") or mimetype == "image/svg+xml"):
        raise ValueError("The uploaded file must be a JPG, PNG, or WEBP image.")

    current_position = upload.stream.tell()
    upload.stream.seek(0, 2)
    file_size = upload.stream.tell()
    upload.stream.seek(current_position)
    if file_size > MAX_STAFF_PHOTO_BYTES:
        raise ValueError("Staff photo must be 4 MB or smaller.")

    filename = f"{uuid4().hex}{suffix}"
    destination = _staff_photo_directory() / filename
    upload.save(destination)
    return filename


def _store_system_logo_upload(upload: FileStorage | None) -> str | None:
    if upload is None or not upload.filename:
        return None

    original_name = secure_filename(upload.filename)
    suffix = Path(original_name).suffix.lower()
    if not original_name or suffix not in SYSTEM_LOGO_EXTENSIONS:
        raise ValueError("Upload a JPG, PNG, or WEBP image for the system logo.")

    mimetype = (upload.mimetype or "").lower()
    if mimetype and (not mimetype.startswith("image/") or mimetype == "image/svg+xml"):
        raise ValueError("Only standard raster image logo formats are supported.")

    upload.stream.seek(0, 2)
    size = upload.stream.tell()
    upload.stream.seek(0)
    if size > MAX_SYSTEM_LOGO_BYTES:
        raise ValueError("System logo must be 3 MB or smaller.")

    filename = f"system-logo-{uuid4().hex}{suffix}"
    destination = _system_logo_directory() / filename
    upload.save(destination)
    return filename


def _store_login_selfie_capture(data_url: str) -> dict[str, Any]:
    if not data_url:
        raise ValueError("Capture a selfie before signing in.")
    if not data_url.startswith("data:") or ";base64," not in data_url:
        raise ValueError("The selfie capture format is invalid. Refresh the page and try again.")

    header, encoded = data_url.split(";base64,", 1)
    mime_type = header.replace("data:", "", 1).strip().lower()
    suffix = AUDIT_SELFIE_MIME_TYPES.get(mime_type)
    if not suffix:
        raise ValueError("Only JPG, PNG, or WEBP selfie captures are supported.")

    try:
        payload = b64decode(encoded, validate=True)
    except Exception as exc:  # pragma: no cover - defensive decode guard
        raise ValueError("The selfie capture could not be decoded. Please retake the photo.") from exc

    if not payload:
        raise ValueError("The selfie capture was empty. Please retake the photo.")
    if len(payload) > MAX_AUDIT_SELFIE_BYTES:
        raise ValueError("The captured selfie is too large. Retake it and try again.")

    filename = f"login-selfie-{uuid4().hex}{suffix}"
    destination = _audit_selfie_directory() / filename
    destination.write_bytes(payload)
    return {
        "filename": filename,
        "mime_type": mime_type,
        "file_size_bytes": len(payload),
    }


def _staff_photo_directory() -> Path:
    directory = _tenant_instance_dir() / "staff_photos"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _system_logo_directory() -> Path:
    directory = _tenant_instance_dir() / "system_branding"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _audit_selfie_directory() -> Path:
    directory = _tenant_instance_dir() / "staff_selfie_audits"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _tenant_instance_dir() -> Path:
    return get_current_organization().instance_dir


def _tenant_default_app_name() -> str:
    organization = get_current_organization()
    return organization.display_name or current_app.config["APP_SETTINGS"].app_name


def _delete_staff_photo(filename: str | None) -> None:
    if not filename:
        return
    photo_path = _staff_photo_directory() / filename
    if photo_path.exists():
        photo_path.unlink()


def _delete_system_logo(filename: str | None) -> None:
    if not filename:
        return
    logo_path = _system_logo_directory() / filename
    if logo_path.exists():
        logo_path.unlink()


def _system_logo_url_for_filename(filename: str | None) -> str:
    if filename:
        return url_for("app.system_logo", filename=filename)
    return url_for("static", filename=DEFAULT_LOGO_MARK_STATIC)


def _audit_selfie_url_for_filename(filename: str | None) -> str:
    if not filename:
        return ""
    return url_for("app.audit_selfie_image", filename=filename)


def _read_settings_form(form) -> dict[str, Any]:
    return {
        "organization_name": form.get("organization_name", "").strip(),
        "default_shift_start": form.get("default_shift_start", "09:00"),
        "default_shift_end": form.get("default_shift_end", "17:00"),
        "default_grace_minutes": form.get("default_grace_minutes", "15").strip() or "15",
        "report_default_range_days": form.get("report_default_range_days", "30").strip() or "30",
        "working_days": form.getlist("working_days"),
        "location_enforcement_enabled": form.get("location_enforcement_enabled") == "on",
        "allowed_location_name": form.get("allowed_location_name", "").strip(),
        "allowed_location_address": form.get("allowed_location_address", "").strip(),
        "allowed_location_latitude": form.get("allowed_location_latitude", "").strip(),
        "allowed_location_longitude": form.get("allowed_location_longitude", "").strip(),
        "allowed_location_radius_meters": form.get("allowed_location_radius_meters", "150").strip() or "150",
    }


def _read_admin_password_form(form) -> dict[str, str]:
    return {
        "current_password": form.get("current_password", ""),
        "new_password": form.get("new_password", ""),
        "confirm_password": form.get("confirm_password", ""),
    }


def _validate_settings_form(form_values: dict[str, Any]) -> str | None:
    if not form_values.get("default_shift_start") or not form_values.get("default_shift_end"):
        return "Default shift start and end times are required."

    try:
        grace_minutes = int(str(form_values.get("default_grace_minutes", "15")))
    except ValueError:
        return "Default grace minutes must be a whole number."
    if grace_minutes < 0:
        return "Default grace minutes cannot be negative."

    try:
        report_days = int(str(form_values.get("report_default_range_days", "30")))
    except ValueError:
        return "Default report range must be a whole number of days."
    if report_days < 1:
        return "Default report range must be at least one day."

    if not form_values.get("working_days"):
        return "Select at least one working day."

    location_enforcement_enabled = bool(form_values.get("location_enforcement_enabled"))
    latitude_raw = str(form_values.get("allowed_location_latitude", "")).strip()
    longitude_raw = str(form_values.get("allowed_location_longitude", "")).strip()
    radius_raw = str(form_values.get("allowed_location_radius_meters", "150")).strip()
    if location_enforcement_enabled:
        if not latitude_raw or not longitude_raw:
            return "Enter the allowed work location latitude and longitude before enabling location restriction."
        try:
            latitude = float(latitude_raw)
            longitude = float(longitude_raw)
        except ValueError:
            return "Allowed work location latitude and longitude must be valid numbers."
        if latitude < -90 or latitude > 90:
            return "Allowed work location latitude must be between -90 and 90."
        if longitude < -180 or longitude > 180:
            return "Allowed work location longitude must be between -180 and 180."
        try:
            radius = int(radius_raw)
        except ValueError:
            return "Allowed work location radius must be a whole number of meters."
        if radius < 25:
            return "Allowed work location radius must be at least 25 meters."
    return None


def _validate_admin_password_form(form_values: dict[str, str]) -> str | None:
    current_password = form_values.get("current_password", "")
    new_password = form_values.get("new_password", "")
    confirm_password = form_values.get("confirm_password", "")

    if not current_password:
        return "Enter the current admin password."
    if len(new_password) < 8:
        return "New admin password must be at least 8 characters long."
    if not any(char.islower() for char in new_password):
        return "New admin password must include a lowercase letter."
    if not any(char.isupper() for char in new_password):
        return "New admin password must include an uppercase letter."
    if not any(char.isdigit() for char in new_password):
        return "New admin password must include a number."
    if not any(not char.isalnum() for char in new_password):
        return "New admin password must include a symbol."
    if new_password != confirm_password:
        return "New admin password and confirmation do not match."
    if current_password == new_password:
        return "Choose a new admin password different from the current one."
    return None


def _decode_template_data(value: object) -> bytes | None:
    if not value:
        return None
    return b64decode(str(value))


def _coerce_float(value: str | None) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _request_device_name() -> str:
    user_agent = request.headers.get("User-Agent", "web")
    return user_agent[:120]


def _request_ip_address() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("X-Real-IP", "").strip() or (request.remote_addr or "")


def _normalize_qr_value(raw_value: str) -> str:
    value = raw_value.strip()
    if value.startswith("ATTENDANCE|"):
        return value.split("|", 1)[1]
    if "/staff/quick/" in value:
        return value.rsplit("/", 1)[-1]
    return value


def _location_policy_view_model(app_defaults: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(app_defaults.get("location_enforcement_enabled"))
    latitude = app_defaults.get("allowed_location_latitude")
    longitude = app_defaults.get("allowed_location_longitude")
    radius = int(app_defaults.get("allowed_location_radius_meters", 150) or 150)
    location_name = str(app_defaults.get("allowed_location_name", "") or "").strip()
    return {
        "enabled": enabled,
        "location_name": location_name or "Main Work Location",
        "address": str(app_defaults.get("allowed_location_address", "") or "").strip(),
        "latitude": latitude,
        "longitude": longitude,
        "radius_meters": radius,
        "is_configured": latitude is not None and longitude is not None,
        "summary": _location_policy_summary(
            enabled=enabled,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius,
            location_name=location_name,
            address=str(app_defaults.get("allowed_location_address", "") or "").strip(),
        ),
    }


def _location_policy_summary(
    *,
    enabled: bool,
    latitude: float | None,
    longitude: float | None,
    radius_meters: int,
    location_name: str,
    address: str,
) -> str:
    if not enabled:
        return "Staff can clock from any location."
    name = location_name or "Main Work Location"
    if latitude is None or longitude is None:
        return f"Location restriction is enabled, but {name} has not been configured yet."
    if address:
        return f"Staff must be within {radius_meters} meters of {name} at {address} to clock in or out."
    return f"Staff must be within {radius_meters} meters of {name} to clock in or out."


def _attendance_location_error(
    *,
    latitude: float | None,
    longitude: float | None,
    gps_accuracy: float | None,
) -> str | None:
    app_defaults = get_app_settings(default_app_name=_tenant_default_app_name())
    policy = _location_policy_view_model(app_defaults)
    if not policy["enabled"]:
        return None
    if not policy["is_configured"]:
        return "Work-location restriction is enabled, but the allowed location is not configured yet. Contact your administrator."
    if latitude is None or longitude is None:
        return (
            f"Location access is required. Allow GPS and try again while you are near "
            f"{policy['location_name']}."
        )

    distance_meters = _distance_in_meters(
        latitude,
        longitude,
        float(policy["latitude"]),
        float(policy["longitude"]),
    )
    allowed_radius = float(policy["radius_meters"]) + max(gps_accuracy or 0.0, 0.0)
    if distance_meters <= allowed_radius:
        return None
    return (
        f"You are outside the allowed work location for {policy['location_name']}. "
        f"Move closer to the approved area and try again."
    )


def _distance_in_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    earth_radius_meters = 6371000.0
    lat1 = math.radians(latitude_a)
    lon1 = math.radians(longitude_a)
    lat2 = math.radians(latitude_b)
    lon2 = math.radians(longitude_b)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    sin_lat = math.sin(delta_lat / 2.0)
    sin_lon = math.sin(delta_lon / 2.0)
    haversine = sin_lat ** 2 + math.cos(lat1) * math.cos(lat2) * sin_lon ** 2
    central_angle = 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))
    return earth_radius_meters * central_angle


def _pwa_short_name(app_name: str) -> str:
    cleaned = " ".join(app_name.split())
    if len(cleaned) <= 12:
        return cleaned
    first_word = cleaned.split(" ", 1)[0]
    return first_word[:12]


def _generate_pwa_icon_png(*, size: int) -> bytes:
    source_path = _pwa_logo_source_path()
    if source_path is not None:
        try:
            return _generate_logo_based_pwa_icon_png(source_path=source_path, size=size)
        except Exception:
            pass
    return _generate_fallback_pwa_icon_png(size=size)


def _generate_logo_based_pwa_icon_png(*, source_path: Path, size: int) -> bytes:
    with Image.open(source_path) as source_image:
        image = source_image.convert("RGBA")
        image = _trim_logo_whitespace(image)
        canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        padded_size = max(1, size - int(size * 0.26))
        logo = image.copy()
        logo.thumbnail((padded_size, padded_size), Image.Resampling.LANCZOS)
        offset = ((size - logo.width) // 2, (size - logo.height) // 2)
        canvas.paste(logo, offset, logo)

    buffer = BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


def _trim_logo_whitespace(image: Image.Image) -> Image.Image:
    pixels = image.load()
    min_x = image.width
    min_y = image.height
    max_x = 0
    max_y = 0
    found = False

    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha > 0 and (red < 245 or green < 245 or blue < 245):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                found = True

    if not found:
        return image

    padding = max(4, int(min(image.size) * 0.04))
    return image.crop(
        (
            max(0, min_x - padding),
            max(0, min_y - padding),
            min(image.width, max_x + padding),
            min(image.height, max_y + padding),
        )
    )


def _pwa_logo_source_path() -> Path | None:
    live_settings = get_app_settings(default_app_name=_tenant_default_app_name())
    filename = str(live_settings.get("system_logo_filename", "") or "").strip()
    if filename:
        candidate = _system_logo_directory() / filename
        if candidate.exists():
            return candidate

    default_candidate = _default_logo_mark_path()
    if default_candidate.exists():
        return default_candidate
    return None


def _default_logo_mark_path() -> Path:
    return Path(current_app.static_folder or "") / DEFAULT_LOGO_MARK_STATIC


def _default_logo_full_path() -> Path:
    return Path(current_app.static_folder or "") / DEFAULT_LOGO_FULL_STATIC


def _generate_fallback_pwa_icon_png(*, size: int) -> bytes:
    background = (17, 22, 31, 255)
    accent = (47, 107, 255, 255)
    highlight = (78, 219, 12, 255)
    white = (245, 248, 252, 255)
    shadow = (22, 33, 58, 255)

    center = size / 2
    outer_radius = size * 0.34
    inner_radius = size * 0.21
    dot_radius = size * 0.055
    dot_center_x = center + size * 0.13
    dot_center_y = center + size * 0.13
    ring_thickness = max(10.0, size * 0.042)

    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            pixel = background
            distance = math.dist((x, y), (center, center))
            if distance <= outer_radius:
                pixel = accent
            if distance <= outer_radius - ring_thickness:
                pixel = shadow
            if distance <= inner_radius:
                pixel = white
            if math.dist((x, y), (dot_center_x, dot_center_y)) <= dot_radius:
                pixel = highlight

            rows.extend(pixel)

    compressed = zlib.compress(bytes(rows), level=9)

    def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + chunk_type
            + payload
            + struct.pack(">I", zlib.crc32(chunk_type + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            png_chunk(b"IHDR", ihdr),
            png_chunk(b"IDAT", compressed),
            png_chunk(b"IEND", b""),
        ]
    )


def _resolve_department_filter(requested_department: str, department_scope: str) -> str:
    if department_scope:
        return department_scope
    return requested_department.strip()


def _get_manageable_staff(staff_id: int) -> dict[str, Any] | None:
    staff = get_staff(staff_id, department_scope=current_department_scope())
    if not staff:
        flash("Staff member not found or outside your department scope.", "warning")
        return None
    if current_access_role() == DEPARTMENT_MANAGER and staff.get("access_role") == SUPER_ADMIN:
        flash("Department managers cannot modify super-admin records.", "warning")
        return None
    return staff


def _admin_context(
    page_title: str,
    nav_primary: str,
    breadcrumbs: list[str],
    nav_secondary: str = "",
    body_class: str = "",
) -> dict[str, Any]:
    notification_rows = _build_admin_notification_rows(limit=8)
    return {
        "page_title": page_title,
        "nav_primary": nav_primary,
        "nav_secondary": nav_secondary,
        "breadcrumbs": breadcrumbs,
        "body_class": body_class,
        "admin_notification_count": len(notification_rows),
        "admin_notification_rows": notification_rows,
        "admin_now_iso": datetime.now().isoformat(timespec="seconds"),
    }


def _platform_context(
    page_title: str,
    nav_primary: str,
    breadcrumbs: list[str],
    body_class: str = "",
) -> dict[str, Any]:
    return {
        "page_title": page_title,
        "nav_primary": nav_primary,
        "breadcrumbs": breadcrumbs,
        "body_class": body_class,
    }


def _platform_organization_rows(
    organizations: list[Any],
    *,
    update_forms: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    update_forms = update_forms or {}
    for organization in organizations:
        hostnames = list(organization.hostnames)
        form = update_forms.get(organization.slug) or {
            "slug": organization.slug,
            "display_name": organization.display_name,
            "hostnames": "\n".join(hostnames),
            "hostnames_list": hostnames,
            "is_default": organization.is_default,
            "admin_password": "",
            "confirm_admin_password": "",
            "plan_name": organization.plan_name,
            "license_status": organization.license_status,
            "expires_on": organization.expires_on,
            "billing_contact_name": organization.billing_contact_name,
            "billing_email": organization.billing_email,
            "billing_phone": organization.billing_phone,
            "billing_cycle": organization.billing_cycle,
            "subscription_amount": f"{organization.subscription_amount:.2f}",
            "renewal_due_on": organization.renewal_due_on,
            "last_payment_on": organization.last_payment_on,
            "license_notes": organization.license_notes,
        }
        primary_url = ""
        if hostnames:
            primary_url = f"https://{hostnames[0]}"
        access_state = get_organization_access_state(organization)
        backups = list_organization_backups(organization, limit=6)
        rows.append(
            {
                "slug": organization.slug,
                "display_name": organization.display_name,
                "is_default": organization.is_default,
                "database_path": str(organization.database_path),
                "instance_dir": str(organization.instance_dir),
                "hostnames": hostnames,
                "primary_url": primary_url,
                "login_url": f"{primary_url}/admin/login" if primary_url else "",
                "form": form,
                "plan_name": organization.plan_name,
                "license_status": organization.license_status,
                "expires_on": organization.expires_on,
                "billing_contact_name": organization.billing_contact_name,
                "billing_email": organization.billing_email,
                "billing_phone": organization.billing_phone,
                "billing_cycle": organization.billing_cycle,
                "subscription_amount": organization.subscription_amount,
                "renewal_due_on": organization.renewal_due_on,
                "last_payment_on": organization.last_payment_on,
                "license_notes": organization.license_notes,
                "access_state": access_state,
                "backups": [
                    {
                        "name": snapshot.name,
                        "created_label": snapshot.created_label,
                        "size_label": snapshot.size_label,
                        "reason_label": snapshot.reason_label,
                        "download_url": url_for(
                            "app.platform_download_backup",
                            slug=organization.slug,
                            backup_name=snapshot.name,
                        ),
                    }
                    for snapshot in backups
                ],
                "latest_backup": backups[0] if backups else None,
            }
        )
    return rows


def _read_platform_organization_form(form) -> dict[str, Any]:
    hostnames_raw = str(form.get("hostnames", "") or "")
    hostnames_list = [
        line.strip()
        for line in hostnames_raw.replace(",", "\n").splitlines()
        if line.strip()
    ]
    return {
        "slug": str(form.get("slug", "") or "").strip(),
        "display_name": str(form.get("display_name", "") or "").strip(),
        "hostnames": hostnames_raw,
        "hostnames_list": hostnames_list,
        "is_default": form.get("is_default") in {"on", "true", "1", "yes"},
        "admin_password": str(form.get("admin_password", "") or ""),
        "confirm_admin_password": str(form.get("confirm_admin_password", "") or ""),
        "plan_name": str(form.get("plan_name", "") or "").strip() or "Standard",
        "license_status": str(form.get("license_status", LICENSE_STATUS_TRIAL) or "").strip().lower() or LICENSE_STATUS_TRIAL,
        "expires_on": str(form.get("expires_on", "") or "").strip(),
        "billing_contact_name": str(form.get("billing_contact_name", "") or "").strip(),
        "billing_email": str(form.get("billing_email", "") or "").strip(),
        "billing_phone": str(form.get("billing_phone", "") or "").strip(),
        "billing_cycle": str(form.get("billing_cycle", BILLING_CYCLE_MONTHLY) or "").strip().lower() or BILLING_CYCLE_MONTHLY,
        "subscription_amount": str(form.get("subscription_amount", "0.00") or "").strip() or "0.00",
        "renewal_due_on": str(form.get("renewal_due_on", "") or "").strip(),
        "last_payment_on": str(form.get("last_payment_on", "") or "").strip(),
        "license_notes": str(form.get("license_notes", "") or "").strip(),
    }


def _validate_platform_organization_form(
    form_values: dict[str, Any],
    *,
    creating: bool,
) -> str | None:
    slug = str(form_values.get("slug", "")).strip()
    if creating and not slug:
        return "Enter an organization slug."
    if creating and not all(char.isalnum() or char == "-" for char in slug.lower()):
        return "Use only letters, numbers, and dashes for the organization slug."
    if not str(form_values.get("display_name", "")).strip():
        return "Enter the institution name."
    hostnames = form_values.get("hostnames_list", [])
    if not hostnames:
        return "Enter at least one domain or subdomain for the institution."
    if not str(form_values.get("plan_name", "")).strip():
        return "Enter the subscription plan name."
    if form_values.get("license_status") not in {
        LICENSE_STATUS_ACTIVE,
        LICENSE_STATUS_TRIAL,
        LICENSE_STATUS_SUSPENDED,
        LICENSE_STATUS_EXPIRED,
    }:
        return "Choose a valid license status."
    if form_values.get("billing_cycle") not in {
        BILLING_CYCLE_MONTHLY,
        BILLING_CYCLE_QUARTERLY,
        BILLING_CYCLE_YEARLY,
        BILLING_CYCLE_MANUAL,
    }:
        return "Choose a valid billing cycle."
    expires_on = str(form_values.get("expires_on", "")).strip()
    renewal_due_on = str(form_values.get("renewal_due_on", "")).strip()
    last_payment_on = str(form_values.get("last_payment_on", "")).strip()
    for value, label in (
        (expires_on, "License expiry date"),
        (renewal_due_on, "Renewal due date"),
        (last_payment_on, "Last payment date"),
    ):
        if value and not _looks_like_iso_date(value):
            return f"{label} must use YYYY-MM-DD format."
    try:
        if str(form_values.get("subscription_amount", "")).strip():
            float(str(form_values.get("subscription_amount", "0")).strip())
    except ValueError:
        return "Subscription amount must be a valid number."
    admin_password = str(form_values.get("admin_password", ""))
    confirm_admin_password = str(form_values.get("confirm_admin_password", ""))
    if creating and not admin_password:
        return "Enter an initial institution admin password."
    if admin_password or confirm_admin_password:
        if len(admin_password) < 8:
            return "Institution admin password must be at least 8 characters."
        if admin_password != confirm_admin_password:
            return "Institution admin password confirmation does not match."
    return None


def _platform_license_status_options() -> list[dict[str, str]]:
    return [
        {"value": LICENSE_STATUS_ACTIVE, "label": "Active"},
        {"value": LICENSE_STATUS_TRIAL, "label": "Trial"},
        {"value": LICENSE_STATUS_SUSPENDED, "label": "Suspended"},
        {"value": LICENSE_STATUS_EXPIRED, "label": "Expired"},
    ]


def _platform_billing_cycle_options() -> list[dict[str, str]]:
    return [
        {"value": BILLING_CYCLE_MONTHLY, "label": "Monthly"},
        {"value": BILLING_CYCLE_QUARTERLY, "label": "Quarterly"},
        {"value": BILLING_CYCLE_YEARLY, "label": "Yearly"},
        {"value": BILLING_CYCLE_MANUAL, "label": "Manual"},
    ]


def _looks_like_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def access_state_summary(access_state: dict[str, Any]) -> dict[str, Any]:
    days_remaining = access_state.get("days_remaining")
    days_label = ""
    if days_remaining is not None:
        if days_remaining < 0:
            days_label = "Expired"
        elif days_remaining == 0:
            days_label = "Ends today"
        else:
            days_label = f"{days_remaining} day{'s' if days_remaining != 1 else ''} left"
    return {
        "status": access_state.get("status", LICENSE_STATUS_ACTIVE),
        "access_allowed": bool(access_state.get("access_allowed")),
        "expires_on": access_state.get("expires_on", ""),
        "days_remaining": days_remaining,
        "days_label": days_label,
        "reason": access_state.get("reason", ""),
    }


def _reference_people() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "role": "Software Engineer", "email": "john.doe@example.com", "phone": "+1 555-0101", "status": "Active"},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "role": "HR Manager", "email": "jane.smith@example.com", "phone": "+1 555-0102", "status": "Active"},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "role": "Accountant", "email": "michael.brown@example.com", "phone": "+1 555-0103", "status": "Active"},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "role": "Marketing Executive", "email": "emily.davis@example.com", "phone": "+1 555-0104", "status": "Active"},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "role": "Operations Manager", "email": "david.wilson@example.com", "phone": "+1 555-0105", "status": "Active"},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "role": "UI/UX Designer", "email": "sarah.johnson@example.com", "phone": "+1 555-0106", "status": "On Leave"},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "role": "Senior Accountant", "email": "robert.lee@example.com", "phone": "+1 555-0107", "status": "Active"},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "role": "HR Executive", "email": "linda.martinez@example.com", "phone": "+1 555-0108", "status": "Active"},
        {"staff_code": "STF009", "first_name": "James", "last_name": "Taylor", "department": "Operations Department", "role": "Team Lead", "email": "james.taylor@example.com", "phone": "+1 555-0109", "status": "Inactive"},
        {"staff_code": "STF010", "first_name": "Jessica", "last_name": "Anderson", "department": "Marketing Department", "role": "Content Specialist", "email": "jessica.anderson@example.com", "phone": "+1 555-0110", "status": "Active"},
    ]


def _chart_points(
    values: list[int],
    width: int = 520,
    height: int = 170,
    bottom_padding: int = 18,
    maximum: int | None = None,
    left_padding: int = 0,
    right_padding: int = 0,
    top_padding: int = 36,
) -> list[str]:
    if not values:
        return []
    scale_max = maximum or max(values) or 1
    plot_width = max(width - left_padding - right_padding, 1)
    plot_bottom = height - bottom_padding
    plot_height = max(plot_bottom - top_padding, 1)
    step_x = plot_width / max(len(values) - 1, 1)
    points: list[str] = []
    for index, value in enumerate(values):
        x = round(left_padding + (index * step_x), 2)
        y = round(plot_bottom - ((value / scale_max) * plot_height), 2)
        points.append(f"{x},{y}")
    return points


def _chart_axis_labels(
    values: list[int],
    maximum: int,
    height: int,
    bottom_padding: int,
    top_padding: int,
) -> list[dict[str, Any]]:
    plot_bottom = height - bottom_padding
    plot_height = max(plot_bottom - top_padding, 1)
    return [
        {
            "label": str(value),
            "y": round(plot_bottom - ((value / maximum) * plot_height), 2),
        }
        for value in values
    ]


def _chart_label_positions(
    labels: list[str],
    width: int,
    left_padding: int = 0,
    right_padding: int = 0,
    y: int = 0,
) -> list[dict[str, Any]]:
    if not labels:
        return []
    plot_width = max(width - left_padding - right_padding, 1)
    step_x = plot_width / max(len(labels) - 1, 1)
    return [
        {
            "label": label,
            "x": round(left_padding + (index * step_x), 2),
            "y": y,
        }
        for index, label in enumerate(labels)
    ]


def _recent_dates(count: int = 7, end_date: date | None = None) -> list[date]:
    last_day = end_date or date.today()
    return [last_day - timedelta(days=(count - 1 - index)) for index in range(count)]


def _label_for_date(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}"


def _default_recent_date_labels(count: int = 7, end_date: date | None = None) -> list[str]:
    return [_label_for_date(value) for value in _recent_dates(count=count, end_date=end_date)]


def _date_labels_from_range(date_from: str, date_to: str) -> list[str]:
    try:
        end_date = date.fromisoformat(date_to) if date_to else date.today()
    except ValueError:
        end_date = date.today()
    return _default_recent_date_labels(end_date=end_date)


def _percent_text(value: int, total: int) -> str:
    if total <= 0:
        return "0.00%"
    return f"{(value / total) * 100:.2f}%"


def _photo_url_for_filename(filename: str | None) -> str:
    if not filename:
        return ""
    return url_for("app.staff_photo", filename=filename)


def _missing_ghana_card_fields(staff: dict[str, Any]) -> list[str]:
    field_map = {
        "ghana_card_number": "Ghana Card Number",
        "nationality": "Nationality",
        "sex": "Sex",
        "date_of_birth": "Date of Birth",
        "digital_address": "Digital Address",
    }
    missing = []
    for key, label in field_map.items():
        if not str(staff.get(key, "") or "").strip():
            missing.append(label)
    return missing


def _display_ghana_datetime(value: str | None) -> str:
    if not value:
        return "Not verified yet"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return str(value)


def _ghana_card_completion_ratio(staff: dict[str, Any]) -> int:
    tracked = [
        "ghana_card_number",
        "nationality",
        "sex",
        "date_of_birth",
        "place_of_birth",
        "residential_address",
        "digital_address",
    ]
    completed = sum(1 for key in tracked if str(staff.get(key, "") or "").strip())
    return round((completed / len(tracked)) * 100)


def _ghana_card_status(staff: dict[str, Any]) -> tuple[str, str]:
    missing_fields = _missing_ghana_card_fields(staff)
    if missing_fields:
        return "Incomplete", "orange"
    if staff.get("ghana_card_verified_at"):
        return "Verified", "green"
    return "Ready", "blue"


def _ghana_card_verification_rows(staff_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for staff in staff_rows:
        item = dict(staff)
        item["photo_url"] = _photo_url_for_filename(item.get("photo_filename"))
        status_text, status_tone = _ghana_card_status(item)
        item["ghana_status_text"] = status_text
        item["ghana_status_tone"] = status_tone
        item["ghana_completion_ratio"] = _ghana_card_completion_ratio(item)
        item["ghana_missing_fields"] = _missing_ghana_card_fields(item)
        item["ghana_verified_at_display"] = _display_ghana_datetime(item.get("ghana_card_verified_at"))
        rows.append(item)
    return rows


def _ghana_card_verification_stats(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    total_staff = len(rows)
    with_profiles = sum(1 for row in rows if str(row.get("ghana_card_number", "")).strip())
    ready_count = sum(1 for row in rows if row.get("ghana_status_text") == "Ready")
    verified_count = sum(1 for row in rows if row.get("ghana_status_text") == "Verified")
    return [
        {"icon": "document", "tone": "blue", "label": "Staff Records", "value": str(total_staff), "sub": "Visible in scope"},
        {"icon": "shield-check", "tone": "green", "label": "Verified", "value": str(verified_count), "sub": "Fingerprint confirmed"},
        {"icon": "fingerprint", "tone": "purple", "label": "Ready to Verify", "value": str(ready_count), "sub": "Complete Ghana Card details"},
        {"icon": "settings", "tone": "orange", "label": "Profiles Added", "value": str(with_profiles), "sub": "Ghana Card numbers saved"},
    ]


def _ghana_card_print_model(staff: dict[str, Any]) -> dict[str, Any]:
    full_name = f"{staff['first_name']} {staff['last_name']}".strip()
    return {
        "full_name": full_name,
        "staff_code": staff.get("staff_code", ""),
        "department": staff.get("department", ""),
        "role": staff.get("role", ""),
        "photo_url": _photo_url_for_filename(staff.get("photo_filename")),
        "ghana_card_number": staff.get("ghana_card_number", ""),
        "nationality": staff.get("nationality", ""),
        "sex": staff.get("sex", ""),
        "date_of_birth": staff.get("date_of_birth", ""),
        "place_of_birth": staff.get("place_of_birth", ""),
        "residential_address": staff.get("residential_address", ""),
        "digital_address": staff.get("digital_address", ""),
        "verified_at": _display_ghana_datetime(staff.get("ghana_card_verified_at")),
        "verified_by": staff.get("ghana_card_verified_by") or "System",
        "verification_note": "Internal verification record only. This document is not a Ghana Card replica or replacement.",
    }


def _hospital_shift_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    for preset in HOSPITAL_SHIFT_PRESETS:
        item = dict(preset)
        duration_minutes = _shift_duration_minutes(
            str(item["shift_start"]),
            str(item["shift_end"]),
        )
        item["time_window"] = (
            f"{_format_clock_label(str(item['shift_start']))} - "
            f"{_format_clock_label(str(item['shift_end']))}"
        )
        item["working_hours_label"] = _format_minutes_as_hours(duration_minutes)
        presets.append(item)
    return presets


def _match_shift_preset(
    shift_start: str | None,
    shift_end: str | None,
) -> dict[str, Any] | None:
    normalized_start = str(shift_start or "").strip()
    normalized_end = str(shift_end or "").strip()
    for preset in HOSPITAL_SHIFT_PRESETS:
        if (
            normalized_start == str(preset["shift_start"])
            and normalized_end == str(preset["shift_end"])
        ):
            return dict(preset)
    return None


def _match_shift_preset_key(shift_start: str | None, shift_end: str | None) -> str:
    preset = _match_shift_preset(shift_start, shift_end)
    return str(preset["key"]) if preset else ""


def _hospital_shift_management_model(department_scope: str = "") -> dict[str, Any]:
    staff_rows = list_staff(active_only=True, department_scope=department_scope)
    presets = _hospital_shift_presets()
    assignments = []
    shift_rows = []

    for index, preset in enumerate(presets):
        matching_staff = [
            row
            for row in staff_rows
            if str(row.get("shift_start", "")) == str(preset["shift_start"])
            and str(row.get("shift_end", "")) == str(preset["shift_end"])
        ]
        departments = sorted(
            {str(row.get("department", "")).strip() for row in matching_staff if row.get("department")}
        )
        shift_rows.append(
            {
                "name": preset["label"],
                "code": f"H{index + 1:02d}",
                "dot": preset["badge_tone"],
                "time": preset["time_window"],
                "break": "Flexible",
                "hours": preset["working_hours_label"],
                "grace": "15 mins",
                "late_after": _format_clock_label(
                    _minutes_after_clock(str(preset["shift_start"]), 15)
                ),
                "status": "Active",
                "employees": len(matching_staff),
                "departments": departments,
            }
        )
        assignments.append(
            {
                "name": preset["label"],
                "employees": len(matching_staff),
                "departments": len(departments),
                "start": _format_clock_label(str(preset["shift_start"])),
                "end": _format_clock_label(str(preset["shift_end"])),
            }
        )

    detail_preset = presets[0] if presets else None
    detail = None
    if detail_preset:
        detail = {
            "name": detail_preset["label"],
            "code": "H01",
            "time": detail_preset["time_window"],
            "break": "Flexible by unit",
            "hours": detail_preset["working_hours_label"],
            "grace": "15 minutes",
            "late_after": _format_clock_label(
                _minutes_after_clock(str(detail_preset["shift_start"]), 15)
            ),
            "early_leave": _format_clock_label(
                _minutes_after_clock(str(detail_preset["shift_end"]), -15)
            ),
            "overtime_after": _format_clock_label(str(detail_preset["shift_end"])),
            "weekly_off": "Configured per department",
            "status": "Active",
            "description": detail_preset["summary"],
            "rules": [
                f"Late if clock-in is after {_format_clock_label(_minutes_after_clock(str(detail_preset['shift_start']), 15))}",
                f"Half day if work is under 4h 00m",
                f"Overtime begins after {_format_clock_label(str(detail_preset['shift_end']))}",
                "Overnight shifts continue into the next calendar day automatically",
            ],
        }

    assigned_total = sum(1 for row in staff_rows if _match_shift_preset(row.get("shift_start"), row.get("shift_end")))
    open_shift_count = sum(1 for row in shift_rows if row["employees"] == 0)
    overnight_count = sum(
        1
        for preset in presets
        if shift_spans_overnight(str(preset["shift_start"]), str(preset["shift_end"]))
    )

    return {
        "stats": [
            {"icon": "shift", "tone": "blue", "label": "Total Shifts", "value": str(len(shift_rows)), "sub": "Hospital presets"},
            {"icon": "staff", "tone": "green", "label": "Assigned Today", "value": str(assigned_total), "sub": "Employees"},
            {"icon": "clock", "tone": "orange", "label": "Open Shifts", "value": str(open_shift_count), "sub": "No staff assigned"},
            {"icon": "overtime", "tone": "purple", "label": "Night Patterns", "value": str(overnight_count), "sub": "Overnight coverage"},
        ],
        "shifts": shift_rows,
        "assignments": assignments,
        "detail": detail,
    }


def _shift_badge_from_window(
    shift_start: str | None,
    shift_end: str | None,
) -> tuple[str, str]:
    preset = _match_shift_preset(shift_start, shift_end)
    if preset:
        return str(preset["badge_label"]), str(preset["badge_tone"])

    try:
        hour = time.fromisoformat(shift_start or "09:00").hour
    except ValueError:
        hour = 9
    if shift_spans_overnight(shift_start, shift_end):
        return "Night", "purple"
    if hour < 12:
        return "Morning", "blue"
    if hour < 18:
        return "Afternoon", "orange"
    return "Night", "purple"


def _shift_duration_minutes(shift_start: str, shift_end: str) -> int:
    start_dt, end_dt = shift_bounds_for_date(
        date(2000, 1, 1),
        shift_start,
        shift_end,
    )
    return max(int((end_dt - start_dt).total_seconds() // 60), 0)


def _format_minutes_as_hours(total_minutes: int) -> str:
    hours, minutes = divmod(max(total_minutes, 0), 60)
    return f"{hours}h {minutes:02d}m"


def _format_clock_label(clock_value: str) -> str:
    try:
        return time.fromisoformat(clock_value).strftime("%I:%M %p")
    except ValueError:
        return clock_value


def _minutes_after_clock(clock_value: str, minutes_delta: int) -> str:
    try:
        base_dt = datetime.combine(date(2000, 1, 1), time.fromisoformat(clock_value))
    except ValueError:
        return clock_value
    return (base_dt + timedelta(minutes=minutes_delta)).time().isoformat(timespec="minutes")


def _format_duration_hhmm(total_minutes: int | None) -> str:
    if total_minutes is None or total_minutes <= 0:
        return "-"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def _format_work_duration(total_minutes: int | None) -> str:
    if total_minutes is None or total_minutes <= 0:
        return "-"
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _average_work_label(values: list[int]) -> str:
    valid = [value for value in values if value > 0]
    if not valid:
        return "0h 00m"
    return _format_work_duration(sum(valid) // len(valid))


def _donut_segments(values: dict[str, int], total: int, circumference: float = 339.29) -> dict[str, dict[str, str]]:
    offset = 0.0
    segments: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        slice_length = round((value / total) * circumference, 2) if total > 0 else 0.0
        remaining = round(max(circumference - slice_length, 0.0), 2)
        segments[key] = {
            "dasharray": f"{slice_length} {remaining}",
            "dashoffset": f"{-round(offset, 2)}",
        }
        offset += slice_length
    return segments


def _attendance_status_display(
    latest_event_type: str,
    latest_status_label: str,
    worked_minutes: int | None,
    attendance_date: str,
    shift_start: str | None,
    shift_end: str | None,
    check_out_at: datetime | None,
) -> tuple[str, str]:
    if latest_event_type == "break_start":
        return "On Break", "purple"
    if latest_event_type == "break_end":
        return "Present", "green"
    if latest_event_type == "check_out":
        if worked_minutes is not None and worked_minutes > 0 and worked_minutes < 240:
            return "Half Day", "purple"
        if check_out_at and shift_start and shift_end and attendance_date:
            try:
                _, scheduled_end = shift_bounds_for_date(
                    date.fromisoformat(attendance_date),
                    shift_start,
                    shift_end,
                )
                if check_out_at >= scheduled_end + timedelta(minutes=45):
                    return "Overtime", "cyan"
            except ValueError:
                pass
        if latest_status_label == "Early checkout":
            return "Early Checkout", "orange"
        return "Present", "green"
    if latest_event_type == "check_in" and latest_status_label == "Late":
        return "Late", "orange"
    return "Present", "green"


def _attendance_activity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped_rows: defaultdict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(
        rows,
        key=lambda item: (
            item["attendance_date"],
            item["staff_id"],
            item["event_time"],
            item["id"],
        ),
    ):
        grouped_rows[(str(row["attendance_date"]), int(row["staff_id"]))].append(row)

    activity_rows: list[dict[str, Any]] = []
    for day_rows in grouped_rows.values():
        base = day_rows[0]
        latest = day_rows[-1]
        check_in_at: datetime | None = None
        check_out_at: datetime | None = None
        latest_dt = datetime.fromisoformat(str(latest["event_time"]))
        active_break_start: datetime | None = None
        break_minutes = 0

        for event in day_rows:
            event_dt = datetime.fromisoformat(str(event["event_time"]))
            if event["event_type"] == "check_in" and check_in_at is None:
                check_in_at = event_dt
            elif event["event_type"] == "check_out":
                check_out_at = event_dt
            elif event["event_type"] == "break_start":
                active_break_start = event_dt
            elif event["event_type"] == "break_end" and active_break_start:
                break_minutes += max(
                    int((event_dt - active_break_start).total_seconds() // 60),
                    0,
                )
                active_break_start = None

        work_end = check_out_at or latest_dt if check_in_at else None
        worked_minutes = None
        if check_in_at and work_end:
            worked_minutes = max(
                int((work_end - check_in_at).total_seconds() // 60) - break_minutes,
                0,
            )

        shift_label, shift_tone = _shift_badge_from_window(
            base.get("shift_start"),
            base.get("shift_end"),
        )
        status_text, status_tone = _attendance_status_display(
            latest_event_type=str(latest["event_type"]),
            latest_status_label=str(latest["status_label"]),
            worked_minutes=worked_minutes,
            attendance_date=str(base["attendance_date"]),
            shift_start=str(base.get("shift_start") or ""),
            shift_end=str(base.get("shift_end") or ""),
            check_out_at=check_out_at,
        )

        activity_rows.append(
            {
                "staff_id": int(base["staff_id"]),
                "attendance_date": str(base["attendance_date"]),
                "latest_event_time": str(latest["event_time"]),
                "staff_code": base["staff_code"],
                "first_name": base["first_name"],
                "last_name": base["last_name"],
                "department": base["department"],
                "role": base["role"],
                "photo_url": _photo_url_for_filename(base.get("photo_filename")),
                "shift_label": shift_label,
                "shift_tone": shift_tone,
                "clock_in": check_in_at.strftime("%I:%M %p") if check_in_at else "-",
                "clock_out": check_out_at.strftime("%I:%M %p") if check_out_at else "-",
                "break_duration": _format_duration_hhmm(break_minutes),
                "work_hours": _format_work_duration(worked_minutes),
                "work_minutes": worked_minutes or 0,
                "status_text": status_text,
                "status_tone": status_tone,
                "remarks": latest.get("notes") or latest["status_label"],
            }
        )

    activity_rows.sort(
        key=lambda item: (item["latest_event_time"], item["staff_code"]),
        reverse=True,
    )
    return activity_rows


def _daily_series_counts(
    activity_rows: list[dict[str, Any]],
    label_dates: list[date],
    active_staff_total: int,
) -> dict[str, list[int]]:
    date_map: dict[str, dict[str, int]] = {
        current_date.isoformat(): {"present": 0, "late": 0, "half_day": 0}
        for current_date in label_dates
    }
    for row in activity_rows:
        current = date_map.get(row["attendance_date"])
        if not current:
            continue
        current["present"] += 1
        if row["status_text"] == "Late":
            current["late"] += 1
        if row["status_text"] == "Half Day":
            current["half_day"] += 1

    present_values: list[int] = []
    absent_values: list[int] = []
    late_values: list[int] = []
    half_day_values: list[int] = []
    for current_date in label_dates:
        bucket = date_map[current_date.isoformat()]
        present_values.append(bucket["present"])
        absent_values.append(max(active_staff_total - bucket["present"], 0))
        late_values.append(bucket["late"])
        half_day_values.append(bucket["half_day"])

    return {
        "present": present_values,
        "absent": absent_values,
        "late": late_values,
        "half_day": half_day_values,
    }


def _chart_series_model(
    label_dates: list[date],
    series_values: dict[str, list[int]],
) -> dict[str, Any]:
    chart_labels = [_label_for_date(current_date) for current_date in label_dates]
    maximum = max(
        1,
        *series_values.get("present", [0]),
        *series_values.get("absent", [0]),
        *series_values.get("late", [0]),
        *series_values.get("half_day", [0]),
    )
    return {
        "chart_labels": chart_labels,
        "chart_series": {
            key: _chart_points(values, maximum=maximum)
            for key, values in series_values.items()
        },
    }


def _dashboard_live_model(
    dashboard: dict[str, Any],
    activity_rows: list[dict[str, Any]],
    target_date: date | None = None,
) -> dict[str, Any]:
    total_staff = int(dashboard.get("total_staff", 0))
    report_day = target_date or date.today()
    label_dates = _recent_dates(end_date=report_day)
    chart_values = _daily_series_counts(activity_rows, label_dates, total_staff)
    chart_width = 520
    chart_height = 188
    chart_left = 54
    chart_right = 18
    chart_top = 30
    chart_bottom_padding = 18
    chart_maximum = max(1, total_staff, *chart_values["present"], *chart_values["absent"], *chart_values["late"])
    today_rows = [
        row for row in activity_rows if row["attendance_date"] == report_day.isoformat()
    ]
    present_today = len(today_rows)
    absent_today = max(total_staff - present_today, 0)
    late_today = sum(1 for row in today_rows if row["status_text"] == "Late")
    donut_segments = _donut_segments(
        {"present": present_today, "absent": absent_today, "late": late_today},
        total_staff,
    )
    return {
        "stats": [
            {"icon": "staff", "tone": "blue", "label": "Total Staff", "value": str(total_staff), "sub": "All Departments"},
            {"icon": "staff", "tone": "green", "label": "Present Today", "value": str(present_today), "sub": f"Late: {late_today}"},
            {"icon": "staff", "tone": "red", "label": "Absent Today", "value": str(absent_today), "sub": "No attendance yet"},
            {"icon": "clock", "tone": "orange", "label": "Late Arrivals", "value": str(late_today), "sub": "From check-in records"},
            {"icon": "overtime", "tone": "purple", "label": "Overtime Today", "value": "0", "sub": "Not recorded yet"},
        ],
        "chart_labels": [_label_for_date(item) for item in label_dates],
        "chart_axes": _chart_axis_labels(
            [chart_maximum, max(chart_maximum * 4 // 5, 1), max(chart_maximum * 3 // 5, 1), max(chart_maximum * 2 // 5, 1), max(chart_maximum // 5, 1)],
            maximum=chart_maximum,
            height=chart_height,
            bottom_padding=chart_bottom_padding,
            top_padding=chart_top,
        ),
        "chart_label_positions": _chart_label_positions(
            [_label_for_date(item) for item in label_dates],
            width=chart_width,
            left_padding=chart_left,
            right_padding=chart_right,
            y=179,
        ),
        "chart_series": {
            "present": _chart_points(chart_values["present"], width=chart_width, height=chart_height, bottom_padding=chart_bottom_padding, maximum=chart_maximum, left_padding=chart_left, right_padding=chart_right, top_padding=chart_top),
            "absent": _chart_points(chart_values["absent"], width=chart_width, height=chart_height, bottom_padding=chart_bottom_padding, maximum=chart_maximum, left_padding=chart_left, right_padding=chart_right, top_padding=chart_top),
            "late": _chart_points(chart_values["late"], width=chart_width, height=chart_height, bottom_padding=chart_bottom_padding, maximum=chart_maximum, left_padding=chart_left, right_padding=chart_right, top_padding=chart_top),
        },
        "today_status": {
            "total": total_staff,
            "present": present_today,
            "absent": absent_today,
            "late": late_today,
            "present_pct": _percent_text(present_today, total_staff),
            "absent_pct": _percent_text(absent_today, total_staff),
            "late_pct": _percent_text(late_today, total_staff),
            "segments": donut_segments,
        },
        "quick_actions": [
            {"label": "Add New Staff", "href": "app.admin_staff_new"},
            {"label": "Clock-In (Kiosk)", "href": "app.kiosk"},
            {"label": "Manage Shifts", "href": "app.admin_shift_management"},
            {"label": "Approve Leaves", "href": "app.admin_leave_management"},
            {"label": "Attendance Report", "href": "app.admin_reports"},
            {"label": "Overtime Report", "href": "app.admin_overtime"},
        ],
        "leave_summary": [
            {"label": "Sick Leave", "value": "0", "suffix": "Employees", "tone": "green"},
            {"label": "Annual Leave", "value": "0", "suffix": "Employees", "tone": "blue"},
            {"label": "Emergency Leave", "value": "0", "suffix": "Employees", "tone": "orange"},
            {"label": "Maternity Leave", "value": "0", "suffix": "Employees", "tone": "purple"},
            {"label": "Paternity Leave", "value": "0", "suffix": "Employees", "tone": "cyan"},
        ],
    }


def _attendance_live_model(
    activity_rows: list[dict[str, Any]],
    active_staff_total: int,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    label_dates = _recent_dates(end_date=date.today())
    chart_values = _daily_series_counts(activity_rows, label_dates, active_staff_total)
    chart_model = _chart_series_model(label_dates, chart_values)
    unique_staff_seen = len({row["staff_id"] for row in activity_rows})
    late_total = sum(1 for row in activity_rows if row["status_text"] == "Late")
    half_day_total = sum(1 for row in activity_rows if row["status_text"] == "Half Day")
    overtime_total = sum(1 for row in activity_rows if row["status_text"] == "Overtime")
    absent_total = max(active_staff_total - unique_staff_seen, 0)
    donut_segments = _donut_segments(
        {
            "present": unique_staff_seen,
            "absent": absent_total,
            "late": late_total,
            "half_day": half_day_total,
        },
        active_staff_total,
    )
    return {
        "stats": [
            {"icon": "staff", "tone": "blue", "label": "Total Staff", "value": str(active_staff_total), "sub": "Active Employees"},
            {"icon": "staff", "tone": "green", "label": "Present", "value": str(unique_staff_seen), "sub": _percent_text(unique_staff_seen, active_staff_total)},
            {"icon": "staff", "tone": "red", "label": "Absent", "value": str(absent_total), "sub": _percent_text(absent_total, active_staff_total)},
            {"icon": "clock", "tone": "orange", "label": "Late", "value": str(late_total), "sub": "Recorded late check-ins"},
            {"icon": "overtime", "tone": "purple", "label": "Half Day", "value": str(half_day_total), "sub": "From recorded work hours"},
            {"icon": "overtime", "tone": "cyan", "label": "Overtime", "value": str(overtime_total), "sub": "Extended checkout records"},
        ],
        **chart_model,
        "status_chart": {
            "total": active_staff_total,
            "present": unique_staff_seen,
            "absent": absent_total,
            "late": late_total,
            "half_day": half_day_total,
            "present_pct": _percent_text(unique_staff_seen, active_staff_total),
            "absent_pct": _percent_text(absent_total, active_staff_total),
            "late_pct": _percent_text(late_total, active_staff_total),
            "half_day_pct": _percent_text(half_day_total, active_staff_total),
            "segments": donut_segments,
        },
    }


def _report_detail_rows_from_activity(
    activity_rows: list[dict[str, Any]],
    date_from: str,
    date_to: str,
) -> list[dict[str, Any]]:
    staff_map: defaultdict[int, dict[str, Any]] = defaultdict(dict)
    for row in activity_rows:
        staff_id = int(row["staff_id"])
        entry = staff_map.setdefault(
            staff_id,
            {
                "staff_id": staff_id,
                "staff_code": row["staff_code"],
                "first_name": row["first_name"],
                "last_name": row["last_name"],
                "department": row["department"],
                "total_days": 0,
                "present": 0,
                "absent": 0,
                "late": 0,
                "half_day": 0,
                "overtime": "0h 00m",
                "avg_hours": "0h 00m",
                "grade": "Good",
                "grade_tone": "green",
                "photo_url": row["photo_url"],
                "_work_minutes": [],
            },
        )
        entry["total_days"] += 1
        entry["present"] += 1
        if row["status_text"] == "Late":
            entry["late"] += 1
        if row["status_text"] == "Half Day":
            entry["half_day"] += 1
        if row["status_text"] == "Overtime":
            hours_value = row["work_hours"].replace("h", " ").replace("m", "").split()
            if len(hours_value) == 2:
                overtime_minutes = (int(hours_value[0]) * 60) + int(hours_value[1])
                entry["overtime"] = _format_work_duration(overtime_minutes)
        if row["work_minutes"] > 0:
            entry["_work_minutes"].append(int(row["work_minutes"]))

    detail_rows = list(staff_map.values())
    for row in detail_rows:
        work_minutes = row.pop("_work_minutes")
        row["avg_hours"] = _average_work_label(work_minutes)
        if row["late"] >= 3:
            row["grade"] = "Poor"
            row["grade_tone"] = "red"
        elif row["late"] >= 1:
            row["grade"] = "Fair"
            row["grade_tone"] = "orange"
    detail_rows.sort(key=lambda item: (item["last_name"] if "last_name" in item else "", item["first_name"]))
    return detail_rows


def _report_live_model(
    snapshot: dict[str, Any],
    detail_rows: list[dict[str, Any]],
    active_staff_total: int,
    labels: list[str],
) -> dict[str, Any]:
    label_dates = _recent_dates(end_date=date.today())
    activity_rows = []
    for row in snapshot.get("daily_rows", []):
        activity_rows.append(
            {
                "attendance_date": row["attendance_date"],
                "staff_id": -1,
                "status_text": "Present",
            }
        )
    chart_counts_map = {entry["attendance_date"]: entry for entry in snapshot.get("daily_rows", [])}
    present_values = [int(chart_counts_map.get(current_date.isoformat(), {}).get("staff_seen", 0)) for current_date in label_dates]
    absent_values = [int(chart_counts_map.get(current_date.isoformat(), {}).get("absent", 0)) for current_date in label_dates]
    late_values = [int(chart_counts_map.get(current_date.isoformat(), {}).get("late", 0)) for current_date in label_dates]
    half_day_values = [0 for _ in label_dates]
    chart_model = _chart_series_model(
        label_dates,
        {
            "present": present_values,
            "absent": absent_values,
            "late": late_values,
            "half_day": half_day_values,
        },
    )
    present_total = sum(int(row["present"]) for row in detail_rows)
    late_total = sum(int(row["late"]) for row in detail_rows)
    half_day_total = sum(int(row["half_day"]) for row in detail_rows)
    unique_staff_seen = len(detail_rows)
    absent_total = max(active_staff_total - unique_staff_seen, 0)
    average_hours = _average_work_label(
        [
            int(row["avg_hours"].split("h")[0]) * 60 + int(row["avg_hours"].split("h")[1].replace("m", "").strip())
            for row in detail_rows
            if row["avg_hours"] != "0h 00m"
        ]
    )
    donut_segments = _donut_segments(
        {
            "present": unique_staff_seen,
            "absent": absent_total,
            "late": late_total,
            "half_day": half_day_total,
        },
        active_staff_total,
    )
    return {
        "stats": [
            {"icon": "staff", "tone": "blue", "label": "Total Employees", "value": str(active_staff_total), "sub": "Active Staff"},
            {"icon": "check-circle", "tone": "green", "label": "Present", "value": str(unique_staff_seen), "sub": _percent_text(unique_staff_seen, active_staff_total)},
            {"icon": "staff", "tone": "red", "label": "Absent", "value": str(absent_total), "sub": _percent_text(absent_total, active_staff_total)},
            {"icon": "clock", "tone": "orange", "label": "Late", "value": str(late_total), "sub": "Late days recorded"},
            {"icon": "overtime", "tone": "purple", "label": "Overtime", "value": "0h 00m", "sub": "Not calculated yet"},
        ],
        "summary": [
            {"label": "Total Working Days", "value": str(len(snapshot.get("daily_rows", [])))},
            {"label": "Total Present Days", "value": str(present_total)},
            {"label": "Total Absent Days", "value": str(absent_total)},
            {"label": "Total Late Days", "value": str(late_total)},
            {"label": "Total Half Days", "value": str(half_day_total)},
            {"label": "Average Working Hours", "value": average_hours},
            {"label": "Average Overtime Hours", "value": "0h 00m"},
        ],
        "quick_reports": [
            "Daily Summary Report",
            "Monthly Summary Report",
            "Late Report",
            "Absence Report",
            "Overtime Report",
            "Leave Report",
            "Shift Report",
            "Custom Report",
        ],
        **chart_model,
        "distribution": {
            "total": active_staff_total,
            "present": unique_staff_seen,
            "absent": absent_total,
            "late": late_total,
            "half_day": half_day_total,
            "present_pct": _percent_text(unique_staff_seen, active_staff_total),
            "absent_pct": _percent_text(absent_total, active_staff_total),
            "late_pct": _percent_text(late_total, active_staff_total),
            "half_day_pct": _percent_text(half_day_total, active_staff_total),
            "segments": donut_segments,
        },
    }


def _shift_empty_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "shift", "tone": "blue", "label": "Total Shifts", "value": "0", "sub": "Active Shifts"},
            {"icon": "staff", "tone": "green", "label": "Assigned Today", "value": "0", "sub": "Employees"},
            {"icon": "clock", "tone": "orange", "label": "Open Shifts", "value": "0", "sub": "Vacant Positions"},
            {"icon": "overtime", "tone": "purple", "label": "Rotations", "value": "0", "sub": "Active Rotations"},
        ],
        "shifts": [],
        "assignments": [],
        "detail": None,
    }


def _leave_empty_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "leave", "tone": "blue", "label": "Total Requests", "value": "0", "sub": "This Month"},
            {"icon": "check-circle", "tone": "green", "label": "Approved", "value": "0", "sub": "0.00%"},
            {"icon": "clock", "tone": "orange", "label": "Pending", "value": "0", "sub": "0.00%"},
            {"icon": "close", "tone": "red", "label": "Rejected", "value": "0", "sub": "0.00%"},
            {"icon": "leave", "tone": "purple", "label": "On Leave Today", "value": "0", "sub": "Employees"},
        ],
        "balances": [
            {"label": "Annual Leave", "value": "0 / 0 Days", "ratio": 0, "tone": "blue"},
            {"label": "Sick Leave", "value": "0 / 0 Days", "ratio": 0, "tone": "green"},
            {"label": "Emergency Leave", "value": "0 / 0 Days", "ratio": 0, "tone": "orange"},
            {"label": "Maternity Leave", "value": "0 / 0 Days", "ratio": 0, "tone": "purple"},
            {"label": "Paternity Leave", "value": "0 / 0 Days", "ratio": 0, "tone": "cyan"},
        ],
    }


def _overtime_empty_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "overtime", "tone": "purple", "label": "Total Overtime Hrs", "value": "0h 00m", "sub": "This Month"},
            {"icon": "payroll", "tone": "green", "label": "Total Overtime Pay", "value": "$0.00", "sub": "This Month"},
            {"icon": "staff", "tone": "blue", "label": "Employees with OT", "value": "0", "sub": "This Month"},
            {"icon": "document", "tone": "orange", "label": "Approved Hours", "value": "0h 00m", "sub": "0.00%"},
            {"icon": "clock", "tone": "red", "label": "Pending Hours", "value": "0h 00m", "sub": "0.00%"},
        ],
        "top_employees": [],
        "summary": [
            {"label": "Total Overtime Hours", "value": "0h 00m"},
            {"label": "Total Overtime Pay", "value": "$0.00"},
            {"label": "Average OT Hours / Employee", "value": "0h 00m"},
        ],
    }


def _payroll_empty_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "payroll", "tone": "blue", "label": "Total Employees", "value": "0", "sub": "All Employees"},
            {"icon": "payroll", "tone": "green", "label": "Gross Pay", "value": "$0.00", "sub": "This Month"},
            {"icon": "payroll", "tone": "orange", "label": "Deductions", "value": "$0.00", "sub": "This Month"},
            {"icon": "payroll", "tone": "purple", "label": "Net Pay", "value": "$0.00", "sub": "This Month"},
            {"icon": "staff", "tone": "cyan", "label": "Processed", "value": "0", "sub": "0.00%"},
            {"icon": "clock", "tone": "red", "label": "Pending", "value": "0", "sub": "0.00%"},
        ],
        "summary": [
            {"label": "Total Employees", "value": "0"},
            {"label": "Total Gross Pay", "value": "$0.00"},
            {"label": "Total Deductions", "value": "$0.00"},
            {"label": "Total Net Pay", "value": "$0.00"},
            {"label": "Processed Employees", "value": "0 (0.00%)"},
            {"label": "Pending Employees", "value": "0 (0.00%)"},
            {"label": "Hold Employees", "value": "0 (0.00%)"},
        ],
        "deductions": [
            {"label": "Tax", "value": "$0.00"},
            {"label": "Provident Fund", "value": "$0.00"},
            {"label": "Health Insurance", "value": "$0.00"},
            {"label": "Other Deductions", "value": "$0.00"},
            {"label": "Total Deductions", "value": "$0.00"},
        ],
    }


def _correction_empty_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "document", "tone": "blue", "label": "Total Requests", "value": "0", "sub": "This Month"},
            {"icon": "clock", "tone": "orange", "label": "Pending", "value": "0", "sub": "0.00%"},
            {"icon": "check-circle", "tone": "green", "label": "Approved", "value": "0", "sub": "0.00%"},
            {"icon": "close", "tone": "red", "label": "Rejected", "value": "0", "sub": "0.00%"},
        ],
        "detail": None,
        "history": [],
    }


def _users_role_stats(user_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    total_users = len(user_rows)
    elevated_roles = {SUPER_ADMIN, HR_ADMIN}
    scoped_roles = {DEPARTMENT_MANAGER, "Supervisor"}
    elevated_users = sum(1 for row in user_rows if row.get("access_role") in elevated_roles)
    scoped_users = sum(1 for row in user_rows if row.get("access_role") in scoped_roles)
    staff_users = sum(1 for row in user_rows if row.get("access_role") == STAFF)
    return [
        {"icon": "users", "tone": "blue", "label": "Total Users", "value": str(total_users), "sub": "Staff accounts"},
        {"icon": "settings", "tone": "green", "label": "Admin Roles", "value": str(elevated_users), "sub": "Super Admin + HR/Admin"},
        {"icon": "staff", "tone": "orange", "label": "Managers", "value": str(scoped_users), "sub": "Department + Supervisor"},
        {"icon": "staff", "tone": "purple", "label": "Staff Role", "value": str(staff_users), "sub": "Standard access"},
    ]


def _live_role_rows(user_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    role_descriptions = {
        SUPER_ADMIN: "Full control across attendance, payroll, settings, and system administration.",
        HR_ADMIN: "Manages staff, reports, leave, payroll reviews, and organization settings.",
        DEPARTMENT_MANAGER: "Handles attendance and staff oversight for one department.",
        "Supervisor": "Tracks team attendance and reporting with no system-level settings access.",
        STAFF: "Uses the staff portal, kiosk, QR, PIN, password, and mobile clock actions.",
    }
    role_counts = {
        role_name: sum(1 for row in user_rows if row.get("access_role") == role_name)
        for role_name in ACCESS_ROLE_CHOICES
    }
    rows = []
    for role_name in ACCESS_ROLE_CHOICES:
        assigned = role_counts.get(role_name, 0)
        rows.append(
            {
                "title": role_name,
                "meta": f"{assigned} assigned. {role_descriptions.get(role_name, 'Built-in access role for the live attendance system.')}",
            }
        )
    return rows


def _dashboard_reference_model() -> dict[str, Any]:
    chart_labels = ["May 13", "May 14", "May 15", "May 16", "May 17", "May 18", "May 19"]
    chart_width = 520
    chart_height = 188
    chart_maximum = 250
    chart_left = 54
    chart_right = 18
    chart_top = 30
    chart_bottom_padding = 18
    return {
        "stats": [
            {"icon": "staff", "tone": "blue", "label": "Total Staff", "value": "256", "sub": "All Departments"},
            {"icon": "staff", "tone": "green", "label": "Present Today", "value": "186", "sub": "On time: 142 | Late: 44"},
            {"icon": "staff", "tone": "red", "label": "Absent Today", "value": "45", "sub": "Approved Leave: 28"},
            {"icon": "clock", "tone": "orange", "label": "Late Arrivals", "value": "44", "sub": "After grace time"},
            {"icon": "overtime", "tone": "purple", "label": "Overtime Today", "value": "26", "sub": "Total Overtime Hours"},
        ],
        "chart_labels": chart_labels,
        "chart_axes": _chart_axis_labels(
            [250, 200, 150, 100, 50],
            maximum=chart_maximum,
            height=chart_height,
            bottom_padding=chart_bottom_padding,
            top_padding=chart_top,
        ),
        "chart_label_positions": _chart_label_positions(
            chart_labels,
            width=chart_width,
            left_padding=chart_left,
            right_padding=chart_right,
            y=179,
        ),
        "chart_series": {
            "present": _chart_points(
                [164, 172, 159, 178, 162, 126, 154],
                width=chart_width,
                height=chart_height,
                bottom_padding=chart_bottom_padding,
                maximum=chart_maximum,
                left_padding=chart_left,
                right_padding=chart_right,
                top_padding=chart_top,
            ),
            "absent": _chart_points(
                [42, 58, 39, 51, 49, 31, 46],
                width=chart_width,
                height=chart_height,
                bottom_padding=chart_bottom_padding,
                maximum=chart_maximum,
                left_padding=chart_left,
                right_padding=chart_right,
                top_padding=chart_top,
            ),
            "late": _chart_points(
                [22, 27, 31, 23, 29, 21, 28],
                width=chart_width,
                height=chart_height,
                bottom_padding=chart_bottom_padding,
                maximum=chart_maximum,
                left_padding=chart_left,
                right_padding=chart_right,
                top_padding=chart_top,
            ),
        },
        "today_status": {"total": 256, "present": 186, "absent": 45, "late": 44},
        "quick_actions": [
            {"label": "Add New Staff", "href": "app.admin_staff_new"},
            {"label": "Clock-In (Kiosk)", "href": "app.kiosk"},
            {"label": "Manage Shifts", "href": "app.admin_shift_management"},
            {"label": "Approve Leaves", "href": "app.admin_leave_management"},
            {"label": "Attendance Report", "href": "app.admin_reports"},
            {"label": "Overtime Report", "href": "app.admin_overtime"},
        ],
        "leave_summary": [
            {"label": "Sick Leave", "value": "5", "suffix": "Employees", "tone": "green"},
            {"label": "Annual Leave", "value": "12", "suffix": "Employees", "tone": "blue"},
            {"label": "Emergency Leave", "value": "3", "suffix": "Employees", "tone": "orange"},
            {"label": "Maternity Leave", "value": "2", "suffix": "Employees", "tone": "purple"},
            {"label": "Paternity Leave", "value": "1", "suffix": "Employee", "tone": "cyan"},
        ],
    }


def _attendance_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "staff", "tone": "blue", "label": "Total Staff", "value": "256", "sub": "All Employees"},
            {"icon": "staff", "tone": "green", "label": "Present", "value": "186", "sub": "72.66%"},
            {"icon": "staff", "tone": "red", "label": "Absent", "value": "45", "sub": "17.58%"},
            {"icon": "clock", "tone": "orange", "label": "Late", "value": "44", "sub": "17.19%"},
            {"icon": "overtime", "tone": "purple", "label": "Half Day", "value": "12", "sub": "4.69%"},
            {"icon": "overtime", "tone": "cyan", "label": "Overtime", "value": "26", "sub": "10.16%"},
        ],
        "chart_labels": ["May 13", "May 14", "May 15", "May 16", "May 17", "May 18", "May 19"],
        "chart_series": {
            "present": _chart_points([162, 176, 159, 181, 166, 128, 157]),
            "absent": _chart_points([41, 55, 37, 53, 49, 29, 43]),
            "late": _chart_points([18, 23, 19, 24, 21, 14, 18]),
            "half_day": _chart_points([8, 10, 11, 9, 8, 7, 10]),
        },
        "status_chart": {"total": 256, "present": 186, "absent": 45, "late": 44, "half_day": 12},
    }


def _shift_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "shift", "tone": "blue", "label": "Total Shifts", "value": "5", "sub": "Active Shifts"},
            {"icon": "staff", "tone": "green", "label": "Assigned Today", "value": "186", "sub": "Employees"},
            {"icon": "clock", "tone": "orange", "label": "Open Shifts", "value": "8", "sub": "Vacant Positions"},
            {"icon": "overtime", "tone": "purple", "label": "Rotations", "value": "3", "sub": "Active Rotations"},
        ],
        "shifts": [
            {"name": "Morning Shift", "code": "MOR", "dot": "green", "time": "09:00 AM - 05:00 PM", "break": "01:00 PM - 02:00 PM", "hours": "8h 00m", "grace": "15m", "late_after": "09:15 AM", "status": "Active"},
            {"name": "Afternoon Shift", "code": "AFT", "dot": "blue", "time": "02:00 PM - 10:00 PM", "break": "06:00 PM - 07:00 PM", "hours": "8h 00m", "grace": "15m", "late_after": "02:15 PM", "status": "Active"},
            {"name": "Night Shift", "code": "NIG", "dot": "purple", "time": "10:00 PM - 06:00 AM", "break": "02:00 AM - 03:00 AM", "hours": "8h 00m", "grace": "20m", "late_after": "10:20 PM", "status": "Active"},
            {"name": "General Shift", "code": "GEN", "dot": "orange", "time": "09:30 AM - 06:30 PM", "break": "01:00 PM - 02:00 PM", "hours": "8h 00m", "grace": "15m", "late_after": "09:45 AM", "status": "Active"},
            {"name": "Weekend Shift", "code": "WED", "dot": "cyan", "time": "09:00 AM - 01:00 PM", "break": "-", "hours": "4h 00m", "grace": "10m", "late_after": "09:10 AM", "status": "Inactive"},
        ],
        "assignments": [
            {"name": "Morning Shift", "employees": 78, "departments": 4, "start": "09:00 AM", "end": "05:00 PM"},
            {"name": "Afternoon Shift", "employees": 52, "departments": 3, "start": "02:00 PM", "end": "10:00 PM"},
            {"name": "Night Shift", "employees": 38, "departments": 2, "start": "10:00 PM", "end": "06:00 AM"},
            {"name": "Weekend Shift", "employees": 18, "departments": 2, "start": "09:00 AM", "end": "01:00 PM"},
        ],
    }


def _leave_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "leave", "tone": "blue", "label": "Total Requests", "value": "28", "sub": "This Month"},
            {"icon": "check-circle", "tone": "green", "label": "Approved", "value": "16", "sub": "57.14%"},
            {"icon": "clock", "tone": "orange", "label": "Pending", "value": "8", "sub": "28.57%"},
            {"icon": "close", "tone": "red", "label": "Rejected", "value": "4", "sub": "14.29%"},
            {"icon": "leave", "tone": "purple", "label": "On Leave Today", "value": "12", "sub": "Employees"},
        ],
        "balances": [
            {"label": "Annual Leave", "value": "12.5 / 20 Days", "ratio": 62, "tone": "blue"},
            {"label": "Sick Leave", "value": "6 / 10 Days", "ratio": 60, "tone": "green"},
            {"label": "Emergency Leave", "value": "3 / 5 Days", "ratio": 60, "tone": "orange"},
            {"label": "Maternity Leave", "value": "90 / 90 Days", "ratio": 100, "tone": "purple"},
            {"label": "Paternity Leave", "value": "7 / 10 Days", "ratio": 70, "tone": "cyan"},
        ],
    }


def _overtime_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "overtime", "tone": "purple", "label": "Total Overtime Hrs", "value": "186h 45m", "sub": "This Month"},
            {"icon": "payroll", "tone": "green", "label": "Total Overtime Pay", "value": "$4,673.50", "sub": "This Month"},
            {"icon": "staff", "tone": "blue", "label": "Employees with OT", "value": "32", "sub": "This Month"},
            {"icon": "document", "tone": "orange", "label": "Approved Hours", "value": "162h 30m", "sub": "87.01%"},
            {"icon": "clock", "tone": "red", "label": "Pending Hours", "value": "24h 15m", "sub": "12.99%"},
        ],
        "top_employees": [
            {"name": "Sarah Johnson", "hours": "20h 30m"},
            {"name": "David Wilson", "hours": "18h 45m"},
            {"name": "Michael Brown", "hours": "16h 00m"},
            {"name": "John Doe", "hours": "15h 30m"},
            {"name": "Emily Davis", "hours": "12h 30m"},
        ],
        "dept_breakdown": [
            {"label": "IT Department", "value": "50h 30m (27.1%)", "tone": "blue"},
            {"label": "Operations", "value": "45h 15m (24.3%)", "tone": "green"},
            {"label": "Finance", "value": "32h 00m (17.1%)", "tone": "orange"},
            {"label": "Marketing", "value": "28h 15m (15.1%)", "tone": "purple"},
            {"label": "HR Department", "value": "24h 40m (13.2%)", "tone": "red"},
        ],
    }


def _payroll_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "payroll", "tone": "blue", "label": "Total Employees", "value": "256", "sub": "All Employees"},
            {"icon": "payroll", "tone": "green", "label": "Gross Pay", "value": "$98,764.50", "sub": "This Month"},
            {"icon": "payroll", "tone": "orange", "label": "Deductions", "value": "$18,245.75", "sub": "This Month"},
            {"icon": "payroll", "tone": "purple", "label": "Net Pay", "value": "$80,518.75", "sub": "This Month"},
            {"icon": "staff", "tone": "cyan", "label": "Processed", "value": "206", "sub": "80.47%"},
            {"icon": "clock", "tone": "red", "label": "Pending", "value": "50", "sub": "19.53%"},
        ],
        "summary": [
            {"label": "Total Employees", "value": "256", "tone": "blue"},
            {"label": "Total Gross Pay", "value": "$98,764.50", "tone": "green"},
            {"label": "Total Deductions", "value": "$18,245.75", "tone": "orange"},
            {"label": "Total Net Pay", "value": "$80,518.75", "tone": "purple"},
            {"label": "Processed Employees", "value": "206 (80.47%)", "tone": "cyan"},
            {"label": "Pending Employees", "value": "50 (19.53%)", "tone": "orange"},
            {"label": "Hold Employees", "value": "2 (0.78%)", "tone": "red"},
        ],
        "deductions": [
            {"label": "Tax", "value": "$7,856.40"},
            {"label": "Provident Fund", "value": "$5,963.20"},
            {"label": "Health Insurance", "value": "$2,145.50"},
            {"label": "Other Deductions", "value": "$2,280.65"},
            {"label": "Total Deductions", "value": "$18,245.75"},
        ],
    }


def _correction_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "document", "tone": "blue", "label": "Total Requests", "value": "36", "sub": "This Month"},
            {"icon": "clock", "tone": "orange", "label": "Pending", "value": "12", "sub": "33.3%"},
            {"icon": "check-circle", "tone": "green", "label": "Approved", "value": "18", "sub": "50.00%"},
            {"icon": "close", "tone": "red", "label": "Rejected", "value": "6", "sub": "16.67%"},
        ],
        "detail": {
            "status": "Pending",
            "request_id": "AC-2024-0054",
            "name": "John Doe",
            "code": "STF001",
            "department": "IT Department",
            "date": "May 20, 2024 (Monday)",
            "shift": "Morning (09:00 AM - 05:00 PM)",
            "type": "Check In",
            "original_time": "09:45 AM",
            "requested_time": "09:00 AM",
            "difference": "45 mins (Early)",
            "reason": "Traffic due to road construction on the highway.",
            "requested_on": "May 20, 2024 10:15 AM",
            "attachment": "traffic_screenshot.jpg",
        },
    }


def _report_reference_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "staff", "tone": "blue", "label": "Total Employees", "value": "256", "sub": "100%"},
            {"icon": "check-circle", "tone": "green", "label": "Present", "value": "206", "sub": "80.47%"},
            {"icon": "staff", "tone": "red", "label": "Absent", "value": "28", "sub": "10.94%"},
            {"icon": "clock", "tone": "orange", "label": "Late", "value": "22", "sub": "8.59%"},
            {"icon": "overtime", "tone": "purple", "label": "Overtime", "value": "186h 45m", "sub": "Total Hours"},
        ],
        "summary": [
            {"label": "Total Working Days", "value": "23", "tone": "blue"},
            {"label": "Total Present Days", "value": "206", "tone": "green"},
            {"label": "Total Absent Days", "value": "28", "tone": "red"},
            {"label": "Total Late Days", "value": "22", "tone": "orange"},
            {"label": "Total Half Days", "value": "9", "tone": "purple"},
            {"label": "Average Working Hours", "value": "8h 35m", "tone": "blue"},
            {"label": "Average Overtime Hours", "value": "0h 43m", "tone": "blue"},
        ],
        "quick_reports": [
            "Daily Summary Report",
            "Monthly Summary Report",
            "Late Report",
            "Absence Report",
            "Overtime Report",
            "Leave Report",
            "Shift Report",
            "Custom Report",
        ],
        "chart_labels": ["May 1", "May 6", "May 11", "May 16", "May 21", "May 26", "May 31"],
        "chart_series": {
            "present": _chart_points([177, 168, 201, 172, 169, 203, 175]),
            "absent": _chart_points([41, 53, 49, 61, 54, 48, 46]),
            "late": _chart_points([18, 23, 16, 28, 21, 25, 19]),
            "half_day": _chart_points([7, 10, 9, 12, 8, 7, 9]),
        },
        "distribution": {"total": 256, "present": 206, "absent": 28, "late": 22, "half_day": 9},
    }


def _notification_reference_model() -> list[dict[str, Any]]:
    return [
        {"title": "New leave request from Jane Smith", "meta": "2 minutes ago · Requires approval"},
        {"title": "Payroll run scheduled for May 31, 2024", "meta": "1 hour ago · Finance"},
        {"title": "Attendance device sync completed", "meta": "Today 09:24 AM · MorphoSmart kiosk"},
        {"title": "3 correction requests are pending review", "meta": "Today 08:10 AM · HR/Admin"},
    ]


def _holiday_reference_model() -> list[dict[str, Any]]:
    return [
        {"title": "Founders Day", "meta": "August 4, 2024 · National Holiday"},
        {"title": "Farmers Day", "meta": "December 6, 2024 · Public Holiday"},
        {"title": "Christmas Day", "meta": "December 25, 2024 · Public Holiday"},
        {"title": "Boxing Day", "meta": "December 26, 2024 · Public Holiday"},
    ]


def _role_reference_model() -> list[dict[str, Any]]:
    return [
        {"title": "Super Admin", "meta": "Full system access, payroll, settings, audit, and approvals"},
        {"title": "HR/Admin", "meta": "Staff management, attendance, leave, correction, reports, payroll view"},
        {"title": "Department Manager", "meta": "Department staff view, approvals, reports, and attendance review"},
        {"title": "Supervisor", "meta": "Attendance review, overtime visibility, and limited reporting"},
        {"title": "Staff", "meta": "Personal attendance, leave requests, QR access, and profile self-service"},
    ]


def _audit_reference_rows() -> list[dict[str, str]]:
    return [
        {"time": "May 19, 2024 10:05 AM", "actor": "Admin User", "event": "Updated payroll settings", "area": "Payroll"},
        {"time": "May 19, 2024 09:42 AM", "actor": "John Doe", "event": "Submitted attendance correction request", "area": "Attendance Correction"},
        {"time": "May 19, 2024 09:18 AM", "actor": "System", "event": "Attendance kiosk synchronized successfully", "area": "Device"},
        {"time": "May 19, 2024 08:31 AM", "actor": "HR/Admin", "event": "Approved leave request for Jane Smith", "area": "Leave"},
        {"time": "May 18, 2024 06:10 PM", "actor": "Finance", "event": "Processed monthly payroll batch", "area": "Payroll"},
    ]


def _selfie_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted_rows: list[dict[str, Any]] = []
    for row in rows:
        created_at = str(row.get("created_at") or "")
        try:
            display_time = datetime.fromisoformat(created_at).strftime("%d %b %Y, %I:%M %p")
        except ValueError:
            display_time = created_at
        formatted_rows.append(
            {
                "time": display_time,
                "actor": f"{row.get('first_name', '')} {row.get('last_name', '')}".strip() or row.get("staff_code", ""),
                "staff_code": row.get("staff_code", ""),
                "department": row.get("department", ""),
                "event": "Staff login selfie captured",
                "area": "Selfie Audit",
                "auth_method": str(row.get("auth_method", "")).replace("_", " ").title(),
                "ip_address": row.get("ip_address", ""),
                "device_name": row.get("device_name", ""),
                "photo_url": _audit_selfie_url_for_filename(row.get("photo_filename")),
            }
        )
    return formatted_rows


def _admin_activity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    formatted_rows: list[dict[str, Any]] = []
    for row in rows:
        created_at = str(row.get("created_at") or "")
        try:
            display_time = datetime.fromisoformat(created_at).strftime("%d %b %Y, %I:%M %p")
        except ValueError:
            display_time = created_at
        actor_type = str(row.get("actor_type") or "user").strip().lower()
        formatted_rows.append(
            {
                "time": display_time,
                "actor": str(row.get("actor_name") or "Unknown User"),
                "actor_role": str(row.get("actor_role") or "System User"),
                "event": str(row.get("event_type") or "").replace("_", " ").title(),
                "details": str(row.get("details") or row.get("target_name") or "No additional details"),
                "target_name": str(row.get("target_name") or ""),
                "ip_address": row.get("ip_address", ""),
                "device_name": row.get("device_name", ""),
                "actor_type": actor_type,
                "area": "User Activity" if actor_type == "user" else "Platform",
            }
        )
    return formatted_rows


def _build_admin_notification_rows(limit: int = 12) -> list[dict[str, str]]:
    app_defaults = get_app_settings(default_app_name=_tenant_default_app_name())
    admin_security = get_admin_security(
        default_username=current_app.config["APP_SETTINGS"].admin_username
    )
    rows: list[dict[str, str]] = []

    active_staff_total = count_active_staff(department_scope=current_department_scope())
    if active_staff_total == 0:
        rows.append(
            {
                "title": "Add your first staff records",
                "meta": "No active staff accounts exist yet. Open Staff and create your institution team.",
                "tone": "warning",
            }
        )

    if not admin_security.get("password_is_custom"):
        rows.append(
            {
                "title": "Institution admin password still uses the default source",
                "meta": "Open Settings and set a custom admin password for this institution.",
                "tone": "warning",
            }
        )

    if not app_defaults.get("location_enforcement_enabled"):
        rows.append(
            {
                "title": "Work location restriction is disabled",
                "meta": "Staff can currently clock in online from any location until GPS restriction is enabled.",
                "tone": "info",
            }
        )

    for audit_row in _selfie_audit_rows(list_staff_selfie_audits(limit=4)):
        rows.append(
            {
                "title": f"{audit_row['actor']} signed in with selfie audit",
                "meta": f"{audit_row['time']} · {audit_row['auth_method']} · {audit_row['department'] or 'Unassigned department'}",
                "tone": "activity",
            }
        )

    for activity_row in _admin_activity_rows(list_admin_activity_logs(limit=4)):
        rows.append(
            {
                "title": f"{activity_row['actor']} - {activity_row['event']}",
                "meta": f"{activity_row['time']} · {activity_row['details']}",
                "tone": "neutral",
            }
        )

    return rows[:limit]


def _staff_display_rows(staff_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    display_rows: list[dict[str, Any]] = []
    for row in staff_rows[:10]:
        item = dict(row)
        item["status_text"] = "Active" if item.get("is_active") else "Inactive"
        item["photo_url"] = _photo_url_for_filename(item.get("photo_filename"))
        display_rows.append(item)
    return display_rows


def _attendance_display_rows() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "role": "Software Engineer", "shift_label": "Morning", "shift_tone": "blue", "clock_in": "08:55 AM", "clock_out": "05:45 PM", "break_duration": "01:00", "work_hours": "8h 50m", "status_text": "Present", "status_tone": "green", "remarks": "-", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "role": "HR Manager", "shift_label": "Morning", "shift_tone": "blue", "clock_in": "09:20 AM", "clock_out": "06:05 PM", "break_duration": "01:00", "work_hours": "8h 45m", "status_text": "Late", "status_tone": "orange", "remarks": "Late by 20m", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "role": "Accountant", "shift_label": "Morning", "shift_tone": "blue", "clock_in": "08:40 AM", "clock_out": "05:30 PM", "break_duration": "01:00", "work_hours": "8h 50m", "status_text": "Present", "status_tone": "green", "remarks": "-", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "role": "Marketing Executive", "shift_label": "Morning", "shift_tone": "blue", "clock_in": "-", "clock_out": "-", "break_duration": "-", "work_hours": "-", "status_text": "Absent", "status_tone": "red", "remarks": "No Show", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "role": "Operations Manager", "shift_label": "Night", "shift_tone": "purple", "clock_in": "09:05 PM", "clock_out": "06:10 AM", "break_duration": "01:00", "work_hours": "8h 05m", "status_text": "Present", "status_tone": "green", "remarks": "-", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "role": "UI/UX Designer", "shift_label": "Afternoon", "shift_tone": "orange", "clock_in": "01:15 PM", "clock_out": "06:00 PM", "break_duration": "00:30", "work_hours": "4h 15m", "status_text": "Half Day", "status_tone": "purple", "remarks": "Left Early", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "role": "Senior Accountant", "shift_label": "Morning", "shift_tone": "blue", "clock_in": "08:30 AM", "clock_out": "07:00 PM", "break_duration": "01:00", "work_hours": "9h 30m", "status_text": "Overtime", "status_tone": "cyan", "remarks": "Overtime 1h 30m", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "role": "HR Executive", "shift_label": "Morning", "shift_tone": "blue", "clock_in": "-", "clock_out": "-", "break_duration": "-", "work_hours": "-", "status_text": "Absent", "status_tone": "red", "remarks": "On Leave", "photo_url": ""},
    ]


def _leave_display_rows() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "leave_type": "Annual Leave", "leave_tone": "blue", "duration": "3 Days", "dates": "May 20, 2024 - May 22, 2024", "reason": "Family vacation", "status": "Pending", "status_tone": "orange", "applied_on": "May 16, 2024", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "leave_type": "Sick Leave", "leave_tone": "green", "duration": "2 Days", "dates": "May 15, 2024 - May 16, 2024", "reason": "Fever and cold", "status": "Approved", "status_tone": "green", "applied_on": "May 14, 2024", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "leave_type": "Emergency Leave", "leave_tone": "orange", "duration": "1 Day", "dates": "May 13, 2024", "reason": "Personal emergency", "status": "Approved", "status_tone": "green", "applied_on": "May 13, 2024", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "leave_type": "Annual Leave", "leave_tone": "blue", "duration": "5 Days", "dates": "Jun 03, 2024 - Jun 07, 2024", "reason": "Trip", "status": "Pending", "status_tone": "orange", "applied_on": "May 17, 2024", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "leave_type": "Sick Leave", "leave_tone": "green", "duration": "1 Day", "dates": "May 10, 2024", "reason": "Headache", "status": "Rejected", "status_tone": "red", "applied_on": "May 10, 2024", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "leave_type": "Maternity Leave", "leave_tone": "purple", "duration": "90 Days", "dates": "Apr 01, 2024 - Jun 29, 2024", "reason": "Maternity", "status": "Approved", "status_tone": "green", "applied_on": "Mar 25, 2024", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "leave_type": "Annual Leave", "leave_tone": "blue", "duration": "2 Days", "dates": "May 24, 2024 - May 25, 2024", "reason": "Short vacation", "status": "Pending", "status_tone": "orange", "applied_on": "May 18, 2024", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "leave_type": "Paternity Leave", "leave_tone": "cyan", "duration": "7 Days", "dates": "May 27, 2024 - Jun 02, 2024", "reason": "Paternity", "status": "Approved", "status_tone": "green", "applied_on": "May 15, 2024", "photo_url": ""},
    ]


def _overtime_display_rows() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "date": "May 19, 2024 (Sun)", "shift": "Morning", "shift_tone": "blue", "ot_start": "05:45 PM", "ot_end": "08:45 PM", "ot_hours": "03h 00m", "reason": "Server maintenance", "status": "Approved", "status_tone": "green", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "date": "May 19, 2024 (Sun)", "shift": "Morning", "shift_tone": "blue", "ot_start": "06:05 PM", "ot_end": "09:05 PM", "ot_hours": "03h 00m", "reason": "Report preparation", "status": "Approved", "status_tone": "green", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "date": "May 18, 2024 (Sat)", "shift": "Morning", "shift_tone": "blue", "ot_start": "05:30 PM", "ot_end": "07:30 PM", "ot_hours": "02h 00m", "reason": "Month end closing", "status": "Approved", "status_tone": "green", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "date": "May 17, 2024 (Fri)", "shift": "Afternoon", "shift_tone": "orange", "ot_start": "06:30 PM", "ot_end": "09:00 PM", "ot_hours": "02h 30m", "reason": "Campaign launch", "status": "Pending", "status_tone": "orange", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "date": "May 17, 2024 (Fri)", "shift": "Night", "shift_tone": "purple", "ot_start": "06:10 AM", "ot_end": "09:10 AM", "ot_hours": "03h 00m", "reason": "System update", "status": "Approved", "status_tone": "green", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "date": "May 16, 2024 (Thu)", "shift": "Night", "shift_tone": "purple", "ot_start": "06:00 AM", "ot_end": "10:00 AM", "ot_hours": "04h 00m", "reason": "Bug fixes", "status": "Approved", "status_tone": "green", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "date": "May 15, 2024 (Wed)", "shift": "Morning", "shift_tone": "blue", "ot_start": "05:00 PM", "ot_end": "07:00 PM", "ot_hours": "02h 00m", "reason": "Audit support", "status": "Rejected", "status_tone": "red", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "date": "May 15, 2024 (Wed)", "shift": "Afternoon", "shift_tone": "orange", "ot_start": "06:00 PM", "ot_end": "08:30 PM", "ot_hours": "02h 30m", "reason": "Interview support", "status": "Approved", "status_tone": "green", "photo_url": ""},
    ]


def _payroll_display_rows() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "work_days": "23", "gross_pay": "$4,850.00", "deductions": "$842.50", "net_pay": "$4,007.50", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "work_days": "22", "gross_pay": "$4,120.00", "deductions": "$735.25", "net_pay": "$3,384.75", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "work_days": "23", "gross_pay": "$5,200.00", "deductions": "$965.00", "net_pay": "$4,235.00", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "work_days": "22", "gross_pay": "$3,750.00", "deductions": "$620.30", "net_pay": "$3,129.70", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "work_days": "23", "gross_pay": "$4,600.00", "deductions": "$810.40", "net_pay": "$3,789.60", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "work_days": "21", "gross_pay": "$4,250.00", "deductions": "$745.60", "net_pay": "$3,504.40", "payment_method": "Bank Transfer", "status": "Pending", "status_tone": "orange", "action_label": "Process", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "work_days": "23", "gross_pay": "$5,800.00", "deductions": "$1,020.80", "net_pay": "$4,779.20", "payment_method": "Bank Transfer", "status": "Pending", "status_tone": "orange", "action_label": "Process", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "work_days": "22", "gross_pay": "$3,950.00", "deductions": "$690.15", "net_pay": "$3,259.85", "payment_method": "Bank Transfer", "status": "Pending", "status_tone": "orange", "action_label": "Process", "photo_url": ""},
        {"staff_code": "STF009", "first_name": "James", "last_name": "Taylor", "department": "Operations Department", "work_days": "23", "gross_pay": "$4,400.00", "deductions": "$780.50", "net_pay": "$3,619.50", "payment_method": "Cash", "status": "Hold", "status_tone": "red", "action_label": "Review", "photo_url": ""},
        {"staff_code": "STF010", "first_name": "Jessica", "last_name": "Anderson", "department": "Marketing Department", "work_days": "22", "gross_pay": "$3,600.00", "deductions": "$640.25", "net_pay": "$2,959.75", "payment_method": "Bank Transfer", "status": "Hold", "status_tone": "red", "action_label": "Review", "photo_url": ""},
    ]


def _correction_display_rows() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "date": "May 20, 2024 Mon", "shift": "Morning 09:00 AM - 05:00 PM", "correction_type": "Check In", "type_tone": "blue", "requested_change": "09:45 AM → 09:00 AM", "reason": "Traffic due to road construction", "status": "Pending", "status_tone": "orange", "requested_on": "May 20, 2024 10:15 AM", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "date": "May 18, 2024 Sat", "shift": "Afternoon 02:00 PM - 10:00 PM", "correction_type": "Check Out", "type_tone": "purple", "requested_change": "10:30 PM → 10:00 PM", "reason": "System auto logout issue", "status": "Approved", "status_tone": "green", "requested_on": "May 18, 2024 11:05 PM", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "date": "May 17, 2024 Fri", "shift": "Morning 09:00 AM - 05:00 PM", "correction_type": "Check In", "type_tone": "blue", "requested_change": "09:20 AM → 09:00 AM", "reason": "Forgot to check-in on time", "status": "Approved", "status_tone": "green", "requested_on": "May 17, 2024 09:35 AM", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "date": "May 16, 2024 Thu", "shift": "Evening 06:00 PM - 02:00 AM", "correction_type": "Check Out", "type_tone": "purple", "requested_change": "02:30 AM → 02:00 AM", "reason": "Had to finish urgent work", "status": "Pending", "status_tone": "orange", "requested_on": "May 16, 2024 02:35 AM", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "date": "May 15, 2024 Wed", "shift": "Morning 09:00 AM - 05:00 PM", "correction_type": "Check In", "type_tone": "blue", "requested_change": "09:30 AM → 09:00 AM", "reason": "Biometric device not working", "status": "Rejected", "status_tone": "red", "requested_on": "May 15, 2024 09:40 AM", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "date": "May 14, 2024 Tue", "shift": "Morning 09:00 AM - 05:00 PM", "correction_type": "Check Out", "type_tone": "purple", "requested_change": "05:45 PM → 05:00 PM", "reason": "System delay in check-out", "status": "Approved", "status_tone": "green", "requested_on": "May 14, 2024 06:10 PM", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "date": "May 13, 2024 Mon", "shift": "Afternoon 02:00 PM - 10:00 PM", "correction_type": "Check In", "type_tone": "blue", "requested_change": "02:25 PM → 02:00 PM", "reason": "Was in client meeting", "status": "Pending", "status_tone": "orange", "requested_on": "May 13, 2024 02:30 PM", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "date": "May 12, 2024 Sun", "shift": "Off Day", "correction_type": "Full Day", "type_tone": "orange", "requested_change": "Absent → Present", "reason": "Worked from office", "status": "Approved", "status_tone": "green", "requested_on": "May 12, 2024 09:00 AM", "photo_url": ""},
    ]


def _report_detail_rows() -> list[dict[str, Any]]:
    return [
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "total_days": 23, "present": 20, "absent": 1, "late": 2, "half_day": 0, "overtime": "12:30", "avg_hours": "8h 45m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "total_days": 23, "present": 21, "absent": 0, "late": 1, "half_day": 1, "overtime": "08:15", "avg_hours": "8h 20m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "total_days": 23, "present": 18, "absent": 2, "late": 2, "half_day": 1, "overtime": "15:45", "avg_hours": "8h 10m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "total_days": 23, "present": 19, "absent": 1, "late": 3, "half_day": 0, "overtime": "10:30", "avg_hours": "8h 30m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "total_days": 23, "present": 17, "absent": 3, "late": 2, "half_day": 1, "overtime": "18:20", "avg_hours": "8h 05m", "grade": "Fair", "grade_tone": "orange", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "total_days": 23, "present": 21, "absent": 0, "late": 1, "half_day": 1, "overtime": "05:30", "avg_hours": "8h 40m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "total_days": 23, "present": 20, "absent": 1, "late": 1, "half_day": 1, "overtime": "09:00", "avg_hours": "8h 25m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "total_days": 23, "present": 18, "absent": 3, "late": 1, "half_day": 1, "overtime": "07:15", "avg_hours": "8h 15m", "grade": "Fair", "grade_tone": "orange", "photo_url": ""},
        {"staff_code": "STF009", "first_name": "James", "last_name": "Taylor", "department": "Operations Department", "total_days": 23, "present": 16, "absent": 4, "late": 2, "half_day": 1, "overtime": "20:10", "avg_hours": "8h 00m", "grade": "Poor", "grade_tone": "red", "photo_url": ""},
        {"staff_code": "STF010", "first_name": "Jessica", "last_name": "Anderson", "department": "Marketing Department", "total_days": 23, "present": 21, "absent": 0, "late": 2, "half_day": 0, "overtime": "04:45", "avg_hours": "8h 35m", "grade": "Good", "grade_tone": "green", "photo_url": ""},
    ]
