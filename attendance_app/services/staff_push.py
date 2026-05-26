from __future__ import annotations

from base64 import urlsafe_b64encode
from datetime import datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Mapping
import json
import sqlite3

from flask import current_app, has_request_context

from attendance_app.config import AppConfig
from attendance_app.db import get_db, init_db
from attendance_app.services.attendance import resolve_shift_attendance_date
from attendance_app.services.notifications import create_notification_for_database
from attendance_app.services.settings import get_app_settings_for_database
from attendance_app.services.tenancy import list_organizations

_RUNNER_LOCK = Lock()
_RUNNER_THREAD: Thread | None = None
_RUNNER_STOP = Event()


def push_dependencies_available() -> bool:
    try:
        _load_push_dependencies()
    except ModuleNotFoundError:
        return False
    return True


def get_push_client_config(
    settings: AppConfig,
    *,
    organization_slug: str,
    login_url: str,
    shift_alarm: Mapping[str, Any],
) -> dict[str, Any]:
    if not push_dependencies_available():
        return {
            "enabled": False,
            "mode": "fallback",
            "reason": "Web Push dependencies are not installed on this server yet.",
        }

    if not shift_alarm.get("supported"):
        return {
            "enabled": False,
            "mode": "fallback",
            "reason": "This staff account does not have a valid shift time yet.",
        }

    vapid = _resolve_vapid_keypair(settings)
    return {
        "enabled": True,
        "mode": "server_push",
        "public_key": vapid["public_key"],
        "organization_slug": organization_slug,
        "login_url": login_url,
        "notification_title": shift_alarm.get("notification_title") or "Shift reminder",
        "notification_body": shift_alarm.get("notification_body") or "Your shift starts in 10 minutes.",
        "notification_tag": shift_alarm.get("storage_key") or f"shift-reminder:{organization_slug}",
        "next_shift_start_iso": shift_alarm.get("next_shift_start_iso") or "",
        "next_reminder_iso": shift_alarm.get("next_reminder_iso") or "",
    }


def save_staff_push_subscription(
    staff_id: int,
    subscription_payload: Mapping[str, Any],
    *,
    user_agent: str = "",
    platform: str = "",
    device_label: str = "",
) -> dict[str, Any]:
    endpoint, key_p256dh, key_auth = _subscription_parts(subscription_payload)
    endpoint_hash = sha256(endpoint.encode("utf-8")).hexdigest()
    payload_json = json.dumps(subscription_payload, separators=(",", ":"), sort_keys=True)
    timestamp = datetime.now().isoformat(timespec="seconds")

    db = get_db()
    db.execute(
        """
        INSERT INTO staff_push_subscriptions (
            staff_id,
            endpoint,
            endpoint_hash,
            subscription_json,
            p256dh_key,
            auth_key,
            device_label,
            platform,
            user_agent,
            is_active,
            notifications_enabled,
            created_at,
            updated_at,
            last_seen_at,
            last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, '')
        ON CONFLICT(endpoint_hash) DO UPDATE SET
            staff_id = excluded.staff_id,
            endpoint = excluded.endpoint,
            subscription_json = excluded.subscription_json,
            p256dh_key = excluded.p256dh_key,
            auth_key = excluded.auth_key,
            device_label = excluded.device_label,
            platform = excluded.platform,
            user_agent = excluded.user_agent,
            is_active = 1,
            notifications_enabled = 1,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at,
            last_error = ''
        """,
        (
            int(staff_id),
            endpoint,
            endpoint_hash,
            payload_json,
            key_p256dh,
            key_auth,
            str(device_label or "").strip(),
            str(platform or "").strip(),
            str(user_agent or "").strip(),
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    db.commit()
    row = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM staff_push_subscriptions
        WHERE staff_id = ? AND is_active = 1 AND notifications_enabled = 1
        """,
        (int(staff_id),),
    ).fetchone()
    return {
        "saved": True,
        "endpoint_hash": endpoint_hash,
        "active_devices": int(row["count"]) if row else 0,
    }


def deactivate_staff_push_subscription(
    *,
    staff_id: int,
    endpoint: str = "",
    endpoint_hash: str = "",
) -> int:
    normalized_hash = endpoint_hash.strip() or sha256(endpoint.encode("utf-8")).hexdigest()
    db = get_db()
    cursor = db.execute(
        """
        UPDATE staff_push_subscriptions
        SET is_active = 0,
            notifications_enabled = 0,
            updated_at = ?
        WHERE staff_id = ? AND endpoint_hash = ?
        """,
        (datetime.now().isoformat(timespec="seconds"), int(staff_id), normalized_hash),
    )
    db.commit()
    return int(cursor.rowcount)


def dispatch_due_shift_alerts(
    settings: AppConfig,
    *,
    now_dt: datetime | None = None,
) -> dict[str, Any]:
    if not push_dependencies_available():
        return {
            "enabled": False,
            "organizations": 0,
            "subscriptions": 0,
            "notifications_sent": 0,
            "skipped": 0,
        }

    vapid = _resolve_vapid_keypair(settings)
    current_dt = now_dt or datetime.now()
    summary = {
        "enabled": True,
        "organizations": 0,
        "subscriptions": 0,
        "notifications_sent": 0,
        "skipped": 0,
        "deactivated": 0,
        "errors": 0,
    }

    for organization in list_organizations(settings):
        summary["organizations"] += 1
        organization_result = _dispatch_due_shift_alerts_for_organization(
            settings,
            organization_slug=organization.slug,
            database_path=organization.database_path,
            display_name=organization.display_name,
            vapid=vapid,
            current_dt=current_dt,
        )
        for key in ("subscriptions", "notifications_sent", "skipped", "deactivated", "errors"):
            summary[key] += int(organization_result.get(key, 0))

    return summary


def start_shift_alert_runner(app) -> None:
    settings: AppConfig = app.config["APP_SETTINGS"]
    if not settings.shift_alert_runner_enabled:
        return

    global _RUNNER_THREAD
    with _RUNNER_LOCK:
        if _RUNNER_THREAD and _RUNNER_THREAD.is_alive():
            return

        _RUNNER_STOP.clear()
        _RUNNER_THREAD = Thread(
            target=_shift_alert_runner_loop,
            name="attendance-shift-alert-runner",
            args=(app,),
            daemon=True,
        )
        _RUNNER_THREAD.start()


def _shift_alert_runner_loop(app) -> None:
    interval_seconds = max(30, int(app.config["APP_SETTINGS"].shift_alert_poll_seconds))
    while not _RUNNER_STOP.is_set():
        try:
            with app.app_context():
                dispatch_due_shift_alerts(app.config["APP_SETTINGS"])
        except Exception:
            app.logger.exception("Shift alert runner failed.")
        if _RUNNER_STOP.wait(interval_seconds):
            break


def build_shift_alarm_plan(
    *,
    staff: Mapping[str, Any],
    check_in_at: datetime | None,
    check_out_at: datetime | None,
    current_dt: datetime,
) -> dict[str, Any]:
    shift_start_value = str(staff.get("shift_start") or "09:00")
    shift_end_value = str(staff.get("shift_end") or "17:00")
    shift_start_clock = _parse_clock_value(shift_start_value)
    if not shift_start_clock:
        return {
            "supported": False,
            "staff_name": _staff_display_name(staff),
            "reminder_key": f"shift-reminder:{staff.get('id', 'staff')}:invalid",
        }

    today_shift_start = datetime.combine(current_dt.date(), shift_start_clock)
    is_checked_in = bool(check_in_at) and not bool(check_out_at)
    if is_checked_in or current_dt > today_shift_start:
        next_shift_start = today_shift_start + timedelta(days=1)
    else:
        next_shift_start = today_shift_start

    reminder_dt = next_shift_start - timedelta(minutes=10)
    return {
        "supported": True,
        "enabled_by_policy": bool(staff.get("allow_mobile_clock", 1)),
        "staff_name": _staff_display_name(staff),
        "shift_start_value": shift_start_value,
        "shift_end_value": shift_end_value,
        "shift_window": f"{_format_clock_label(shift_start_value)} - {_format_clock_label(shift_end_value)}",
        "shift_start_label": _format_clock_label(shift_start_value),
        "shift_end_label": _format_clock_label(shift_end_value),
        "next_shift_start": next_shift_start,
        "next_reminder": reminder_dt,
        "next_shift_start_iso": next_shift_start.isoformat(),
        "next_reminder_iso": reminder_dt.isoformat(),
        "reminder_key": f"shift-reminder:{staff.get('id', 'staff')}:{next_shift_start.isoformat(timespec='minutes')}",
    }


def _dispatch_due_shift_alerts_for_organization(
    settings: AppConfig,
    *,
    organization_slug: str,
    database_path: Path,
    display_name: str,
    vapid: Mapping[str, str],
    current_dt: datetime,
) -> dict[str, int]:
    init_db(database_path)
    db = sqlite3.connect(Path(database_path).resolve(), timeout=30)
    db.row_factory = sqlite3.Row
    _configure_background_connection(db)
    try:
        live_settings = get_app_settings_for_database(
            database_path,
            default_app_name=display_name or settings.app_name,
        )
        location_name = (
            live_settings.get("allowed_location_name")
            or live_settings.get("organization_name")
            or display_name
            or settings.app_name
        )
        staff_rows = db.execute(
            """
            SELECT id, staff_code, first_name, last_name, shift_start, shift_end, allow_mobile_clock
            FROM staff
            WHERE is_active = 1 AND allow_mobile_clock = 1
            ORDER BY first_name, last_name, staff_code
            """
        ).fetchall()

        result = {
            "subscriptions": 0,
            "notifications_sent": 0,
            "skipped": 0,
            "deactivated": 0,
            "errors": 0,
        }

        for raw_staff_row in staff_rows:
            staff_row = dict(raw_staff_row)
            status = _staff_status_for_database(db, staff_row, current_dt)
            alarm = build_shift_alarm_plan(
                staff=staff_row,
                check_in_at=status.get("check_in_at"),
                check_out_at=status.get("check_out_at"),
                current_dt=current_dt,
            )
            if not alarm.get("supported") or not alarm.get("enabled_by_policy"):
                result["skipped"] += 1
                continue

            reminder_dt = alarm.get("next_reminder")
            shift_start_dt = alarm.get("next_shift_start")
            if not isinstance(reminder_dt, datetime) or not isinstance(shift_start_dt, datetime):
                result["skipped"] += 1
                continue
            if current_dt < reminder_dt or current_dt >= shift_start_dt:
                result["skipped"] += 1
                continue
            if status.get("currently_inside"):
                result["skipped"] += 1
                continue

            subscriptions = db.execute(
                """
                SELECT *
                FROM staff_push_subscriptions
                WHERE staff_id = ? AND is_active = 1 AND notifications_enabled = 1
                ORDER BY updated_at DESC, id DESC
                """,
                (int(staff_row["id"]),),
            ).fetchall()
            result["subscriptions"] += len(subscriptions)
            if not subscriptions:
                continue

            for subscription_row in subscriptions:
                if _shift_alert_already_sent(db, int(subscription_row["id"]), str(alarm["reminder_key"])):
                    continue
                payload = _shift_notification_payload(
                    staff_row=staff_row,
                    organization_slug=organization_slug,
                    location_name=str(location_name),
                    alarm=alarm,
                )
                try:
                    _send_web_push_message(
                        subscription=_subscription_info_from_row(subscription_row),
                        payload=payload,
                        vapid=vapid,
                        settings=settings,
                    )
                    _record_shift_alert_delivery(
                        db,
                        staff_id=int(staff_row["id"]),
                        subscription_id=int(subscription_row["id"]),
                        reminder_key=str(alarm["reminder_key"]),
                        shift_start_at=shift_start_dt.isoformat(timespec="seconds"),
                        scheduled_for=reminder_dt.isoformat(timespec="seconds"),
                        status="sent",
                        detail="Notification delivered.",
                    )
                    db.execute(
                        """
                        UPDATE staff_push_subscriptions
                        SET last_push_sent_at = ?, updated_at = ?, last_error = ''
                        WHERE id = ?
                        """,
                        (
                            current_dt.isoformat(timespec="seconds"),
                            current_dt.isoformat(timespec="seconds"),
                            int(subscription_row["id"]),
                        ),
                    )
                    db.commit()
                    result["notifications_sent"] += 1
                except Exception as error:
                    error_text = str(error)
                    is_gone = "404" in error_text or "410" in error_text
                    if is_gone:
                        db.execute(
                            """
                            UPDATE staff_push_subscriptions
                            SET is_active = 0,
                                notifications_enabled = 0,
                                updated_at = ?,
                                last_error = ?
                            WHERE id = ?
                            """,
                            (
                                current_dt.isoformat(timespec="seconds"),
                                error_text[:400],
                                int(subscription_row["id"]),
                            ),
                        )
                        result["deactivated"] += 1
                    else:
                        db.execute(
                            """
                            UPDATE staff_push_subscriptions
                            SET updated_at = ?, last_error = ?
                            WHERE id = ?
                            """,
                            (
                                current_dt.isoformat(timespec="seconds"),
                                error_text[:400],
                                int(subscription_row["id"]),
                            ),
                        )
                    _record_shift_alert_delivery(
                        db,
                        staff_id=int(staff_row["id"]),
                        subscription_id=int(subscription_row["id"]),
                        reminder_key=str(alarm["reminder_key"]),
                        shift_start_at=shift_start_dt.isoformat(timespec="seconds"),
                        scheduled_for=reminder_dt.isoformat(timespec="seconds"),
                        status="failed",
                        detail=error_text[:400],
                    )
                    db.commit()
                    result["errors"] += 1

        if result["notifications_sent"]:
            create_notification_for_database(
                database_path,
                title="Shift alerts delivered",
                message=(
                    f"{result['notifications_sent']} shift alarm"
                    f"{'' if result['notifications_sent'] == 1 else 's'} sent for upcoming staff shifts."
                ),
                category="attendance",
                audience="admin",
                tone="success",
                action_url="/admin/notifications",
            )
        return result
    finally:
        db.close()


def _subscription_parts(subscription_payload: Mapping[str, Any]) -> tuple[str, str, str]:
    endpoint = str(subscription_payload.get("endpoint") or "").strip()
    keys = subscription_payload.get("keys") if isinstance(subscription_payload.get("keys"), Mapping) else {}
    key_p256dh = str(keys.get("p256dh") or "").strip()
    key_auth = str(keys.get("auth") or "").strip()
    if not endpoint or not key_p256dh or not key_auth:
        raise ValueError("A complete push subscription is required.")
    return endpoint, key_p256dh, key_auth


def _subscription_info_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "endpoint": str(row["endpoint"] or ""),
        "keys": {
            "p256dh": str(row["p256dh_key"] or ""),
            "auth": str(row["auth_key"] or ""),
        },
    }


def _staff_status_for_database(db: sqlite3.Connection, staff_row: Mapping[str, Any], reference_dt: datetime) -> dict[str, Any]:
    attendance_day = resolve_shift_attendance_date(
        str(staff_row.get("shift_start") or "09:00"),
        str(staff_row.get("shift_end") or "17:00"),
        reference_dt,
    )
    rows = db.execute(
        """
        SELECT event_time, event_type
        FROM attendance_events
        WHERE staff_id = ? AND attendance_date = ?
        ORDER BY event_time ASC, id ASC
        """,
        (int(staff_row["id"]), attendance_day.isoformat()),
    ).fetchall()
    check_in_at = None
    check_out_at = None
    latest_type = ""
    for row in rows:
        event_dt = datetime.fromisoformat(str(row["event_time"]))
        latest_type = str(row["event_type"] or "")
        if latest_type == "check_in" and check_in_at is None:
            check_in_at = event_dt
        elif latest_type == "check_out":
            check_out_at = event_dt
    return {
        "check_in_at": check_in_at,
        "check_out_at": check_out_at,
        "currently_inside": bool(check_in_at and not check_out_at),
        "latest_event_type": latest_type,
    }


def _shift_notification_payload(
    *,
    staff_row: Mapping[str, Any],
    organization_slug: str,
    location_name: str,
    alarm: Mapping[str, Any],
) -> dict[str, Any]:
    staff_name = alarm.get("staff_name") or _staff_display_name(staff_row)
    shift_start_label = str(alarm.get("shift_start_label") or "--")
    return {
        "title": "Shift reminder",
        "body": (
            f"{staff_name}, your shift at {location_name} starts in 10 minutes. "
            f"Clock in by {shift_start_label}."
        ),
        "tag": f"shift-alert:{organization_slug}:{staff_row.get('id', 'staff')}:{alarm.get('reminder_key', 'shift')}",
        "icon": f"/pwa/icon-192.png?org={organization_slug}",
        "badge": f"/pwa/icon-180.png?org={organization_slug}",
        "url": f"/portal/{organization_slug}/staff/login",
        "vibrate": [250, 150, 250, 150, 500],
        "requireInteraction": True,
    }


def _shift_alert_already_sent(db: sqlite3.Connection, subscription_id: int, reminder_key: str) -> bool:
    row = db.execute(
        """
        SELECT 1
        FROM staff_shift_alert_logs
        WHERE subscription_id = ? AND reminder_key = ? AND status = 'sent'
        LIMIT 1
        """,
        (int(subscription_id), reminder_key),
    ).fetchone()
    return bool(row)


def _record_shift_alert_delivery(
    db: sqlite3.Connection,
    *,
    staff_id: int,
    subscription_id: int,
    reminder_key: str,
    shift_start_at: str,
    scheduled_for: str,
    status: str,
    detail: str,
) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    db.execute(
        """
        INSERT INTO staff_shift_alert_logs (
            staff_id,
            subscription_id,
            reminder_key,
            shift_start_at,
            scheduled_for,
            status,
            detail,
            sent_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subscription_id, reminder_key) DO UPDATE SET
            status = excluded.status,
            detail = excluded.detail,
            sent_at = excluded.sent_at
        """,
        (
            int(staff_id),
            int(subscription_id),
            reminder_key,
            shift_start_at,
            scheduled_for,
            status,
            detail,
            timestamp,
        ),
    )


def _send_web_push_message(
    *,
    subscription: Mapping[str, Any],
    payload: Mapping[str, Any],
    vapid: Mapping[str, str],
    settings: AppConfig,
) -> None:
    webpush, WebPushException = _load_push_dependencies()
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=vapid["private_key"],
            vapid_claims={"sub": f"mailto:{settings.web_push_contact_email}"},
        )
    except WebPushException as error:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code:
            raise RuntimeError(f"Web push failed with status {status_code}") from error
        raise RuntimeError(str(error) or "Web push delivery failed.") from error


def _resolve_vapid_keypair(settings: AppConfig) -> dict[str, str]:
    public_key = str(settings.web_push_vapid_public_key or "").strip()
    private_key = str(settings.web_push_vapid_private_key or "").strip()
    if public_key and private_key:
        return {"public_key": public_key, "private_key": private_key}

    private_key_path = settings.instance_dir / "web_push_vapid_private.pem"
    public_key_path = settings.instance_dir / "web_push_vapid_public.txt"
    if private_key_path.exists() and public_key_path.exists():
        return {
            "public_key": public_key_path.read_text(encoding="utf-8").strip(),
            "private_key": private_key_path.read_text(encoding="utf-8"),
        }

    ec, serialization, _webpush, _WebPushException = _load_push_dependencies(include_crypto=True)
    private_key_obj = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_numbers = private_key_obj.public_key().public_numbers()
    public_key_bytes = b"\x04" + public_numbers.x.to_bytes(32, "big") + public_numbers.y.to_bytes(32, "big")
    public_key_value = urlsafe_b64encode(public_key_bytes).rstrip(b"=").decode("utf-8")

    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key_path.write_text(private_pem, encoding="utf-8")
    public_key_path.write_text(public_key_value, encoding="utf-8")
    return {"public_key": public_key_value, "private_key": private_pem}


def _load_push_dependencies(*, include_crypto: bool = False):
    if include_crypto:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    else:
        ec = serialization = None
    from pywebpush import WebPushException, webpush

    if include_crypto:
        return ec, serialization, webpush, WebPushException
    return webpush, WebPushException


def _configure_background_connection(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA busy_timeout = 30000")


def _parse_clock_value(value: str) -> time | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        return time.fromisoformat(cleaned)
    except ValueError:
        return None


def _format_clock_label(value: str) -> str:
    clock = _parse_clock_value(value)
    if clock is None:
        return "--"
    return datetime.combine(datetime.today().date(), clock).strftime("%I:%M %p")


def _staff_display_name(staff: Mapping[str, Any]) -> str:
    return (
        f"{staff.get('first_name', '')} {staff.get('last_name', '')}".strip()
        or str(staff.get("staff_code") or "Staff")
    )
