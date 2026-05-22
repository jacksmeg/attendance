from __future__ import annotations

from collections import defaultdict
from base64 import b64decode
import csv
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
import json
import math
from pathlib import Path
import struct
from typing import Any, Mapping
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
from .services.notifications import (
    count_unread_notifications,
    create_notification,
    create_notification_for_database,
    format_notification_rows,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
    notification_category_options,
)
from .services.payroll import (
    PAYROLL_STATUS_HOLD,
    PAYROLL_STATUS_PENDING,
    PAYROLL_STATUS_PROCESSED,
    get_payroll_status_map,
    normalize_payroll_month,
    payroll_month_bounds,
    set_payroll_status,
    set_payroll_status_bulk,
)
from .services.qr_codes import build_qr_svg
from .services.reporting import (
    attendance_rows_to_csv,
    build_report_snapshot,
    report_views_to_csv,
)
from .services.seed import seed_demo_data
from .services.selfie_audits import create_staff_selfie_audit, list_staff_selfie_audits
from .services.settings import (
    WORKDAY_OPTIONS,
    admin_password_matches,
    get_admin_security,
    get_admin_security_for_database,
    get_app_settings,
    get_app_settings_for_database,
    save_admin_password,
    save_admin_credentials_for_database,
    save_app_settings,
)
from .services.shifts import (
    assign_shift_to_staff,
    create_shift,
    delete_shift,
    ensure_default_shifts,
    get_shift,
    list_shift_assignments,
    list_shifts,
    set_shift_active,
    unassign_shift_from_staff,
    update_shift,
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
    reset_staff_credentials,
    rotate_staff_qr_token,
    update_staff_access_role,
    update_staff,
    upsert_fingerprint,
    verify_staff_reset_identity,
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
    apply_organization_license_action,
    count_organization_hostnames,
    count_organizations,
    count_organizations_by_license,
    get_current_organization_access_state,
    get_organization_access_state,
    get_organization_by_slug,
    list_organization_license_events,
    list_organizations,
    provision_organization,
    record_organization_license_event,
    update_organization,
)

STAFF_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
REPORT_ATTENDANCE_GROUPS = {
    "all": "All Records",
    "present": "Present",
    "late": "Late",
    "checked_in": "Checked In",
    "checked_out": "Checked Out",
}
PAYROLL_FILTER_STATUSES = {
    "all": "All Employees",
    "processed": "Processed",
    "pending": "Pending",
    "hold": "Hold",
}
PAYMENT_METHOD_CHOICES = [
    "Bank Transfer",
    "Mobile Money",
    "Cash",
    "Cheque",
]
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
            "app_name": settings.app_name,
            "product_name": settings.app_name,
            "software_version": settings.software_version,
            "copyright_notice": settings.copyright_notice,
            "institution_name": live_settings["organization_name"],
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
        if is_platform_admin():
            return redirect(url_for("app.platform_organizations"))
        if session.get("staff_authenticated"):
            return redirect(url_for("app.staff_home"))
        if session.get("admin_authenticated"):
            return redirect(url_for("app.admin_dashboard"))
        if current_app.config["APP_SETTINGS"].fingerprint_backend == "disabled":
            return redirect(url_for("app.staff_login"))
        return redirect(url_for("app.kiosk"))

    @bp.route("/platform/login", methods=["GET", "POST"])
    def platform_login():
        if is_platform_admin():
            return redirect(url_for("app.platform_organizations"))
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
            title="Platform Control",
            next_url=request.args.get("next", ""),
            body_class="platform-auth-body",
        )

    @bp.route("/portal/<slug>/admin/login")
    def portal_admin_login(slug: str):
        organization = get_organization_by_slug(current_app.config["APP_SETTINGS"], slug)
        if not organization:
            flash("That institution portal could not be found.", "error")
            return redirect(url_for("app.home"))
        session["portal_organization_slug"] = organization.slug
        session["organization_slug"] = organization.slug
        session["pending_organization_slug"] = organization.slug
        return redirect(url_for("app.admin_login"))

    @bp.route("/portal/<slug>/staff/login")
    def portal_staff_login(slug: str):
        organization = get_organization_by_slug(current_app.config["APP_SETTINGS"], slug)
        if not organization:
            flash("That institution portal could not be found.", "error")
            return redirect(url_for("app.home"))
        session["portal_organization_slug"] = organization.slug
        session["organization_slug"] = organization.slug
        session["pending_organization_slug"] = organization.slug
        return redirect(url_for("app.staff_login"))

    @bp.route("/portal/<slug>/staff/recover")
    def portal_staff_recover(slug: str):
        organization = get_organization_by_slug(current_app.config["APP_SETTINGS"], slug)
        if not organization:
            flash("That institution portal could not be found.", "error")
            return redirect(url_for("app.home"))
        session["portal_organization_slug"] = organization.slug
        session["organization_slug"] = organization.slug
        session["pending_organization_slug"] = organization.slug
        mode = request.args.get("mode", "").strip()
        if mode:
            return redirect(url_for("app.staff_recover_credentials", mode=mode))
        return redirect(url_for("app.staff_recover_credentials"))

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
        product_name = current_app.config["APP_SETTINGS"].app_name
        organization = _resolve_pwa_request_organization()
        live_settings = _get_app_settings_for_organization(
            organization,
            default_app_name=organization.display_name or product_name,
        )
        institution_name = live_settings["organization_name"]
        tenant_scoped_install = bool(institution_name and institution_name != product_name)
        icon_query = {"org": organization.slug} if tenant_scoped_install else {}
        if tenant_scoped_install:
            manifest_name = f"{institution_name} Staff App"
            short_name = _pwa_short_name(f"{institution_name} Staff")
            manifest_id = url_for("app.portal_staff_login", slug=organization.slug)
            start_url = url_for("app.portal_staff_login", slug=organization.slug)
            staff_login_url = url_for("app.portal_staff_login", slug=organization.slug)
            admin_login_url = url_for("app.portal_admin_login", slug=organization.slug)
            description = (
                f"{institution_name} staff mobile app powered by {product_name} "
                "for attendance, QR access, and staff self-service."
            )
        else:
            manifest_name = product_name
            short_name = _pwa_short_name(product_name)
            manifest_id = url_for("app.home")
            start_url = url_for("app.home")
            staff_login_url = url_for("app.staff_login")
            admin_login_url = url_for("app.admin_login")
            description = (
                f"{product_name} mobile attendance portal for "
                f"{institution_name} staff clocking, QR access, and attendance status."
            )
        manifest = {
            "name": manifest_name,
            "short_name": short_name,
            "id": manifest_id,
            "start_url": start_url,
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#11161f",
            "theme_color": "#2f6bff",
                "description": description,
                "icons": [
                    {
                        "src": url_for("app.pwa_icon_png", size=180, **icon_query),
                        "sizes": "180x180",
                        "type": "image/png",
                        "purpose": "any",
                    },
                    {
                        "src": url_for("app.pwa_icon_png", size=192, **icon_query),
                        "sizes": "192x192",
                        "type": "image/png",
                        "purpose": "any maskable",
                    },
                    {
                        "src": url_for("app.pwa_icon_png", size=512, **icon_query),
                        "sizes": "512x512",
                        "type": "image/png",
                        "purpose": "any maskable",
                    },
                ],
                "shortcuts": [
                    {
                        "name": "Staff Login",
                        "short_name": "Login",
                        "url": staff_login_url,
                        "icons": [{"src": url_for("app.pwa_icon_png", size=192, **icon_query), "sizes": "192x192"}],
                    },
                    {
                        "name": "My Attendance",
                        "short_name": "Attendance",
                        "url": url_for("app.staff_home"),
                        "icons": [{"src": url_for("app.pwa_icon_png", size=192, **icon_query), "sizes": "192x192"}],
                    },
                    {
                        "name": "Admin Login",
                        "short_name": "Admin",
                        "url": admin_login_url,
                        "icons": [{"src": url_for("app.pwa_icon_png", size=192, **icon_query), "sizes": "192x192"}],
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
        organization = _resolve_pwa_request_organization()
        product_name = current_app.config["APP_SETTINGS"].app_name
        live_settings = _get_app_settings_for_organization(
            organization,
            default_app_name=organization.display_name or product_name,
        )
        institution_name = live_settings["organization_name"]
        tenant_scoped_install = bool(institution_name and institution_name != product_name)
        icon_query = {"org": organization.slug} if tenant_scoped_install else {}
        precache_urls = [
            url_for("app.home"),
            url_for("app.staff_login"),
            url_for("app.admin_login"),
            url_for("app.pwa_offline"),
            url_for("static", filename="styles.css"),
            url_for("static", filename="admin_styles.css"),
            url_for("static", filename="pwa.js"),
            url_for("static", filename="theme.js"),
            url_for("app.pwa_icon_png", size=180, **icon_query),
            url_for("app.pwa_icon_png", size=192, **icon_query),
            url_for("app.pwa_icon_png", size=512, **icon_query),
        ]
        if tenant_scoped_install:
            precache_urls.extend(
                [
                    url_for("app.portal_staff_login", slug=organization.slug),
                    url_for("app.portal_admin_login", slug=organization.slug),
                ]
            )
        cache_name = f"attendance-pwa-{organization.slug}-{current_app.config['APP_SETTINGS'].fingerprint_backend}"
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
        organization = _resolve_pwa_request_organization()
        live_settings = _get_app_settings_for_organization(
            organization,
            default_app_name=organization.display_name or current_app.config["APP_SETTINGS"].app_name,
        )
        return Response(
            _generate_pwa_icon_png(size=size, organization=organization, live_settings=live_settings),
            mimetype="image/png",
            headers={"Cache-Control": "public, max-age=3600"},
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
        _notify_attendance_result(result, action_url=url_for("app.admin_attendance"))
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
        _notify_attendance_result(result, action_url=url_for("app.admin_attendance"))
        flash(
            f"{result['staff_name']} recorded a {result['event_type'].replace('_', ' ')} successfully.",
            "success",
        )
        return redirect(url_for("app.kiosk"))

    @bp.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if is_platform_admin():
            flash("Platform sessions use the standalone platform control room.", "warning")
            return redirect(url_for("app.platform_organizations"))
        settings = current_app.config["APP_SETTINGS"]
        organization = get_current_organization()
        live_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        admin_security = get_admin_security(default_username=settings.admin_username)
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            next_url = request.form.get("next") or url_for("app.admin_dashboard")
            if (
                username == admin_security["admin_username"]
                and admin_password_matches(password, settings.admin_password)
            ):
                start_institution_admin_session(
                    username,
                    live_settings["organization_name"],
                    organization_slug=organization.slug,
                )
                log_admin_activity(
                    actor_type="user",
                    actor_name=live_settings["organization_name"] or username,
                    actor_role="Institution Admin",
                    event_type="admin_login",
                    details="Institution administrator signed in successfully.",
                    ip_address=_request_ip_address(),
                    device_name=_request_device_name(),
                )
                _notify_admin(
                    title="Institution administrator signed in",
                    message=(
                        f"{live_settings['organization_name'] or username} started an admin session "
                        f"from {_request_device_name()}."
                    ),
                    category="security",
                    tone="neutral",
                    action_url=url_for("app.admin_audit_logs"),
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
        if is_platform_admin():
            flash("Platform sessions use the standalone platform control room.", "warning")
            return redirect(url_for("app.platform_organizations"))
        organization = get_current_organization()
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
                start_staff_session(staff, organization_slug=organization.slug)
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

    @bp.route("/staff/recover", methods=["GET", "POST"])
    def staff_recover_credentials():
        organization = get_current_organization()
        form_values = {
            "staff_identifier": request.args.get("identifier", "").strip(),
            "date_of_birth": "",
            "phone": "",
            "email": "",
            "reset_mode": request.args.get("mode", "password").strip().lower() or "password",
            "new_password": "",
            "confirm_new_password": "",
            "new_pin": "",
            "confirm_new_pin": "",
        }

        if request.method == "POST":
            form_values = _read_staff_recovery_form(request.form)
            validation_error = _validate_staff_recovery_form(form_values)
            if validation_error:
                flash(validation_error, "error")
            else:
                staff = verify_staff_reset_identity(
                    login_identifier=form_values["staff_identifier"],
                    date_of_birth=form_values["date_of_birth"],
                    phone=form_values["phone"],
                    email=form_values["email"],
                )
                if not staff:
                    flash(
                        "We could not verify that account with the details provided. Use your exact date of birth and a registered phone number or email address.",
                        "error",
                    )
                else:
                    password_value = form_values["new_password"] if form_values["reset_mode"] in {"password", "both"} else ""
                    pin_value = form_values["new_pin"] if form_values["reset_mode"] in {"pin", "both"} else ""
                    reset_staff_credentials(
                        int(staff["id"]),
                        password=password_value,
                        pin=pin_value,
                    )
                    full_name = f"{staff.get('first_name', '')} {staff.get('last_name', '')}".strip() or str(
                        staff.get("staff_code", "Staff")
                    )
                    reset_target = "password and PIN" if form_values["reset_mode"] == "both" else form_values["reset_mode"].upper()
                    log_admin_activity(
                        actor_type="staff",
                        actor_name=full_name,
                        actor_role=str(staff.get("access_role", STAFF)),
                        event_type="staff_self_service_reset",
                        target_name=reset_target,
                        details="Staff completed a self-service credential reset after identity verification.",
                        ip_address=request.headers.get("X-Forwarded-For", request.remote_addr or "")[:255],
                        device_name=_request_device_name(),
                    )
                    _notify_admin(
                        title=f"{full_name} reset {reset_target}",
                        message="Self-service credential recovery completed after identity verification.",
                        category="security",
                        tone="warning",
                        action_url=url_for("app.admin_audit_logs"),
                        target_staff_id=int(staff["id"]),
                    )
                    flash("Your access details were reset successfully. Sign in with your new password or PIN.", "success")
                    return redirect(url_for("app.staff_login"))

        return render_template(
            "staff/recover.html",
            title="Reset Staff Access",
            form_values=form_values,
            body_class="staff-login-minimal-body",
        )

    @bp.route("/logout")
    def logout():
        was_staff = bool(session.get("staff_authenticated"))
        was_admin = bool(session.get("admin_authenticated"))
        was_platform_admin = is_platform_admin()
        organization_slug = str(
            session.get("portal_organization_slug")
            or session.get("organization_slug")
            or session.get("pending_organization_slug")
            or ""
        ).strip()
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
            return redirect(_portal_aware_login_url("staff", organization_slug))
        if was_admin:
            return redirect(_portal_aware_login_url("admin", organization_slug))
        if current_app.config["APP_SETTINGS"].fingerprint_backend == "disabled":
            return redirect(url_for("app.staff_login"))
        return redirect(url_for("app.kiosk"))

    @bp.route("/platform/logout")
    def platform_logout():
        if is_platform_admin():
            clear_user_session()
            flash("Platform session closed.", "success")
        return redirect(url_for("app.platform_login"))

    @bp.route("/platform/organizations", methods=["GET", "POST"])
    @platform_admin_required
    def platform_organizations():
        settings = current_app.config["APP_SETTINGS"]
        valid_sections = {"dashboard", "organizations", "licenses", "backups", "create"}
        section = str(request.values.get("section", request.args.get("section", "organizations")) or "").strip().lower() or "organizations"
        if section not in valid_sections:
            section = "organizations"
        selected_slug = request.values.get("organization", request.args.get("organization", "")).strip().lower()
        create_form = {
            "slug": "",
            "display_name": "",
            "hostnames": "",
            "is_default": False,
            "admin_username": "admin",
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
            "grace_days": "0",
        }
        update_forms: dict[str, dict[str, Any]] = {}

        if request.method == "POST":
            return_section = str(request.form.get("section", section) or "").strip().lower() or section
            if return_section not in valid_sections:
                return_section = section
            action = request.form.get("action", "create").strip().lower()
            if action == "create":
                section = "create"
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
                            grace_days=create_form["grace_days"],
                        )
                        init_db(created_organization.database_path)
                        save_admin_credentials_for_database(
                            created_organization.database_path,
                            username=create_form["admin_username"],
                            password=create_form["admin_password"],
                        )
                        record_organization_license_event(
                            settings,
                            slug=created_organization.slug,
                            event_type="provisioned",
                            title="Institution provisioned",
                            actor_name=current_display_name() or "Platform Super Admin",
                            details=(
                                f"{create_form['display_name']} was provisioned on the "
                                f"{create_form['plan_name']} plan."
                            ),
                            next_status=created_organization.license_status,
                            next_expires_on=created_organization.expires_on,
                            amount=created_organization.subscription_amount,
                        )
                    except ValueError as exc:
                        flash(str(exc), "error")
                    else:
                        flash(
                            f"{create_form['display_name']} was provisioned successfully.",
                            "success",
                        )
                        return redirect(
                            url_for(
                                "app.platform_organizations",
                                section="organizations",
                                organization=created_organization.slug,
                            )
                        )
            elif action == "update":
                slug = request.form.get("organization_slug", "").strip()
                section = return_section
                selected_slug = slug.lower()
                previous_organization = get_organization_by_slug(settings, slug)
                if not previous_organization:
                    flash("The selected institution could not be found.", "error")
                else:
                    admin_security = get_admin_security_for_database(
                        previous_organization.database_path,
                        default_username=current_app.config["APP_SETTINGS"].admin_username,
                    )
                    update_form = _read_platform_organization_form(request.form)
                    update_form = _hydrate_platform_update_form(
                        update_form,
                        request.form,
                        previous_organization,
                        admin_security["admin_username"],
                    )
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
                                grace_days=update_form["grace_days"],
                            )
                            if update_form["admin_password"]:
                                save_admin_credentials_for_database(
                                    updated_organization.database_path,
                                    username=update_form["admin_username"],
                                    password=update_form["admin_password"],
                                )
                            else:
                                save_admin_credentials_for_database(
                                    updated_organization.database_path,
                                    username=update_form["admin_username"],
                                )
                            if _license_fields_changed(previous_organization, update_form):
                                record_organization_license_event(
                                    settings,
                                    slug=updated_organization.slug,
                                    event_type="license_profile_updated",
                                    title="License profile updated",
                                    actor_name=current_display_name() or "Platform Super Admin",
                                    details=(
                                        f"Plan set to {updated_organization.plan_name}; "
                                        f"billing cycle {updated_organization.billing_cycle}; "
                                        f"grace period {updated_organization.grace_days} day(s)."
                                    ),
                                    previous_status=previous_organization.license_status,
                                    next_status=updated_organization.license_status,
                                    previous_expires_on=previous_organization.expires_on,
                                    next_expires_on=updated_organization.expires_on,
                                    amount=updated_organization.subscription_amount,
                                )
                        except ValueError as exc:
                            flash(str(exc), "error")
                        else:
                            flash(
                                f"{update_form['display_name']} was updated successfully.",
                                "success",
                            )
                            return redirect(
                                url_for(
                                    "app.platform_organizations",
                                    section=return_section,
                                    organization=slug,
                                )
                            )
            elif action == "license_action":
                slug = request.form.get("organization_slug", "").strip()
                section = return_section
                selected_slug = slug.lower()
                license_action = request.form.get("license_action", "").strip().lower()
                if not slug:
                    flash("Choose an institution before applying a license action.", "error")
                else:
                    try:
                        updated_organization = apply_organization_license_action(
                            settings,
                            slug=slug,
                            action=license_action,
                            actor_name=current_display_name() or "Platform Super Admin",
                        )
                    except ValueError as exc:
                        flash(str(exc), "error")
                    else:
                        flash(
                            f"{updated_organization.display_name}: {license_action.replace('_', ' ').title()} applied.",
                            "success",
                        )
                        return redirect(
                            url_for(
                                "app.platform_organizations",
                                section=return_section,
                                organization=slug,
                            )
                        )
            elif action == "create_backup":
                slug = request.form.get("organization_slug", "").strip()
                section = return_section
                selected_slug = slug.lower()
                target_organization = get_organization_by_slug(settings, slug)
                if not target_organization:
                    flash("The selected institution could not be found.", "error")
                else:
                    backup_path = create_organization_backup(
                        target_organization,
                        reason="manual",
                        note="Created from the platform super admin portal.",
                    )
                    _notify_admin_for_database(
                        target_organization.database_path,
                        title="Manual backup created",
                        message=f"A platform backup snapshot was created: {backup_path.name}.",
                        category="system",
                        tone="info",
                        action_url=url_for("app.admin_notifications"),
                    )
                    flash(
                        f"Backup created for {target_organization.display_name}: {backup_path.name}",
                        "success",
                    )
                    return redirect(
                        url_for(
                            "app.platform_organizations",
                            section=return_section,
                            organization=slug,
                        )
                    )
            elif action == "restore_backup":
                slug = request.form.get("organization_slug", "").strip()
                backup_name = request.form.get("backup_name", "").strip()
                section = return_section
                selected_slug = slug.lower()
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
                        _notify_admin_for_database(
                            target_organization.database_path,
                            title="Institution restored from backup",
                            message=(
                                f"The platform restored this institution from {backup_name}. "
                                f"A safety snapshot was saved as {pre_restore_backup.name}."
                            ),
                            category="system",
                            tone="warning",
                            action_url=url_for("app.admin_notifications"),
                        )
                        flash(
                            f"{target_organization.display_name} was restored from {backup_name}. "
                            f"A safety snapshot was saved as {pre_restore_backup.name}.",
                            "success",
                        )
                        return redirect(
                            url_for(
                                "app.platform_organizations",
                                section=return_section,
                                organization=slug,
                            )
                        )

        organizations = list_organizations(settings)
        ensure_automatic_backups(organizations)
        organizations = list_organizations(settings)
        rows = _platform_organization_rows(organizations, update_forms=update_forms)
        if not selected_slug and rows:
            selected_slug = str(rows[0]["slug"]).lower()
        expiring_soon = sum(
            1
            for row in rows
            if row["access_state"]["state"] in {"active", "trial", "expiring"}
            and row["access_state"]["days_remaining"] is not None
            and 0 <= int(row["access_state"]["days_remaining"]) <= 14
        )
        grace_active = sum(1 for row in rows if row["access_state"]["state"] == "grace")
        total_backups = sum(len(row["backups"]) for row in rows)
        customers_with_backups = sum(1 for row in rows if row["backups"])
        monthly_revenue = sum(
            row["subscription_amount"]
            for row in rows
            if row["access_state"]["status"] in {LICENSE_STATUS_ACTIVE, LICENSE_STATUS_TRIAL}
        )
        if section in {"organizations", "licenses", "backups"} and not selected_slug and rows:
            selected_slug = str(rows[0]["slug"]).lower()
        expiring_watchlist = sorted(
            [
                row
                for row in rows
                if row["access_state"]["days_remaining"] is not None
                and 0 <= int(row["access_state"]["days_remaining"]) <= 14
                and row["access_state"]["state"] in {"active", "trial", "expiring"}
            ],
            key=lambda row: int(row["access_state"]["days_remaining"] or 9999),
        )[:6]
        blocked_watchlist = [
            row
            for row in rows
            if row["access_state"]["state"] in {"expired", "suspended"}
        ][:6]
        trial_watchlist = [
            row
            for row in rows
            if row["access_state"]["status"] == LICENSE_STATUS_TRIAL
        ][:6]
        recent_backups = [
            row
            for row in rows
            if row["latest_backup"]
        ][:6]
        section_map = {
            "dashboard": {
                "page_title": "Platform Dashboard",
                "breadcrumbs": ["Platform", "Dashboard"],
                "hero_title": "Run every customer workspace from one polished command center.",
                "hero_copy": "Watch customer health, license urgency, backups, and onboarding momentum without digging through overloaded forms.",
                "kicker": "Platform Command Center",
                "initial_tab": "overview",
            },
            "organizations": {
                "page_title": "Organizations",
                "breadcrumbs": ["Platform", "Organizations"],
                "hero_title": "Manage customer workspaces with a cleaner institution directory.",
                "hero_copy": "Open a customer workspace, hand over access details, and keep identity, portal, and domain mapping organized.",
                "kicker": "Institution Directory",
                "initial_tab": "overview",
            },
            "licenses": {
                "page_title": "Licenses",
                "breadcrumbs": ["Platform", "Licenses"],
                "hero_title": "Keep subscriptions, renewals, and grace windows under control.",
                "hero_copy": "Focus on commercial health with brighter license cards, faster actions, and a cleaner review flow.",
                "kicker": "License Command Center",
                "initial_tab": "license",
            },
            "backups": {
                "page_title": "Backups",
                "breadcrumbs": ["Platform", "Backups"],
                "hero_title": "Protect each institution with clean backup and restore coverage.",
                "hero_copy": "Monitor recovery readiness, create snapshots on demand, and restore customers from one dedicated workspace.",
                "kicker": "Backup Operations",
                "initial_tab": "backups",
            },
            "create": {
                "page_title": "Add Organization",
                "breadcrumbs": ["Platform", "Add Organization"],
                "hero_title": "Provision a new institution with everything ready from day one.",
                "hero_copy": "Create the workspace, assign the first admin, set commercial terms, and hand over a clean portal in one guided step.",
                "kicker": "New Workspace",
                "initial_tab": "overview",
            },
        }
        section_meta = section_map[section]
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
            "expiring_soon": expiring_soon,
            "grace_active": grace_active,
            "backups": total_backups,
            "customers_with_backups": customers_with_backups,
            "monthly_revenue": monthly_revenue,
            "monthly_revenue_label": f"GH₵{monthly_revenue:,.2f}",
            "backup_coverage": round((customers_with_backups / len(rows)) * 100) if rows else 0,
        }
        return render_template(
            "platform/organizations.html",
            title=section_meta["page_title"],
            create_form=create_form,
            organizations=rows,
            platform_stats=stats,
            selected_organization_slug=selected_slug,
            platform_section=section,
            platform_section_meta=section_meta,
            platform_initial_tab=section_meta["initial_tab"],
            platform_dashboard={
                "expiring": expiring_watchlist,
                "blocked": blocked_watchlist,
                "trial": trial_watchlist,
                "recent_backups": recent_backups,
            },
            license_status_options=_platform_license_status_options(),
            billing_cycle_options=_platform_billing_cycle_options(),
            **_platform_context(
                section_meta["page_title"],
                section,
                section_meta["breadcrumbs"],
                body_class=f"platform-section-{section}",
            ),
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
            payment_method_choices=PAYMENT_METHOD_CHOICES,
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
            payment_method_choices=PAYMENT_METHOD_CHOICES,
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

    @bp.route("/admin/shift-management", methods=["GET", "POST"])
    @roles_required(*REPORTING_ROLES)
    def admin_shift_management():
        department_scope = current_department_scope()
        ensure_default_shifts()
        search = request.values.get("search", "").strip()
        selected_shift_id = request.values.get("shift_id", "").strip()
        selected_shift_id_int = int(selected_shift_id) if selected_shift_id.isdigit() else None

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            try:
                if action == "create_shift":
                    shift_id = create_shift(_read_shift_form(request.form))
                    flash("Shift created successfully.", "success")
                    return redirect(
                        url_for("app.admin_shift_management", shift_id=shift_id, search=search)
                    )
                if action == "update_shift" and selected_shift_id_int:
                    update_shift(selected_shift_id_int, _read_shift_form(request.form))
                    flash("Shift updated successfully.", "success")
                    return redirect(
                        url_for("app.admin_shift_management", shift_id=selected_shift_id_int, search=search)
                    )
                if action == "toggle_shift" and selected_shift_id_int:
                    should_activate = request.form.get("next_state", "0").strip() == "1"
                    set_shift_active(selected_shift_id_int, should_activate)
                    flash(
                        "Shift activated successfully." if should_activate else "Shift deactivated successfully.",
                        "success",
                    )
                    return redirect(
                        url_for("app.admin_shift_management", shift_id=selected_shift_id_int, search=search)
                    )
                if action == "delete_shift" and selected_shift_id_int:
                    delete_shift(selected_shift_id_int)
                    flash("Shift deleted successfully.", "success")
                    return redirect(url_for("app.admin_shift_management", search=search))
                if action == "assign_staff" and selected_shift_id_int:
                    staff_id = int(request.form.get("staff_id", "0"))
                    if assign_shift_to_staff(selected_shift_id_int, staff_id, department_scope=department_scope):
                        flash("Staff assigned to shift successfully.", "success")
                    else:
                        flash("Could not assign that staff member to the selected shift.", "error")
                    return redirect(
                        url_for("app.admin_shift_management", shift_id=selected_shift_id_int, search=search)
                    )
                if action == "remove_assignment":
                    staff_id = int(request.form.get("staff_id", "0"))
                    if unassign_shift_from_staff(staff_id, department_scope=department_scope):
                        flash("Staff removed from shift successfully.", "success")
                    else:
                        flash("Could not remove that staff member from the shift.", "error")
                    return redirect(
                        url_for("app.admin_shift_management", shift_id=selected_shift_id_int or "", search=search)
                    )
            except (ValueError, sqlite3.IntegrityError) as exc:
                flash(str(exc), "error")

        return render_template(
            "admin/shift_management.html",
            title="Shift Management",
            shift_model=_hospital_shift_management_model(
                department_scope=department_scope,
                search=search,
                selected_shift_id=selected_shift_id_int,
            ),
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

    @bp.route("/admin/payroll", methods=["GET", "POST"])
    @roles_required(*REPORTING_ROLES)
    def admin_payroll():
        department_scope = current_department_scope()
        app_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        payroll_month = normalize_payroll_month(request.values.get("payroll_month", ""))
        date_from, date_to = payroll_month_bounds(payroll_month)
        department = _resolve_department_filter(
            request.values.get("department", "").strip(),
            department_scope,
        )
        search = request.values.get("search", "").strip()
        status_filter = _normalize_payroll_filter_status(
            request.values.get("status", "").strip()
        )

        staff_rows = list_staff(
            search=search,
            department=department,
            active_only=True,
            department_scope=department_scope,
        )
        attendance_rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=department,
            search=search,
            department_scope=department_scope,
        )
        activity_rows = _attendance_activity_rows(attendance_rows)
        payroll_all_rows = _build_payroll_rows(
            staff_rows=staff_rows,
            activity_rows=activity_rows,
            payroll_month=payroll_month,
            working_days=app_settings["working_days"],
        )

        redirect_params = {
            "payroll_month": payroll_month,
            "department": department,
            "search": search,
            "status": status_filter,
        }

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            visible_staff_ids = [int(row["staff_id"]) for row in payroll_all_rows]
            try:
                if action == "set_status":
                    staff_id = int(request.form.get("staff_id", "0"))
                    next_status = request.form.get("next_status", "").strip()
                    notes = request.form.get("notes", "").strip()
                    target_row = next((row for row in payroll_all_rows if int(row["staff_id"]) == staff_id), None)
                    if not target_row:
                        raise ValueError("Choose a valid payroll employee.")
                    set_payroll_status(
                        payroll_month,
                        staff_id,
                        next_status,
                        notes=notes,
                    )
                    target_name = f"{target_row['first_name']} {target_row['last_name']}".strip()
                    _notify_admin(
                        title=f"Payroll status updated for {target_name}",
                        message=(
                            f"{_payroll_month_label(payroll_month)} payroll moved to "
                            f"{next_status.title()}{' with notes.' if notes else '.'}"
                        ),
                        category="payroll",
                        tone="activity" if next_status.lower() == PAYROLL_STATUS_PROCESSED.lower() else "warning",
                        action_url=url_for("app.admin_payroll", payroll_month=payroll_month),
                        target_staff_id=staff_id,
                    )
                    flash("Payroll status updated successfully.", "success")
                elif action == "process_visible":
                    if not visible_staff_ids:
                        raise ValueError("There are no payroll employees in the current view.")
                    set_payroll_status_bulk(
                        payroll_month,
                        visible_staff_ids,
                        PAYROLL_STATUS_PROCESSED,
                    )
                    _notify_admin(
                        title="Payroll batch processed",
                        message=(
                            f"{len(visible_staff_ids)} payroll row(s) were marked processed for "
                            f"{_payroll_month_label(payroll_month)}."
                        ),
                        category="payroll",
                        tone="activity",
                        action_url=url_for("app.admin_payroll", payroll_month=payroll_month),
                    )
                    flash("Visible payroll rows were marked as processed.", "success")
                elif action == "hold_visible":
                    if not visible_staff_ids:
                        raise ValueError("There are no payroll employees in the current view.")
                    set_payroll_status_bulk(
                        payroll_month,
                        visible_staff_ids,
                        PAYROLL_STATUS_HOLD,
                    )
                    _notify_admin(
                        title="Payroll batch placed on hold",
                        message=(
                            f"{len(visible_staff_ids)} payroll row(s) were placed on hold for "
                            f"{_payroll_month_label(payroll_month)}."
                        ),
                        category="payroll",
                        tone="warning",
                        action_url=url_for("app.admin_payroll", payroll_month=payroll_month),
                    )
                    flash("Visible payroll rows were placed on hold.", "success")
                elif action == "reset_visible":
                    if not visible_staff_ids:
                        raise ValueError("There are no payroll employees in the current view.")
                    set_payroll_status_bulk(
                        payroll_month,
                        visible_staff_ids,
                        PAYROLL_STATUS_PENDING,
                    )
                    _notify_admin(
                        title="Payroll batch reset to pending",
                        message=(
                            f"{len(visible_staff_ids)} payroll row(s) were reset to pending for "
                            f"{_payroll_month_label(payroll_month)}."
                        ),
                        category="payroll",
                        tone="neutral",
                        action_url=url_for("app.admin_payroll", payroll_month=payroll_month),
                    )
                    flash("Visible payroll rows were reset to pending.", "success")
                else:
                    flash("Choose a valid payroll action.", "error")
            except ValueError as exc:
                flash(str(exc), "error")
            return redirect(url_for("app.admin_payroll", **redirect_params))

        payroll_rows = _filter_payroll_rows(payroll_all_rows, status_filter)
        payroll_model = _payroll_live_model(
            payroll_rows=payroll_rows,
            all_rows=payroll_all_rows,
            payroll_month=payroll_month,
        )
        return render_template(
            "admin/payroll.html",
            title="Payroll",
            payroll_model=payroll_model,
            payroll_rows=payroll_rows,
            departments=list_departments(department_scope=department_scope),
            filters={
                "payroll_month": payroll_month,
                "department": department,
                "search": search,
                "status": status_filter,
            },
            **_admin_context(
                "Payroll",
                "payroll",
                ["Dashboard", "Payroll", f"{_payroll_month_label(payroll_month)} Payroll"],
                nav_secondary="dashboard",
            ),
        )

    @bp.route("/admin/payroll/export.csv")
    @roles_required(*REPORTING_ROLES)
    def admin_payroll_export():
        department_scope = current_department_scope()
        app_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        payroll_month = normalize_payroll_month(request.args.get("payroll_month", ""))
        date_from, date_to = payroll_month_bounds(payroll_month)
        department = _resolve_department_filter(
            request.args.get("department", "").strip(),
            department_scope,
        )
        search = request.args.get("search", "").strip()
        status_filter = _normalize_payroll_filter_status(
            request.args.get("status", "").strip()
        )
        staff_rows = list_staff(
            search=search,
            department=department,
            active_only=True,
            department_scope=department_scope,
        )
        attendance_rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=department,
            search=search,
            department_scope=department_scope,
        )
        activity_rows = _attendance_activity_rows(attendance_rows)
        payroll_rows = _filter_payroll_rows(
            _build_payroll_rows(
                staff_rows=staff_rows,
                activity_rows=activity_rows,
                payroll_month=payroll_month,
                working_days=app_settings["working_days"],
            ),
            status_filter,
        )
        return Response(
            _payroll_rows_to_csv(payroll_rows),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=payroll-{payroll_month}.csv"
            },
        )

    @bp.route("/admin/payroll/summary")
    @roles_required(*REPORTING_ROLES)
    def admin_payroll_summary():
        department_scope = current_department_scope()
        app_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        payroll_month = normalize_payroll_month(request.args.get("payroll_month", ""))
        date_from, date_to = payroll_month_bounds(payroll_month)
        department = _resolve_department_filter(
            request.args.get("department", "").strip(),
            department_scope,
        )
        search = request.args.get("search", "").strip()
        status_filter = _normalize_payroll_filter_status(
            request.args.get("status", "").strip()
        )
        staff_rows = list_staff(
            search=search,
            department=department,
            active_only=True,
            department_scope=department_scope,
        )
        attendance_rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=department,
            search=search,
            department_scope=department_scope,
        )
        activity_rows = _attendance_activity_rows(attendance_rows)
        payroll_all_rows = _build_payroll_rows(
            staff_rows=staff_rows,
            activity_rows=activity_rows,
            payroll_month=payroll_month,
            working_days=app_settings["working_days"],
        )
        payroll_rows = _filter_payroll_rows(payroll_all_rows, status_filter)
        payroll_model = _payroll_live_model(
            payroll_rows=payroll_rows,
            all_rows=payroll_all_rows,
            payroll_month=payroll_month,
        )
        top_earners = sorted(
            payroll_rows,
            key=lambda row: float(row["net_pay_amount"]),
            reverse=True,
        )[:6]
        return render_template(
            "admin/payroll_summary.html",
            title="Payroll Summary",
            payroll_model=payroll_model,
            payroll_rows=payroll_rows,
            top_earners=top_earners,
            filters={
                "payroll_month": payroll_month,
                "department": department,
                "search": search,
                "status": status_filter,
            },
            **_admin_context(
                "Payroll Summary",
                "payroll",
                ["Dashboard", "Payroll", "Summary"],
                nav_secondary="dashboard",
                body_class="payroll-summary-page",
            ),
        )

    @bp.route("/admin/payroll/<int:staff_id>/payslip")
    @roles_required(*REPORTING_ROLES)
    def admin_payroll_payslip(staff_id: int):
        department_scope = current_department_scope()
        app_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        payroll_month = normalize_payroll_month(request.args.get("payroll_month", ""))
        date_from, date_to = payroll_month_bounds(payroll_month)
        staff = _get_manageable_staff(staff_id)
        if not staff:
            return redirect(url_for("app.admin_payroll", payroll_month=payroll_month))

        activity_rows = _attendance_activity_rows(
            [
                row
                for row in list_attendance_events(
                    date_from=date_from,
                    date_to=date_to,
                    department_scope=department_scope,
                )
                if int(row["staff_id"]) == int(staff_id)
            ]
        )
        payroll_rows = _build_payroll_rows(
            staff_rows=[staff],
            activity_rows=activity_rows,
            payroll_month=payroll_month,
            working_days=app_settings["working_days"],
        )
        if not payroll_rows:
            flash("No payroll record is available for that staff member in the selected month.", "warning")
            return redirect(url_for("app.admin_payroll", payroll_month=payroll_month))

        payslip_row = payroll_rows[0]
        return render_template(
            "admin/payroll_payslip.html",
            title="Payslip",
            payslip=payslip_row,
            institution_settings=app_settings,
            payroll_month=payroll_month,
            payroll_month_label=_payroll_month_label(payroll_month),
            **_admin_context(
                "Payroll",
                "payroll",
                ["Dashboard", "Payroll", "Payslip"],
                body_class="payroll-print-page",
            ),
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

    @bp.route("/admin/notifications", methods=["GET", "POST"])
    @roles_required(*REPORTING_ROLES)
    def admin_notifications():
        category_options = notification_category_options()
        valid_categories = {item["key"] for item in category_options}
        category = request.values.get("category", "").strip().lower()
        if category not in valid_categories:
            category = ""
        scope = request.values.get("scope", "all").strip().lower()
        unread_only = scope == "unread"

        if request.method == "POST":
            action = request.form.get("action", "").strip()
            redirect_args: dict[str, str] = {}
            if category:
                redirect_args["category"] = category
            if unread_only:
                redirect_args["scope"] = "unread"

            if action == "mark_read":
                try:
                    notification_id = int(request.form.get("notification_id", "0"))
                except ValueError:
                    flash("Choose a valid notification to mark as read.", "error")
                else:
                    if mark_notification_read(notification_id, audience="admin"):
                        flash("Notification marked as read.", "success")
                    else:
                        flash("That notification could not be updated.", "error")
            elif action == "mark_all_read":
                changed = mark_all_notifications_read(audience="admin")
                if changed:
                    flash(f"{changed} notification(s) were marked as read.", "success")
                else:
                    flash("There were no unread notifications to update.", "info")
            else:
                flash("Choose a valid notification action.", "error")
            return redirect(url_for("app.admin_notifications", **redirect_args))

        raw_rows = list_notifications(
            limit=40,
            audience="admin",
            unread_only=unread_only,
            category=category,
        )
        notification_rows = format_notification_rows(raw_rows)
        all_rows = list_notifications(limit=200, audience="admin")
        category_counts = defaultdict(int)
        unread_counts = defaultdict(int)
        for row in all_rows:
            row_category = str(row.get("category") or "system")
            category_counts[row_category] += 1
            if not row.get("is_read"):
                unread_counts[row_category] += 1
        preference_settings = get_app_settings(default_app_name=_tenant_default_app_name())
        return render_template(
            "admin/notifications.html",
            title="Notifications",
            notification_rows=notification_rows,
            filters={
                "category": category,
                "scope": "unread" if unread_only else "all",
            },
            notification_categories=category_options,
            notification_counts={
                "total": len(all_rows),
                "unread": count_unread_notifications(audience="admin"),
                "by_category": dict(category_counts),
                "unread_by_category": dict(unread_counts),
            },
            notification_preferences=preference_settings,
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
                    is_active=_form_checkbox_checked(request.form, "is_active"),
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
        report_kind = request.args.get("report_kind", "staff").strip().lower()
        attendance_group = _normalize_report_attendance_group(
            request.args.get("attendance_group", "").strip()
        )
        if report_kind not in {"staff", "daily", "department", "exceptions"}:
            report_kind = "staff"
        active_staff_rows = list_staff(
            search=search,
            department=department,
            active_only=True,
            department_scope=department_scope,
        )
        active_staff_total = len(active_staff_rows)
        rows = list_attendance_events(
            date_from=date_from,
            date_to=date_to,
            department=department,
            search=search,
            department_scope=department_scope,
        )
        snapshot = build_report_snapshot(
            rows=rows,
            active_staff_total=active_staff_total,
        )
        activity_rows = _attendance_activity_rows(rows)
        detail_rows = _report_detail_rows_from_activity(
            activity_rows,
            date_from=date_from,
            date_to=date_to,
            staff_rows=active_staff_rows,
        )
        report_views = _report_views_model(
            activity_rows=activity_rows,
            detail_rows=detail_rows,
            staff_rows=active_staff_rows,
            report_kind=report_kind,
            attendance_group=attendance_group,
            active_staff_total=active_staff_total,
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
                active_staff_total=active_staff_total,
                labels=_date_labels_from_range(date_from, date_to),
            ),
            detail_rows=detail_rows,
            departments=list_departments(department_scope=department_scope),
            filters={
                "date_from": date_from,
                "date_to": date_to,
                "department": department,
                "search": search,
                "report_kind": report_kind,
                "attendance_group": attendance_group,
            },
            report_views=report_views,
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
        attendance_group = _normalize_report_attendance_group(
            request.args.get("attendance_group", "").strip()
        )
        active_staff_rows = list_staff(
            search=request.args.get("search", "").strip(),
            department=_resolve_department_filter(request.args.get("department", "").strip(), department_scope),
            active_only=True,
            department_scope=department_scope,
        )
        activity_rows = _attendance_activity_rows(rows)
        detail_rows = _report_detail_rows_from_activity(
            activity_rows,
            date_from=date_from,
            date_to=date_to,
            staff_rows=active_staff_rows,
        )
        report_views = _report_views_model(
            activity_rows=activity_rows,
            detail_rows=detail_rows,
            staff_rows=active_staff_rows,
            report_kind=request.args.get("report_kind", "staff").strip().lower(),
            attendance_group=attendance_group,
            active_staff_total=len(active_staff_rows),
        )
        payload = report_views_to_csv(
            daily_rows=report_views["daily_rows"],
            department_rows=report_views["department_rows"],
            staff_rows=report_views["staff_rows"],
            attendance_group_label=report_views["attendance_group"]["label"],
        )
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
                    _notify_admin(
                        title="Institution admin password changed",
                        message="The institution administrator password was updated from Settings.",
                        category="security",
                        tone="warning",
                        action_url=url_for("app.admin_settings") + "#admin-password-card",
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
                        _notify_admin(
                            title="Institution settings updated",
                            message="Branding, attendance rules, location policy, or notification preferences were updated.",
                            category="system",
                            tone="info",
                            action_url=url_for("app.admin_settings"),
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
            last_attendance_result=session.pop("last_staff_attendance_result", None),
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
        _store_last_staff_attendance_result(
            result,
            today_status=get_staff_today_status(staff["id"]),
            location_policy=_location_policy_view_model(
                get_app_settings(default_app_name=_tenant_default_app_name())
            ),
        )
        _notify_attendance_result(result, action_url=url_for("app.admin_attendance"))
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
            _store_last_staff_attendance_result(
                result,
                today_status=get_staff_today_status(staff["id"]),
                location_policy=_location_policy_view_model(
                    get_app_settings(default_app_name=_tenant_default_app_name())
                ),
            )
            _notify_attendance_result(result, action_url=url_for("app.admin_attendance"))
            return redirect(url_for("app.staff_quick_access", qr_token=qr_token))

        return render_template(
            "staff/quick_access.html",
            title="QR Quick Access",
            staff=staff,
            today_status=today_status,
            today=date.today(),
            last_attendance_result=session.pop("last_staff_attendance_result", None),
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


def _store_last_staff_attendance_result(
    result: dict[str, Any],
    *,
    today_status: dict[str, Any],
    location_policy: dict[str, Any],
) -> None:
    event_type = str(result.get("event_type", "") or "")
    if event_type == "check_out":
        headline = "Attendance Complete!"
        message = "Your work session has been successfully recorded"
        status_badge = "Completed for Today"
        footer_title = "Great work today! Your attendance has been successfully recorded."
    elif event_type == "check_in":
        headline = "Attendance Recorded!"
        message = "Your check-in has been successfully recorded"
        status_badge = "Checked In for Today"
        footer_title = "Your attendance has been recorded successfully."
    elif event_type == "break_start":
        headline = "Break Started!"
        message = "Your break start has been successfully recorded"
        status_badge = "Break in Progress"
        footer_title = "Your break status has been updated successfully."
    else:
        headline = "Break Ended!"
        message = "Your return from break has been successfully recorded"
        status_badge = "Back to Work"
        footer_title = "Your attendance has been updated successfully."

    check_in_at = today_status.get("check_in_at")
    check_out_at = today_status.get("check_out_at")
    worked_minutes = int(today_status.get("worked_minutes") or 0)
    location_line = location_policy.get("address") or location_policy.get("location_name") or "Main Work Location"

    session["last_staff_attendance_result"] = {
        "success_label": "Success",
        "headline": headline,
        "message": message,
        "check_in_time": check_in_at.strftime("%I:%M %p") if check_in_at else "--",
        "check_out_time": check_out_at.strftime("%I:%M %p") if check_out_at else "--",
        "check_in_location": location_line,
        "check_out_location": location_line,
        "worked_hours": f"{worked_minutes / 60:.2f} hours",
        "status_badge": status_badge,
        "footer_title": footer_title,
        "footer_note": "You can view your full attendance history in the reports section.",
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
            "base_salary": float(staff.get("base_salary", 0) or 0),
            "overtime_hourly_rate": float(staff.get("overtime_hourly_rate", 0) or 0),
            "tax_deduction": float(staff.get("tax_deduction", 0) or 0),
            "provident_fund": float(staff.get("provident_fund", 0) or 0),
            "health_insurance": float(staff.get("health_insurance", 0) or 0),
            "other_deduction": float(staff.get("other_deduction", 0) or 0),
            "payment_method": staff.get("payment_method") or "Bank Transfer",
            "bank_name": staff.get("bank_name") or "",
            "account_name": staff.get("account_name") or "",
            "account_number": staff.get("account_number") or "",
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
        "base_salary": 0.0,
        "overtime_hourly_rate": 0.0,
        "tax_deduction": 0.0,
        "provident_fund": 0.0,
        "health_insurance": 0.0,
        "other_deduction": 0.0,
        "payment_method": "Bank Transfer",
        "bank_name": "",
        "account_name": "",
        "account_number": "",
        "portal_password": "",
        "portal_pin": "",
        "regenerate_qr": False,
        "qr_token": "",
    }


def _read_staff_form(form) -> dict[str, Any]:
    grace_minutes = form.get("grace_minutes", "15").strip() or "15"
    base_salary = form.get("base_salary", "0").strip() or "0"
    overtime_hourly_rate = form.get("overtime_hourly_rate", "0").strip() or "0"
    tax_deduction = form.get("tax_deduction", "0").strip() or "0"
    provident_fund = form.get("provident_fund", "0").strip() or "0"
    health_insurance = form.get("health_insurance", "0").strip() or "0"
    other_deduction = form.get("other_deduction", "0").strip() or "0"
    def _safe_float(value: str) -> float | str:
        try:
            return float(value)
        except ValueError:
            return value
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
        "is_active": _form_checkbox_checked(form, "is_active"),
        "allow_mobile_clock": form.get("allow_mobile_clock") == "on",
        "allow_pin_clock": form.get("allow_pin_clock") == "on",
        "allow_qr_clock": form.get("allow_qr_clock") == "on",
        "base_salary": _safe_float(base_salary),
        "overtime_hourly_rate": _safe_float(overtime_hourly_rate),
        "tax_deduction": _safe_float(tax_deduction),
        "provident_fund": _safe_float(provident_fund),
        "health_insurance": _safe_float(health_insurance),
        "other_deduction": _safe_float(other_deduction),
        "payment_method": form.get("payment_method", "Bank Transfer").strip() or "Bank Transfer",
        "bank_name": form.get("bank_name", "").strip(),
        "account_name": form.get("account_name", "").strip(),
        "account_number": form.get("account_number", "").strip(),
        "portal_password": form.get("portal_password", ""),
        "portal_pin": form.get("portal_pin", "").strip(),
        "regenerate_qr": form.get("regenerate_qr") == "on",
    }


def _read_staff_recovery_form(form) -> dict[str, Any]:
    return {
        "staff_identifier": form.get("staff_identifier", "").strip(),
        "date_of_birth": form.get("date_of_birth", "").strip(),
        "phone": form.get("phone", "").strip(),
        "email": form.get("email", "").strip(),
        "reset_mode": form.get("reset_mode", "password").strip().lower() or "password",
        "new_password": form.get("new_password", ""),
        "confirm_new_password": form.get("confirm_new_password", ""),
        "new_pin": form.get("new_pin", "").strip(),
        "confirm_new_pin": form.get("confirm_new_pin", "").strip(),
    }


def _validate_staff_recovery_form(form_values: dict[str, Any]) -> str | None:
    if not form_values.get("staff_identifier"):
        return "Enter your staff number or email address."

    mode = str(form_values.get("reset_mode", "password")).strip().lower()
    if mode not in {"password", "pin", "both"}:
        return "Choose whether you want to reset your password, PIN, or both."

    date_of_birth = str(form_values.get("date_of_birth", "")).strip()
    if not date_of_birth:
        return "Enter your date of birth to verify your identity."
    try:
        date.fromisoformat(date_of_birth)
    except ValueError:
        return "Date of birth must use a valid date."

    if not str(form_values.get("phone", "")).strip() and not str(form_values.get("email", "")).strip():
        return "Provide your registered phone number or registered email address."

    email_value = str(form_values.get("email", "")).strip()
    if email_value and "@" not in email_value:
        return "Enter a valid registered email address."

    if mode in {"password", "both"}:
        password_value = str(form_values.get("new_password", ""))
        confirm_password = str(form_values.get("confirm_new_password", ""))
        if len(password_value) < 8:
            return "New password must be at least 8 characters long."
        if password_value != confirm_password:
            return "New password and confirmation do not match."

    if mode in {"pin", "both"}:
        pin_value = str(form_values.get("new_pin", "")).strip()
        confirm_pin = str(form_values.get("confirm_new_pin", "")).strip()
        if not pin_value.isdigit() or len(pin_value) < 4:
            return "New PIN must be numeric and at least 4 digits long."
        if pin_value != confirm_pin:
            return "New PIN and confirmation do not match."

    return None


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

    if form_values.get("payment_method") not in PAYMENT_METHOD_CHOICES:
        return "Choose a valid payment method for payroll."

    for key, label in (
        ("base_salary", "Base salary"),
        ("overtime_hourly_rate", "Overtime hourly rate"),
        ("tax_deduction", "Tax deduction"),
        ("provident_fund", "Provident fund"),
        ("health_insurance", "Health insurance"),
        ("other_deduction", "Other deduction"),
    ):
        try:
            numeric_value = float(form_values.get(key, 0) or 0)
        except (TypeError, ValueError):
            return f"{label} must be a valid number."
        if numeric_value < 0:
            return f"{label} cannot be negative."

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


def _portal_aware_login_url(login_kind: str, organization_slug: str = "") -> str:
    cleaned_slug = str(organization_slug or "").strip()
    if cleaned_slug:
        if login_kind == "admin":
            return url_for("app.portal_admin_login", slug=cleaned_slug)
        return url_for("app.portal_staff_login", slug=cleaned_slug)
    if login_kind == "admin":
        return url_for("app.admin_login")
    return url_for("app.staff_login")


def _clear_admin_notification_cache() -> None:
    g.pop("_notification_cache", None)


def _notify_admin(
    *,
    title: str,
    message: str,
    category: str = "system",
    tone: str = "neutral",
    action_url: str = "",
    target_staff_id: int | None = None,
) -> None:
    create_notification(
        title=title,
        message=message,
        category=category,
        audience="admin",
        tone=tone,
        action_url=action_url,
        target_staff_id=target_staff_id,
    )
    _clear_admin_notification_cache()


def _notify_admin_for_database(
    database_path: Path,
    *,
    title: str,
    message: str,
    category: str = "system",
    tone: str = "neutral",
    action_url: str = "",
    target_staff_id: int | None = None,
) -> None:
    create_notification_for_database(
        database_path,
        title=title,
        message=message,
        category=category,
        audience="admin",
        tone=tone,
        action_url=action_url,
        target_staff_id=target_staff_id,
    )


def _notify_attendance_result(result: Mapping[str, Any], *, action_url: str) -> None:
    staff_name = str(result.get("staff_name") or "Staff member")
    event_type = str(result.get("event_type") or "attendance").replace("_", " ").title()
    event_time = str(result.get("captured_at_label") or result.get("occurred_at_label") or "just now")
    _notify_admin(
        title=f"{staff_name} recorded {event_type}",
        message=f"{staff_name} completed a {event_type.lower()} event at {event_time}.",
        category="attendance",
        tone="activity",
        action_url=action_url,
        target_staff_id=int(result.get("staff_id") or 0) or None,
    )


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
        "notification_attendance_enabled": form.get("notification_attendance_enabled") == "on",
        "notification_security_enabled": form.get("notification_security_enabled") == "on",
        "notification_payroll_enabled": form.get("notification_payroll_enabled") == "on",
        "notification_system_enabled": form.get("notification_system_enabled") == "on",
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


def _resolve_pwa_request_organization():
    requested_slug = str(request.args.get("org", "") or "").strip()
    if requested_slug:
        requested_organization = get_organization_by_slug(
            current_app.config["APP_SETTINGS"],
            requested_slug,
        )
        if requested_organization is not None:
            return requested_organization
    return get_current_organization()


def _get_app_settings_for_organization(organization, *, default_app_name: str = "") -> dict[str, Any]:
    current_organization = get_current_organization()
    if current_organization.slug == organization.slug:
        return get_app_settings(default_app_name=default_app_name)
    return get_app_settings_for_database(
        organization.database_path,
        default_app_name=default_app_name,
    )


def _generate_pwa_icon_png(*, size: int, organization=None, live_settings: dict[str, Any] | None = None) -> bytes:
    source_path = _pwa_logo_source_path(organization=organization, live_settings=live_settings)
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


def _pwa_logo_source_path(*, organization=None, live_settings: dict[str, Any] | None = None) -> Path | None:
    chosen_organization = organization or get_current_organization()
    chosen_settings = live_settings or _get_app_settings_for_organization(
        chosen_organization,
        default_app_name=chosen_organization.display_name or current_app.config["APP_SETTINGS"].app_name,
    )
    filename = str(chosen_settings.get("system_logo_filename", "") or "").strip()
    if filename:
        if organization is None or chosen_organization.slug == get_current_organization().slug:
            candidate = _system_logo_directory() / filename
        else:
            candidate = chosen_organization.instance_dir / "system" / filename
        if candidate.exists():
            return candidate

    default_candidate = _default_logo_mark_path()
    if default_candidate.exists():
        return default_candidate
    return None


def _form_checkbox_checked(form: Mapping[str, Any], key: str) -> bool:
    value = str(form.get(key, "")).strip().lower()
    return value in {"1", "on", "true", "yes"}


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
    notification_rows = _cached_admin_notification_rows(limit=8)
    unread_count = count_unread_notifications(audience="admin")
    return {
        "page_title": page_title,
        "nav_primary": nav_primary,
        "nav_secondary": nav_secondary,
        "breadcrumbs": breadcrumbs,
        "body_class": body_class,
        "admin_notification_count": unread_count,
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
        admin_security = get_admin_security_for_database(
            organization.database_path,
            default_username=current_app.config["APP_SETTINGS"].admin_username,
        )
        form = update_forms.get(organization.slug) or {
            "slug": organization.slug,
            "display_name": organization.display_name,
            "hostnames": "\n".join(hostnames),
            "hostnames_list": hostnames,
            "is_default": organization.is_default,
            "admin_username": admin_security["admin_username"],
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
            "grace_days": str(organization.grace_days),
        }
        primary_url = ""
        if hostnames:
            primary_url = f"https://{hostnames[0]}"
        platform_admin_login_url = url_for("app.portal_admin_login", slug=organization.slug, _external=True)
        platform_staff_login_url = url_for("app.portal_staff_login", slug=organization.slug, _external=True)
        hostname_admin_login_url = f"{primary_url}/admin/login" if primary_url else ""
        hostname_staff_login_url = f"{primary_url}/staff/login" if primary_url else ""
        access_state = get_organization_access_state(organization)
        access_summary = access_state_summary(access_state)
        backups = list_organization_backups(organization, limit=6)
        license_history = [
            {
                **event,
                "created_label": _format_history_datetime(event.get("created_at", "")),
                "amount_label": f"GH₵{float(event.get('amount') or 0):,.2f}" if event.get("amount") else "",
                "tone": _platform_license_tone(event.get("next_status") or event.get("previous_status") or ""),
            }
            for event in list_organization_license_events(
                current_app.config["APP_SETTINGS"],
                organization.slug,
                limit=12,
            )
        ]
        backup_rows = [
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
        ]
        rows.append(
            {
                "slug": organization.slug,
                "display_name": organization.display_name,
                "is_default": organization.is_default,
                "database_path": str(organization.database_path),
                "instance_dir": str(organization.instance_dir),
                "hostnames": hostnames,
                "primary_url": primary_url,
                "login_url": hostname_admin_login_url,
                "staff_login_url": hostname_staff_login_url,
                "platform_login_url": platform_admin_login_url,
                "platform_staff_login_url": platform_staff_login_url,
                "preferred_admin_login_url": hostname_admin_login_url or platform_admin_login_url,
                "preferred_staff_login_url": hostname_staff_login_url or platform_staff_login_url,
                "admin_username": admin_security["admin_username"],
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
                "grace_days": organization.grace_days,
                "access_state": access_state,
                "access_summary": access_summary,
                "license_health": {
                    "tone": access_summary["tone"],
                    "state_label": access_summary["state_label"],
                    "days_label": access_summary["days_label"] or "No expiry date set",
                    "renewal_label": _format_date_label(organization.renewal_due_on),
                    "renewal_hint": access_summary["renewal_label"],
                    "grace_label": f"{organization.grace_days} day{'s' if organization.grace_days != 1 else ''}",
                    "subscription_label": f"GH₵{organization.subscription_amount:,.2f}",
                    "billing_cycle_label": organization.billing_cycle.replace("-", " ").title(),
                },
                "license_history": license_history,
                "backups": backup_rows,
                "latest_backup": backup_rows[0] if backup_rows else None,
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
        "admin_username": str(form.get("admin_username", "") or "").strip(),
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
        "grace_days": str(form.get("grace_days", "0") or "").strip() or "0",
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
    if not str(form_values.get("admin_username", "")).strip():
        return "Enter the institution admin username."
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
    try:
        if int(str(form_values.get("grace_days", "0")).strip()) < 0:
            return "Grace period must be zero or a positive number of days."
    except ValueError:
        return "Grace period must be a valid whole number."
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


def _hydrate_platform_update_form(
    form_values: dict[str, Any],
    raw_form,
    organization: Any,
    admin_username: str,
) -> dict[str, Any]:
    submitted_keys = set(raw_form.keys())
    overview_keys = {"display_name", "hostnames", "admin_username", "is_default"}

    if "display_name" not in submitted_keys:
        form_values["display_name"] = organization.display_name
    if "hostnames" not in submitted_keys:
        form_values["hostnames_list"] = list(organization.hostnames)
        form_values["hostnames"] = "\n".join(organization.hostnames)
    if "admin_username" not in submitted_keys:
        form_values["admin_username"] = admin_username
    if "plan_name" not in submitted_keys:
        form_values["plan_name"] = organization.plan_name
    if "license_status" not in submitted_keys:
        form_values["license_status"] = organization.license_status
    if "expires_on" not in submitted_keys:
        form_values["expires_on"] = organization.expires_on
    if "billing_contact_name" not in submitted_keys:
        form_values["billing_contact_name"] = organization.billing_contact_name
    if "billing_email" not in submitted_keys:
        form_values["billing_email"] = organization.billing_email
    if "billing_phone" not in submitted_keys:
        form_values["billing_phone"] = organization.billing_phone
    if "billing_cycle" not in submitted_keys:
        form_values["billing_cycle"] = organization.billing_cycle
    if "subscription_amount" not in submitted_keys:
        form_values["subscription_amount"] = f"{organization.subscription_amount:.2f}"
    if "renewal_due_on" not in submitted_keys:
        form_values["renewal_due_on"] = organization.renewal_due_on
    if "last_payment_on" not in submitted_keys:
        form_values["last_payment_on"] = organization.last_payment_on
    if "license_notes" not in submitted_keys:
        form_values["license_notes"] = organization.license_notes
    if "grace_days" not in submitted_keys:
        form_values["grace_days"] = str(organization.grace_days)
    if not (submitted_keys & overview_keys):
        form_values["is_default"] = organization.is_default
    return form_values


def _license_fields_changed(organization: Any, form_values: dict[str, Any]) -> bool:
    return any(
        (
            str(form_values.get("plan_name", "")).strip() != str(organization.plan_name),
            str(form_values.get("license_status", "")).strip() != str(organization.license_status),
            str(form_values.get("expires_on", "")).strip() != str(organization.expires_on),
            str(form_values.get("billing_contact_name", "")).strip() != str(organization.billing_contact_name),
            str(form_values.get("billing_email", "")).strip() != str(organization.billing_email),
            str(form_values.get("billing_phone", "")).strip() != str(organization.billing_phone),
            str(form_values.get("billing_cycle", "")).strip() != str(organization.billing_cycle),
            str(form_values.get("subscription_amount", "")).strip() != f"{organization.subscription_amount:.2f}",
            str(form_values.get("renewal_due_on", "")).strip() != str(organization.renewal_due_on),
            str(form_values.get("last_payment_on", "")).strip() != str(organization.last_payment_on),
            str(form_values.get("license_notes", "")).strip() != str(organization.license_notes),
            str(form_values.get("grace_days", "")).strip() != str(organization.grace_days),
        )
    )


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
    state = str(access_state.get("state") or access_state.get("status") or LICENSE_STATUS_ACTIVE)
    days_label = ""
    if state == "grace":
        grace_remaining = access_state.get("grace_days_remaining")
        if grace_remaining is None:
            days_label = "Grace active"
        elif grace_remaining == 0:
            days_label = "Grace ends today"
        else:
            days_label = f"{grace_remaining} grace day{'s' if grace_remaining != 1 else ''} left"
    elif days_remaining is not None:
        if days_remaining < 0:
            days_label = "Expired"
        elif days_remaining == 0:
            days_label = "Ends today"
        else:
            days_label = f"{days_remaining} day{'s' if days_remaining != 1 else ''} left"
    renewal_days_remaining = access_state.get("renewal_days_remaining")
    renewal_label = ""
    if renewal_days_remaining is not None:
        if renewal_days_remaining < 0:
            renewal_label = "Renewal overdue"
        elif renewal_days_remaining == 0:
            renewal_label = "Renewal due today"
        else:
            renewal_label = f"Renewal in {renewal_days_remaining} day{'s' if renewal_days_remaining != 1 else ''}"
    return {
        "status": access_state.get("status", LICENSE_STATUS_ACTIVE),
        "state": state,
        "state_label": state.replace("-", " ").title(),
        "access_allowed": bool(access_state.get("access_allowed")),
        "expires_on": access_state.get("expires_on", ""),
        "days_remaining": days_remaining,
        "days_label": days_label,
        "renewal_due_on": access_state.get("renewal_due_on", ""),
        "renewal_days_remaining": renewal_days_remaining,
        "renewal_label": renewal_label,
        "tone": _platform_license_tone(state),
        "reason": access_state.get("reason", ""),
    }


def _platform_license_tone(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "active":
        return "green"
    if normalized == "trial":
        return "purple"
    if normalized == "expiring":
        return "orange"
    if normalized == "grace":
        return "cyan"
    if normalized == "suspended":
        return "orange"
    if normalized == "expired":
        return "red"
    return "neutral"


def _format_date_label(value: str) -> str:
    try:
        return date.fromisoformat(str(value or "").strip()).strftime("%d %b %Y")
    except ValueError:
        return "Not scheduled"


def _format_history_datetime(value: str) -> str:
    try:
        return datetime.fromisoformat(str(value or "").strip()).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return "Just now"


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


def _range_day_count(date_from: str, date_to: str) -> int:
    if not date_from or not date_to:
        return 1
    try:
        start_date = date.fromisoformat(date_from)
        end_date = date.fromisoformat(date_to)
    except ValueError:
        return 1
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    return max((end_date - start_date).days + 1, 1)


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


def _read_shift_form(form: Mapping[str, Any]) -> dict[str, Any]:
    values = {
        "name": str(form.get("name", "")).strip(),
        "code": str(form.get("code", "")).strip().upper(),
        "shift_start": str(form.get("shift_start", "")).strip(),
        "shift_end": str(form.get("shift_end", "")).strip(),
        "break_label": str(form.get("break_label", "Flexible")).strip() or "Flexible",
        "grace_minutes": int(str(form.get("grace_minutes", "15")).strip() or "15"),
        "weekly_off": str(form.get("weekly_off", "Configured per department")).strip() or "Configured per department",
        "description": str(form.get("description", "")).strip(),
        "is_active": str(form.get("is_active", "1")).strip() in {"1", "on", "true", "yes"},
    }
    if not values["name"]:
        raise ValueError("Shift name is required.")
    if not values["code"]:
        raise ValueError("Shift code is required.")
    if not values["shift_start"] or not values["shift_end"]:
        raise ValueError("Shift start and end times are required.")
    try:
        time.fromisoformat(values["shift_start"])
        time.fromisoformat(values["shift_end"])
    except ValueError as exc:
        raise ValueError("Shift start and end must be valid times.") from exc
    if values["shift_start"] == values["shift_end"]:
        raise ValueError("Shift start and end cannot be the same time.")
    if values["grace_minutes"] < 0:
        raise ValueError("Grace minutes cannot be negative.")
    return values


def _hospital_shift_management_model(
    department_scope: str = "",
    search: str = "",
    selected_shift_id: int | None = None,
) -> dict[str, Any]:
    shift_records = list_shifts(search=search)
    all_staff_rows = list_staff(active_only=True, department_scope=department_scope)
    assignments_by_shift: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in all_staff_rows:
        shift_id = row.get("shift_id")
        if shift_id:
            assignments_by_shift[int(shift_id)].append(row)

    selected_shift = get_shift(selected_shift_id) if selected_shift_id else None
    if not selected_shift and shift_records:
        selected_shift = shift_records[0]

    shift_rows: list[dict[str, Any]] = []
    assignment_rows: list[dict[str, Any]] = []
    overnight_count = 0
    assigned_total = 0
    open_shift_count = 0

    for shift in shift_records:
        shift_id = int(shift["id"])
        assigned_rows = assignments_by_shift.get(shift_id, [])
        assigned_total += len(assigned_rows)
        if not assigned_rows:
            open_shift_count += 1
        if shift_spans_overnight(str(shift["shift_start"]), str(shift["shift_end"])):
            overnight_count += 1

        departments = sorted(
            {
                str(row.get("department", "")).strip()
                for row in assigned_rows
                if row.get("department")
            }
        )
        badge_label, badge_tone = _shift_badge_from_window(
            shift.get("shift_start"),
            shift.get("shift_end"),
        )
        shift_rows.append(
            {
                "id": shift_id,
                "name": shift["name"],
                "code": str(shift["code"]).strip().upper(),
                "dot": badge_tone,
                "time": f"{_format_clock_label(str(shift['shift_start']))} - {_format_clock_label(str(shift['shift_end']))}",
                "break": shift.get("break_label") or "Flexible",
                "hours": _format_minutes_as_hours(
                    _shift_duration_minutes(str(shift["shift_start"]), str(shift["shift_end"]))
                ),
                "grace": f"{int(shift['grace_minutes'])} mins",
                "late_after": _format_clock_label(
                    _minutes_after_clock(str(shift["shift_start"]), int(shift["grace_minutes"]))
                ),
                "status": "Active" if int(shift["is_active"]) else "Inactive",
                "employees": len(assigned_rows),
                "departments": departments,
                "is_selected": bool(selected_shift and shift_id == int(selected_shift["id"])),
                "badge_label": badge_label,
            }
        )
        assignment_rows.append(
            {
                "id": shift_id,
                "name": shift["name"],
                "employees": len(assigned_rows),
                "departments": len(departments),
                "start": _format_clock_label(str(shift["shift_start"])),
                "end": _format_clock_label(str(shift["shift_end"])),
                "selected": bool(selected_shift and shift_id == int(selected_shift["id"])),
            }
        )

    selected_assignments: list[dict[str, Any]] = []
    available_staff_options: list[dict[str, Any]] = []
    detail = None
    form_values = {
        "name": "",
        "code": "",
        "shift_start": "08:00",
        "shift_end": "16:00",
        "break_label": "Flexible",
        "grace_minutes": 15,
        "weekly_off": "Configured per department",
        "description": "",
        "is_active": True,
    }

    if selected_shift:
        selected_shift_id = int(selected_shift["id"])
        selected_assignments = assignments_by_shift.get(selected_shift_id, [])
        selected_assignments.sort(
            key=lambda item: (str(item.get("department", "")), str(item.get("last_name", "")), str(item.get("first_name", "")))
        )
        form_values = {
            "name": selected_shift["name"],
            "code": str(selected_shift["code"]).strip().upper(),
            "shift_start": str(selected_shift["shift_start"]),
            "shift_end": str(selected_shift["shift_end"]),
            "break_label": selected_shift.get("break_label") or "Flexible",
            "grace_minutes": int(selected_shift["grace_minutes"]),
            "weekly_off": selected_shift.get("weekly_off") or "Configured per department",
            "description": selected_shift.get("description") or "",
            "is_active": bool(int(selected_shift["is_active"])),
        }
        detail = {
            "id": selected_shift_id,
            "name": selected_shift["name"],
            "code": str(selected_shift["code"]).strip().upper(),
            "time": f"{_format_clock_label(str(selected_shift['shift_start']))} - {_format_clock_label(str(selected_shift['shift_end']))}",
            "break": selected_shift.get("break_label") or "Flexible",
            "hours": _format_minutes_as_hours(
                _shift_duration_minutes(str(selected_shift["shift_start"]), str(selected_shift["shift_end"]))
            ),
            "grace": f"{int(selected_shift['grace_minutes'])} minutes",
            "late_after": _format_clock_label(
                _minutes_after_clock(str(selected_shift["shift_start"]), int(selected_shift["grace_minutes"]))
            ),
            "early_leave": _format_clock_label(
                _minutes_after_clock(str(selected_shift["shift_end"]), -15)
            ),
            "overtime_after": _format_clock_label(str(selected_shift["shift_end"])),
            "weekly_off": selected_shift.get("weekly_off") or "Configured per department",
            "status": "Active" if int(selected_shift["is_active"]) else "Inactive",
            "description": selected_shift.get("description") or "No description added yet.",
            "rules": [
                f"Late if clock-in is after {_format_clock_label(_minutes_after_clock(str(selected_shift['shift_start']), int(selected_shift['grace_minutes'])))}",
                "Half day if work is under 4h 00m",
                f"Overtime begins after {_format_clock_label(str(selected_shift['shift_end']))}",
                "Overnight shifts continue into the next calendar day automatically"
                if shift_spans_overnight(str(selected_shift["shift_start"]), str(selected_shift["shift_end"]))
                else "Day shifts close on the same calendar day",
            ],
        }

    available_staff_options = [
        {
            "id": int(row["id"]),
            "label": f"{row['staff_code']} - {row['first_name']} {row['last_name']} ({row['department']})",
        }
        for row in all_staff_rows
        if not selected_shift or int(row.get("shift_id") or 0) != int(selected_shift["id"])
    ]

    return {
        "stats": [
            {"icon": "shift", "tone": "blue", "label": "Total Shifts", "value": str(len(shift_rows)), "sub": "Configured schedules"},
            {"icon": "staff", "tone": "green", "label": "Assigned Staff", "value": str(assigned_total), "sub": "Active employees linked"},
            {"icon": "clock", "tone": "orange", "label": "Open Shifts", "value": str(open_shift_count), "sub": "No staff assigned"},
            {"icon": "overtime", "tone": "purple", "label": "Night Patterns", "value": str(overnight_count), "sub": "Overnight coverage"},
        ],
        "search": search,
        "selected_shift_id": int(selected_shift["id"]) if selected_shift else None,
        "shifts": shift_rows,
        "assignments": assignment_rows,
        "selected_assignments": selected_assignments,
        "available_staff_options": available_staff_options,
        "detail": detail,
        "form_values": form_values,
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
                "latest_event_type": str(latest["event_type"]),
                "latest_status_label": str(latest["status_label"]),
                "staff_code": base["staff_code"],
                "first_name": base["first_name"],
                "last_name": base["last_name"],
                "department": base["department"],
                "role": base["role"],
                "photo_url": _photo_url_for_filename(base.get("photo_filename")),
                "shift_start": base.get("shift_start") or "",
                "shift_end": base.get("shift_end") or "",
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


def _normalize_report_attendance_group(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in REPORT_ATTENDANCE_GROUPS:
        return "all"
    return normalized


def _report_attendance_group_label(attendance_group: str) -> str:
    return REPORT_ATTENDANCE_GROUPS.get(attendance_group, REPORT_ATTENDANCE_GROUPS["all"])


def _report_activity_matches_group(
    row: dict[str, Any],
    attendance_group: str,
) -> bool:
    if attendance_group == "all":
        return True
    if attendance_group == "present":
        return row.get("clock_in") != "-"
    if attendance_group == "late":
        return row.get("status_text") == "Late"
    if attendance_group == "checked_in":
        return row.get("latest_event_type") in {"check_in", "break_start", "break_end"}
    if attendance_group == "checked_out":
        return row.get("latest_event_type") == "check_out"
    return True


def _filter_report_activity_rows(
    activity_rows: list[dict[str, Any]],
    attendance_group: str,
) -> list[dict[str, Any]]:
    return [
        row for row in activity_rows
        if _report_activity_matches_group(row, attendance_group)
    ]


def _filter_report_detail_rows(
    detail_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
    attendance_group: str,
) -> list[dict[str, Any]]:
    if attendance_group == "all":
        return detail_rows
    matching_staff_ids = {int(row["staff_id"]) for row in activity_rows}
    return [
        row for row in detail_rows
        if int(row["staff_id"]) in matching_staff_ids
    ]


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
    staff_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    total_days_in_range = _range_day_count(date_from, date_to)
    staff_map: defaultdict[int, dict[str, Any]] = defaultdict(dict)
    for staff in staff_rows:
        staff_id = int(staff["id"])
        staff_map.setdefault(
            staff_id,
            {
                "staff_id": staff_id,
                "staff_code": staff["staff_code"],
                "first_name": staff["first_name"],
                "last_name": staff["last_name"],
                "department": staff["department"],
                "total_days": 0,
                "present": 0,
                "absent": total_days_in_range,
                "late": 0,
                "half_day": 0,
                "overtime": "0h 00m",
                "avg_hours": "0h 00m",
                "grade": "Good",
                "grade_tone": "green",
                "photo_url": staff.get("photo_url", ""),
                "_work_minutes": [],
            },
        )

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
        row["absent"] = max(total_days_in_range - int(row["present"]), 0)
        row["avg_hours"] = _average_work_label(work_minutes)
        if row["absent"] >= max(1, total_days_in_range // 2):
            row["grade"] = "Poor"
            row["grade_tone"] = "red"
        elif row["late"] >= 3:
            row["grade"] = "Poor"
            row["grade_tone"] = "red"
        elif row["late"] >= 1:
            row["grade"] = "Fair"
            row["grade_tone"] = "orange"
    detail_rows.sort(key=lambda item: (item["last_name"] if "last_name" in item else "", item["first_name"]))
    return detail_rows


def _daily_report_rows_from_activity(
    activity_rows: list[dict[str, Any]],
    active_staff_total: int,
) -> list[dict[str, Any]]:
    daily_map: dict[str, dict[str, Any]] = {}
    for row in activity_rows:
        attendance_date = str(row["attendance_date"])
        entry = daily_map.setdefault(
            attendance_date,
            {
                "attendance_date": attendance_date,
                "total_staff": active_staff_total,
                "present": 0,
                "checked_in": 0,
                "checked_out": 0,
                "late": 0,
                "absent": 0,
            },
        )
        entry["present"] += 1
        if _report_activity_matches_group(row, "checked_in"):
            entry["checked_in"] += 1
        if _report_activity_matches_group(row, "checked_out"):
            entry["checked_out"] += 1
        if _report_activity_matches_group(row, "late"):
            entry["late"] += 1

    daily_rows = list(daily_map.values())
    for row in daily_rows:
        row["absent"] = max(int(row["total_staff"]) - int(row["present"]), 0)
    daily_rows.sort(key=lambda item: item["attendance_date"], reverse=True)
    return daily_rows


def _department_report_rows_from_activity(
    activity_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    staff_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    department_staff_totals: defaultdict[str, int] = defaultdict(int)
    for staff in staff_rows:
        department_staff_totals[str(staff["department"])] += 1

    department_map: dict[str, dict[str, Any]] = {
        department_name: {
            "department": department_name,
            "total_staff": total_staff,
            "present": 0,
            "checked_in": 0,
            "checked_out": 0,
            "late": 0,
            "absent": 0,
        }
        for department_name, total_staff in department_staff_totals.items()
    }

    for row in activity_rows:
        department_name = str(row["department"])
        entry = department_map.setdefault(
            department_name,
            {
                "department": department_name,
                "total_staff": 0,
                "present": 0,
                "checked_in": 0,
                "checked_out": 0,
                "late": 0,
                "absent": 0,
            },
        )
        entry["present"] += 1
        if _report_activity_matches_group(row, "checked_in"):
            entry["checked_in"] += 1
        if _report_activity_matches_group(row, "checked_out"):
            entry["checked_out"] += 1
        if _report_activity_matches_group(row, "late"):
            entry["late"] += 1

    for row in detail_rows:
        department_name = str(row["department"])
        entry = department_map.setdefault(
            department_name,
            {
                "department": department_name,
                "total_staff": 0,
                "present": 0,
                "checked_in": 0,
                "checked_out": 0,
                "late": 0,
                "absent": 0,
            },
        )
        entry["absent"] += int(row.get("absent", 0))

    department_rows = list(department_map.values())
    department_rows.sort(
        key=lambda item: (
            -int(item["present"]),
            -int(item["late"]),
            str(item["department"]),
        )
    )
    return department_rows


def _report_group_metric(row: dict[str, Any], attendance_group: str) -> int:
    if attendance_group == "present":
        return int(row.get("present", 0))
    if attendance_group == "late":
        return int(row.get("late", 0))
    if attendance_group == "checked_in":
        return int(row.get("checked_in", 0))
    if attendance_group == "checked_out":
        return int(row.get("checked_out", 0))
    return 1


def _report_views_model(
    activity_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    staff_rows: list[dict[str, Any]],
    report_kind: str,
    attendance_group: str,
    active_staff_total: int,
) -> dict[str, Any]:
    if report_kind not in {"staff", "daily", "department", "exceptions"}:
        report_kind = "staff"

    filtered_activity_rows = _filter_report_activity_rows(activity_rows, attendance_group)
    filtered_staff_rows = _filter_report_detail_rows(
        detail_rows,
        filtered_activity_rows,
        attendance_group,
    )
    daily_rows = _daily_report_rows_from_activity(activity_rows, active_staff_total)
    department_rows = _department_report_rows_from_activity(
        activity_rows,
        detail_rows,
        staff_rows,
    )
    if attendance_group != "all":
        daily_rows = [
            row for row in daily_rows
            if _report_group_metric(row, attendance_group) > 0
        ]
        department_rows = [
            row for row in department_rows
            if _report_group_metric(row, attendance_group) > 0
        ]
    exception_rows = [
        row for row in filtered_staff_rows
        if int(row.get("late", 0)) > 0
        or int(row.get("absent", 0)) > 0
        or int(row.get("half_day", 0)) > 0
        or row.get("grade_tone") in {"orange", "red"}
    ]
    exception_rows.sort(
        key=lambda item: (
            -int(item.get("absent", 0)),
            -int(item.get("late", 0)),
            str(item.get("last_name", "")),
            str(item.get("first_name", "")),
        )
    )

    tabs = [
        {"key": "staff", "label": "Staff Summary", "count": len(filtered_staff_rows)},
        {"key": "daily", "label": "Daily Summary", "count": len(daily_rows)},
        {"key": "department", "label": "Department Summary", "count": len(department_rows)},
        {"key": "exceptions", "label": "Exceptions", "count": len(exception_rows)},
    ]
    group_label = _report_attendance_group_label(attendance_group)
    group_suffix = "" if attendance_group == "all" else f" Showing {group_label.lower()} records only."

    config_map: dict[str, dict[str, Any]] = {
        "staff": {
            "title": "Attendance Details",
            "subtitle": f"Per-staff summary across the selected report period.{group_suffix}",
            "empty_title": f"No {group_label.lower()} staff summary data yet." if attendance_group != "all" else "No staff summary data yet.",
            "empty_text": "Run attendance activity in the selected date range to populate staff performance summaries.",
            "row_count": len(filtered_staff_rows),
        },
        "daily": {
            "title": "Daily Attendance Summary",
            "subtitle": f"Daily totals grouped into present, checked in, checked out, late, and absent counts.{group_suffix}",
            "empty_title": f"No daily {group_label.lower()} attendance summary yet." if attendance_group != "all" else "No daily attendance summary yet.",
            "empty_text": "There are no recorded attendance events inside the selected report period.",
            "row_count": len(daily_rows),
        },
        "department": {
            "title": "Department Performance",
            "subtitle": f"Department-level totals for present, checked in, checked out, late, and absent staff.{group_suffix}",
            "empty_title": f"No department {group_label.lower()} summary yet." if attendance_group != "all" else "No department summary yet.",
            "empty_text": "No attendance events were recorded for the filtered departments in this date range.",
            "row_count": len(department_rows),
        },
        "exceptions": {
            "title": "Attendance Exceptions",
            "subtitle": f"Staff needing attention due to lateness, absence, or half-day patterns.{group_suffix}",
            "empty_title": "No attendance exceptions in this period.",
            "empty_text": "Everyone in the selected report set is currently within normal attendance patterns.",
            "row_count": len(exception_rows),
        },
    }

    quick_reports = [
        {"key": tab["key"], "label": tab["label"], "is_active": tab["key"] == report_kind}
        for tab in tabs
    ]

    return {
        "active_kind": report_kind,
        "attendance_group": {
            "key": attendance_group,
            "label": group_label,
        },
        "group_options": [
            {
                "key": key,
                "label": label,
                "is_active": key == attendance_group,
            }
            for key, label in REPORT_ATTENDANCE_GROUPS.items()
        ],
        "tabs": tabs,
        "quick_reports": quick_reports,
        "staff_rows": filtered_staff_rows,
        "daily_rows": daily_rows,
        "department_rows": department_rows,
        "exception_rows": exception_rows,
        "current": config_map[report_kind],
    }


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


def _normalize_payroll_filter_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in PAYROLL_FILTER_STATUSES:
        return "all"
    return normalized


def _payroll_month_label(payroll_month: str) -> str:
    normalized = normalize_payroll_month(payroll_month)
    return date.fromisoformat(f"{normalized}-01").strftime("%B %Y")


def _weekday_key(value: date) -> str:
    return WORKDAY_OPTIONS[value.weekday()]


def _working_day_count(date_from: str, date_to: str, working_days: list[str]) -> int:
    try:
        start_date = date.fromisoformat(date_from)
        end_date = date.fromisoformat(date_to)
    except ValueError:
        return 0
    if end_date < start_date:
        return 0
    allowed_days = set(working_days or WORKDAY_OPTIONS[:5])
    count = 0
    current = start_date
    while current <= end_date:
        if _weekday_key(current) in allowed_days:
            count += 1
        current += timedelta(days=1)
    return count


def _scheduled_shift_minutes(shift_start: str, shift_end: str) -> int:
    if not shift_start or not shift_end:
        return 0
    try:
        start_dt, end_dt = shift_bounds_for_date(
            date(2000, 1, 1),
            shift_start,
            shift_end,
        )
    except ValueError:
        return 0
    return max(int((end_dt - start_dt).total_seconds() // 60), 0)


def _format_currency(amount: float) -> str:
    return f"GH₵{amount:,.2f}"


def _format_payroll_day_value(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _payroll_status_tone(status: str) -> str:
    normalized = status.strip().title()
    if normalized == PAYROLL_STATUS_PROCESSED:
        return "green"
    if normalized == PAYROLL_STATUS_HOLD:
        return "red"
    return "orange"


def _build_payroll_rows(
    *,
    staff_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
    payroll_month: str,
    working_days: list[str],
) -> list[dict[str, Any]]:
    normalized_month = normalize_payroll_month(payroll_month)
    date_from, date_to = payroll_month_bounds(normalized_month)
    scheduled_work_days = _working_day_count(date_from, date_to, working_days)
    status_map = get_payroll_status_map(normalized_month)
    activity_map: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        activity_map[int(row["staff_id"])].append(row)

    payroll_rows: list[dict[str, Any]] = []
    for staff in staff_rows:
        staff_id = int(staff["id"])
        staff_activity = activity_map.get(staff_id, [])
        present_days = sum(1 for row in staff_activity if row.get("clock_in") != "-")
        late_days = sum(1 for row in staff_activity if row.get("status_text") == "Late")
        half_days = sum(1 for row in staff_activity if row.get("status_text") == "Half Day")
        checked_out_days = sum(1 for row in staff_activity if row.get("latest_event_type") == "check_out")
        overtime_minutes = 0
        for row in staff_activity:
            worked_minutes = int(row.get("work_minutes") or 0)
            scheduled_minutes = _scheduled_shift_minutes(
                str(row.get("shift_start") or ""),
                str(row.get("shift_end") or ""),
            )
            if worked_minutes > 0 and scheduled_minutes > 0:
                overtime_minutes += max(worked_minutes - scheduled_minutes, 0)

        payable_days = max(float(present_days) - (float(half_days) * 0.5), 0.0)
        absent_days = max(scheduled_work_days - payable_days, 0.0)
        base_salary = float(staff.get("base_salary") or 0)
        overtime_rate = float(staff.get("overtime_hourly_rate") or 0)
        tax_deduction = float(staff.get("tax_deduction") or 0)
        provident_fund = float(staff.get("provident_fund") or 0)
        health_insurance = float(staff.get("health_insurance") or 0)
        other_deduction = float(staff.get("other_deduction") or 0)

        if scheduled_work_days > 0:
            base_earned = round(base_salary * min(payable_days, float(scheduled_work_days)) / float(scheduled_work_days), 2)
        else:
            base_earned = round(base_salary, 2)
        overtime_hours = round(overtime_minutes / 60, 2)
        overtime_pay = round(overtime_hours * overtime_rate, 2)
        gross_pay = round(base_earned + overtime_pay, 2)
        total_deductions = round(
            tax_deduction + provident_fund + health_insurance + other_deduction,
            2,
        )
        net_pay = round(max(gross_pay - total_deductions, 0), 2)

        status_data = status_map.get(staff_id, {})
        status = str(status_data.get("status") or PAYROLL_STATUS_PENDING)
        payroll_rows.append(
            {
                "staff_id": staff_id,
                "staff_code": staff["staff_code"],
                "first_name": staff["first_name"],
                "last_name": staff["last_name"],
                "department": staff["department"],
                "role": staff["role"],
                "photo_url": staff.get("photo_url") or _photo_url_for_filename(staff.get("photo_filename")),
                "payroll_month_label": _payroll_month_label(normalized_month),
                "scheduled_work_days": scheduled_work_days,
                "present_days": present_days,
                "late_days": late_days,
                "half_days": half_days,
                "checked_out_days": checked_out_days,
                "payable_days": payable_days,
                "absent_days": absent_days,
                "work_days": _format_payroll_day_value(payable_days),
                "days_summary": f"{_format_payroll_day_value(payable_days)} / {scheduled_work_days or 0} days",
                "base_salary_amount": base_salary,
                "base_earned_amount": base_earned,
                "overtime_hourly_rate": overtime_rate,
                "overtime_minutes": overtime_minutes,
                "overtime_hours": overtime_hours,
                "overtime_hours_label": _format_work_duration(overtime_minutes) if overtime_minutes > 0 else "0h 00m",
                "overtime_pay_amount": overtime_pay,
                "tax_deduction_amount": tax_deduction,
                "provident_fund_amount": provident_fund,
                "health_insurance_amount": health_insurance,
                "other_deduction_amount": other_deduction,
                "gross_pay_amount": gross_pay,
                "total_deductions_amount": total_deductions,
                "net_pay_amount": net_pay,
                "gross_pay": _format_currency(gross_pay),
                "deductions": _format_currency(total_deductions),
                "net_pay": _format_currency(net_pay),
                "payment_method": staff.get("payment_method") or "Bank Transfer",
                "bank_name": staff.get("bank_name") or "",
                "account_name": staff.get("account_name") or "",
                "account_number": staff.get("account_number") or "",
                "status": status,
                "status_tone": _payroll_status_tone(status),
                "notes": str(status_data.get("notes") or ""),
                "processed_at": str(status_data.get("processed_at") or ""),
            }
        )

    payroll_rows.sort(
        key=lambda item: (
            str(item["department"]),
            str(item["last_name"]),
            str(item["first_name"]),
        )
    )
    return payroll_rows


def _filter_payroll_rows(
    payroll_rows: list[dict[str, Any]],
    status_filter: str,
) -> list[dict[str, Any]]:
    if status_filter == "all":
        return payroll_rows
    expected_status = PAYROLL_FILTER_STATUSES[status_filter]
    return [
        row for row in payroll_rows
        if row.get("status") == expected_status
    ]


def _payroll_live_model(
    *,
    payroll_rows: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    payroll_month: str,
) -> dict[str, Any]:
    gross_total = round(sum(float(row["gross_pay_amount"]) for row in all_rows), 2)
    deductions_total = round(sum(float(row["total_deductions_amount"]) for row in all_rows), 2)
    net_total = round(sum(float(row["net_pay_amount"]) for row in all_rows), 2)
    processed_count = sum(1 for row in all_rows if row["status"] == PAYROLL_STATUS_PROCESSED)
    pending_count = sum(1 for row in all_rows if row["status"] == PAYROLL_STATUS_PENDING)
    hold_count = sum(1 for row in all_rows if row["status"] == PAYROLL_STATUS_HOLD)
    tax_total = round(sum(float(row["tax_deduction_amount"]) for row in all_rows), 2)
    provident_total = round(sum(float(row["provident_fund_amount"]) for row in all_rows), 2)
    health_total = round(sum(float(row["health_insurance_amount"]) for row in all_rows), 2)
    other_total = round(sum(float(row["other_deduction_amount"]) for row in all_rows), 2)

    tabs = [
        {"key": "all", "label": "Payroll Employees", "count": len(all_rows)},
        {"key": "processed", "label": "Processed", "count": processed_count},
        {"key": "pending", "label": "Pending", "count": pending_count},
        {"key": "hold", "label": "Hold", "count": hold_count},
    ]

    return {
        "payroll_month_label": _payroll_month_label(payroll_month),
        "stats": [
            {"icon": "payroll", "tone": "blue", "label": "Total Employees", "value": str(len(all_rows)), "sub": "Payroll-ready staff"},
            {"icon": "payroll", "tone": "green", "label": "Gross Pay", "value": _format_currency(gross_total), "sub": _payroll_month_label(payroll_month)},
            {"icon": "payroll", "tone": "orange", "label": "Deductions", "value": _format_currency(deductions_total), "sub": _payroll_month_label(payroll_month)},
            {"icon": "payroll", "tone": "purple", "label": "Net Pay", "value": _format_currency(net_total), "sub": _payroll_month_label(payroll_month)},
            {"icon": "staff", "tone": "cyan", "label": "Processed", "value": str(processed_count), "sub": _percent_text(processed_count, len(all_rows))},
            {"icon": "clock", "tone": "red", "label": "Pending", "value": str(pending_count), "sub": _percent_text(pending_count, len(all_rows))},
        ],
        "summary": [
            {"label": "Selected Rows", "value": str(len(payroll_rows))},
            {"label": "Payroll Employees", "value": str(len(all_rows))},
            {"label": "Total Gross Pay", "value": _format_currency(gross_total)},
            {"label": "Total Deductions", "value": _format_currency(deductions_total)},
            {"label": "Total Net Pay", "value": _format_currency(net_total)},
            {"label": "Processed Employees", "value": f"{processed_count} ({_percent_text(processed_count, len(all_rows))})"},
            {"label": "Pending Employees", "value": f"{pending_count} ({_percent_text(pending_count, len(all_rows))})"},
            {"label": "Hold Employees", "value": f"{hold_count} ({_percent_text(hold_count, len(all_rows))})"},
        ],
        "deductions": [
            {"label": "Tax", "value": _format_currency(tax_total)},
            {"label": "Provident Fund", "value": _format_currency(provident_total)},
            {"label": "Health Insurance", "value": _format_currency(health_total)},
            {"label": "Other Deductions", "value": _format_currency(other_total)},
            {"label": "Total Deductions", "value": _format_currency(deductions_total)},
        ],
        "tabs": tabs,
    }


def _payroll_rows_to_csv(payroll_rows: list[dict[str, Any]]) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Payroll Month",
            "Staff Code",
            "Name",
            "Department",
            "Role",
            "Work Days",
            "Scheduled Work Days",
            "Late Days",
            "Half Days",
            "Checked Out Days",
            "Base Earned",
            "Overtime Hours",
            "Overtime Pay",
            "Gross Pay",
            "Tax",
            "Provident Fund",
            "Health Insurance",
            "Other Deductions",
            "Total Deductions",
            "Net Pay",
            "Payment Method",
            "Status",
        ]
    )
    for row in payroll_rows:
        writer.writerow(
            [
                row.get("payroll_month_label", ""),
                row["staff_code"],
                f"{row['first_name']} {row['last_name']}",
                row["department"],
                row["role"],
                row["work_days"],
                row["scheduled_work_days"],
                row["late_days"],
                row["half_days"],
                row["checked_out_days"],
                row["base_earned_amount"],
                row["overtime_hours_label"],
                row["overtime_pay_amount"],
                row["gross_pay_amount"],
                row["tax_deduction_amount"],
                row["provident_fund_amount"],
                row["health_insurance_amount"],
                row["other_deduction_amount"],
                row["total_deductions_amount"],
                row["net_pay_amount"],
                row["payment_method"],
                row["status"],
            ]
        )
    return output.getvalue()


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
            {"icon": "payroll", "tone": "green", "label": "Total Overtime Pay", "value": _format_currency(0), "sub": "This Month"},
            {"icon": "staff", "tone": "blue", "label": "Employees with OT", "value": "0", "sub": "This Month"},
            {"icon": "document", "tone": "orange", "label": "Approved Hours", "value": "0h 00m", "sub": "0.00%"},
            {"icon": "clock", "tone": "red", "label": "Pending Hours", "value": "0h 00m", "sub": "0.00%"},
        ],
        "top_employees": [],
        "summary": [
            {"label": "Total Overtime Hours", "value": "0h 00m"},
            {"label": "Total Overtime Pay", "value": _format_currency(0)},
            {"label": "Average OT Hours / Employee", "value": "0h 00m"},
        ],
    }


def _payroll_empty_model() -> dict[str, Any]:
    return {
        "stats": [
            {"icon": "payroll", "tone": "blue", "label": "Total Employees", "value": "0", "sub": "All Employees"},
            {"icon": "payroll", "tone": "green", "label": "Gross Pay", "value": _format_currency(0), "sub": "This Month"},
            {"icon": "payroll", "tone": "orange", "label": "Deductions", "value": _format_currency(0), "sub": "This Month"},
            {"icon": "payroll", "tone": "purple", "label": "Net Pay", "value": _format_currency(0), "sub": "This Month"},
            {"icon": "staff", "tone": "cyan", "label": "Processed", "value": "0", "sub": "0.00%"},
            {"icon": "clock", "tone": "red", "label": "Pending", "value": "0", "sub": "0.00%"},
        ],
        "summary": [
            {"label": "Total Employees", "value": "0"},
            {"label": "Total Gross Pay", "value": _format_currency(0)},
            {"label": "Total Deductions", "value": _format_currency(0)},
            {"label": "Total Net Pay", "value": _format_currency(0)},
            {"label": "Processed Employees", "value": "0 (0.00%)"},
            {"label": "Pending Employees", "value": "0 (0.00%)"},
            {"label": "Hold Employees", "value": "0 (0.00%)"},
        ],
        "deductions": [
            {"label": "Tax", "value": _format_currency(0)},
            {"label": "Provident Fund", "value": _format_currency(0)},
            {"label": "Health Insurance", "value": _format_currency(0)},
            {"label": "Other Deductions", "value": _format_currency(0)},
            {"label": "Total Deductions", "value": _format_currency(0)},
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
            {"icon": "payroll", "tone": "green", "label": "Total Overtime Pay", "value": _format_currency(4673.50), "sub": "This Month"},
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
            {"icon": "payroll", "tone": "green", "label": "Gross Pay", "value": _format_currency(98764.50), "sub": "This Month"},
            {"icon": "payroll", "tone": "orange", "label": "Deductions", "value": _format_currency(18245.75), "sub": "This Month"},
            {"icon": "payroll", "tone": "purple", "label": "Net Pay", "value": _format_currency(80518.75), "sub": "This Month"},
            {"icon": "staff", "tone": "cyan", "label": "Processed", "value": "206", "sub": "80.47%"},
            {"icon": "clock", "tone": "red", "label": "Pending", "value": "50", "sub": "19.53%"},
        ],
        "summary": [
            {"label": "Total Employees", "value": "256", "tone": "blue"},
            {"label": "Total Gross Pay", "value": _format_currency(98764.50), "tone": "green"},
            {"label": "Total Deductions", "value": _format_currency(18245.75), "tone": "orange"},
            {"label": "Total Net Pay", "value": _format_currency(80518.75), "tone": "purple"},
            {"label": "Processed Employees", "value": "206 (80.47%)", "tone": "cyan"},
            {"label": "Pending Employees", "value": "50 (19.53%)", "tone": "orange"},
            {"label": "Hold Employees", "value": "2 (0.78%)", "tone": "red"},
        ],
        "deductions": [
            {"label": "Tax", "value": _format_currency(7856.40)},
            {"label": "Provident Fund", "value": _format_currency(5963.20)},
            {"label": "Health Insurance", "value": _format_currency(2145.50)},
            {"label": "Other Deductions", "value": _format_currency(2280.65)},
            {"label": "Total Deductions", "value": _format_currency(18245.75)},
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
    return format_notification_rows(list_notifications(limit=limit, audience="admin"))


def _cached_admin_notification_rows(limit: int = 12) -> list[dict[str, str]]:
    return _build_admin_notification_rows(limit=limit)


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
        {"staff_code": "STF001", "first_name": "John", "last_name": "Doe", "department": "IT Department", "work_days": "23", "gross_pay": "GH₵4,850.00", "deductions": "GH₵842.50", "net_pay": "GH₵4,007.50", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF002", "first_name": "Jane", "last_name": "Smith", "department": "HR Department", "work_days": "22", "gross_pay": "GH₵4,120.00", "deductions": "GH₵735.25", "net_pay": "GH₵3,384.75", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF003", "first_name": "Michael", "last_name": "Brown", "department": "Finance Department", "work_days": "23", "gross_pay": "GH₵5,200.00", "deductions": "GH₵965.00", "net_pay": "GH₵4,235.00", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF004", "first_name": "Emily", "last_name": "Davis", "department": "Marketing Department", "work_days": "22", "gross_pay": "GH₵3,750.00", "deductions": "GH₵620.30", "net_pay": "GH₵3,129.70", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF005", "first_name": "David", "last_name": "Wilson", "department": "Operations Department", "work_days": "23", "gross_pay": "GH₵4,600.00", "deductions": "GH₵810.40", "net_pay": "GH₵3,789.60", "payment_method": "Bank Transfer", "status": "Processed", "status_tone": "green", "action_label": "View Payslip", "photo_url": ""},
        {"staff_code": "STF006", "first_name": "Sarah", "last_name": "Johnson", "department": "IT Department", "work_days": "21", "gross_pay": "GH₵4,250.00", "deductions": "GH₵745.60", "net_pay": "GH₵3,504.40", "payment_method": "Bank Transfer", "status": "Pending", "status_tone": "orange", "action_label": "Process", "photo_url": ""},
        {"staff_code": "STF007", "first_name": "Robert", "last_name": "Lee", "department": "Finance Department", "work_days": "23", "gross_pay": "GH₵5,800.00", "deductions": "GH₵1,020.80", "net_pay": "GH₵4,779.20", "payment_method": "Bank Transfer", "status": "Pending", "status_tone": "orange", "action_label": "Process", "photo_url": ""},
        {"staff_code": "STF008", "first_name": "Linda", "last_name": "Martinez", "department": "HR Department", "work_days": "22", "gross_pay": "GH₵3,950.00", "deductions": "GH₵690.15", "net_pay": "GH₵3,259.85", "payment_method": "Bank Transfer", "status": "Pending", "status_tone": "orange", "action_label": "Process", "photo_url": ""},
        {"staff_code": "STF009", "first_name": "James", "last_name": "Taylor", "department": "Operations Department", "work_days": "23", "gross_pay": "GH₵4,400.00", "deductions": "GH₵780.50", "net_pay": "GH₵3,619.50", "payment_method": "Cash", "status": "Hold", "status_tone": "red", "action_label": "Review", "photo_url": ""},
        {"staff_code": "STF010", "first_name": "Jessica", "last_name": "Anderson", "department": "Marketing Department", "work_days": "22", "gross_pay": "GH₵3,600.00", "deductions": "GH₵640.25", "net_pay": "GH₵2,959.75", "payment_method": "Bank Transfer", "status": "Hold", "status_tone": "red", "action_label": "Review", "photo_url": ""},
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
