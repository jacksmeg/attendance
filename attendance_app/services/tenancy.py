from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
import re
import shutil
import sqlite3

from flask import current_app, g, has_request_context, request, session

from attendance_app.config import AppConfig

LICENSE_STATUS_ACTIVE = "active"
LICENSE_STATUS_TRIAL = "trial"
LICENSE_STATUS_SUSPENDED = "suspended"
LICENSE_STATUS_EXPIRED = "expired"

LICENSE_STATUSES = {
    LICENSE_STATUS_ACTIVE,
    LICENSE_STATUS_TRIAL,
    LICENSE_STATUS_SUSPENDED,
    LICENSE_STATUS_EXPIRED,
}

BILLING_CYCLE_MONTHLY = "monthly"
BILLING_CYCLE_QUARTERLY = "quarterly"
BILLING_CYCLE_YEARLY = "yearly"
BILLING_CYCLE_MANUAL = "manual"

BILLING_CYCLES = {
    BILLING_CYCLE_MONTHLY,
    BILLING_CYCLE_QUARTERLY,
    BILLING_CYCLE_YEARLY,
    BILLING_CYCLE_MANUAL,
}


@dataclass(slots=True)
class OrganizationContext:
    slug: str
    display_name: str
    database_path: Path
    instance_dir: Path
    mock_store_path: Path
    hostnames: tuple[str, ...]
    is_default: bool = False
    license_status: str = LICENSE_STATUS_ACTIVE
    plan_name: str = "Standard"
    expires_on: str = ""
    billing_contact_name: str = ""
    billing_email: str = ""
    billing_phone: str = ""
    billing_cycle: str = BILLING_CYCLE_MONTHLY
    subscription_amount: float = 0.0
    renewal_due_on: str = ""
    last_payment_on: str = ""
    license_notes: str = ""
    grace_days: int = 0


def init_platform_registry(registry_path: Path) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(registry_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                database_path TEXT NOT NULL,
                instance_dir TEXT NOT NULL,
                mock_store_path TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS organization_domains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                hostname TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_organization_domains_org
            ON organization_domains(organization_id);
            """
        )
        _ensure_registry_column(
            db,
            "organizations",
            "license_status",
            f"TEXT NOT NULL DEFAULT '{LICENSE_STATUS_ACTIVE}'",
        )
        _ensure_registry_column(
            db,
            "organizations",
            "plan_name",
            "TEXT NOT NULL DEFAULT 'Standard'",
        )
        _ensure_registry_column(db, "organizations", "expires_on", "TEXT")
        _ensure_registry_column(db, "organizations", "billing_contact_name", "TEXT")
        _ensure_registry_column(db, "organizations", "billing_email", "TEXT")
        _ensure_registry_column(db, "organizations", "billing_phone", "TEXT")
        _ensure_registry_column(
            db,
            "organizations",
            "billing_cycle",
            f"TEXT NOT NULL DEFAULT '{BILLING_CYCLE_MONTHLY}'",
        )
        _ensure_registry_column(
            db,
            "organizations",
            "subscription_amount",
            "REAL NOT NULL DEFAULT 0",
        )
        _ensure_registry_column(db, "organizations", "renewal_due_on", "TEXT")
        _ensure_registry_column(db, "organizations", "last_payment_on", "TEXT")
        _ensure_registry_column(db, "organizations", "license_notes", "TEXT")
        _ensure_registry_column(
            db,
            "organizations",
            "grace_days",
            "INTEGER NOT NULL DEFAULT 0",
        )
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS organization_license_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT 'Platform Super Admin',
                previous_status TEXT,
                next_status TEXT,
                previous_expires_on TEXT,
                next_expires_on TEXT,
                amount REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_license_events_org
            ON organization_license_events(organization_id, created_at DESC);
            """
        )
        db.commit()
    finally:
        db.close()
    _clear_organization_registry_caches()


def ensure_default_organization(settings: AppConfig) -> OrganizationContext:
    return provision_organization(
        settings,
        slug=settings.default_organization_slug,
        display_name=settings.app_name,
        database_path=settings.database_path,
        instance_dir=settings.instance_dir,
        mock_store_path=settings.mock_store_path,
        is_default=True,
        plan_name="Standard",
        license_status=LICENSE_STATUS_ACTIVE,
    )


def provision_organization(
    settings: AppConfig,
    *,
    slug: str,
    display_name: str,
    hostnames: Iterable[str] = (),
    database_path: Path | None = None,
    instance_dir: Path | None = None,
    mock_store_path: Path | None = None,
    is_default: bool = False,
    license_status: str = LICENSE_STATUS_ACTIVE,
    plan_name: str = "Standard",
    expires_on: str = "",
    billing_contact_name: str = "",
    billing_email: str = "",
    billing_phone: str = "",
    billing_cycle: str = BILLING_CYCLE_MONTHLY,
    subscription_amount: float | int | str = 0,
    renewal_due_on: str = "",
    last_payment_on: str = "",
    license_notes: str = "",
    grace_days: int | str = 0,
) -> OrganizationContext:
    normalized_slug = _normalize_slug(slug)
    if not normalized_slug:
        raise ValueError("Organization slug must contain letters or numbers.")

    display_value = str(display_name).strip() or normalized_slug.replace("-", " ").title()
    if database_path is None or instance_dir is None or mock_store_path is None:
        org_root = settings.instance_dir / "organizations" / normalized_slug
        instance_dir = Path(instance_dir or org_root).resolve()
        database_path = Path(database_path or (instance_dir / "attendance.db")).resolve()
        mock_store_path = Path(mock_store_path or (instance_dir / "mock_fingerprint_store.json")).resolve()
    else:
        instance_dir = Path(instance_dir).resolve()
        database_path = Path(database_path).resolve()
        mock_store_path = Path(mock_store_path).resolve()

    instance_dir.mkdir(parents=True, exist_ok=True)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    mock_store_path.parent.mkdir(parents=True, exist_ok=True)

    normalized_hosts = [
        host for host in {_normalize_hostname(value) for value in hostnames} if host
    ]
    timestamp = datetime.now().isoformat(timespec="seconds")
    normalized_license_status = _normalize_license_status(license_status)
    normalized_billing_cycle = _normalize_billing_cycle(billing_cycle)

    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        if is_default:
            db.execute("UPDATE organizations SET is_default = 0")
        db.execute(
            """
            INSERT INTO organizations (
                slug, display_name, database_path, instance_dir, mock_store_path,
                is_default, is_active, created_at, updated_at,
                license_status, plan_name, expires_on,
                billing_contact_name, billing_email, billing_phone,
                billing_cycle, subscription_amount, renewal_due_on, last_payment_on, license_notes, grace_days
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                display_name = excluded.display_name,
                database_path = excluded.database_path,
                instance_dir = excluded.instance_dir,
                mock_store_path = excluded.mock_store_path,
                is_default = excluded.is_default,
                updated_at = excluded.updated_at,
                license_status = excluded.license_status,
                plan_name = excluded.plan_name,
                expires_on = excluded.expires_on,
                billing_contact_name = excluded.billing_contact_name,
                billing_email = excluded.billing_email,
                billing_phone = excluded.billing_phone,
                billing_cycle = excluded.billing_cycle,
                subscription_amount = excluded.subscription_amount,
                renewal_due_on = excluded.renewal_due_on,
                last_payment_on = excluded.last_payment_on,
                license_notes = excluded.license_notes,
                grace_days = excluded.grace_days
            """,
            (
                normalized_slug,
                display_value,
                str(database_path),
                str(instance_dir),
                str(mock_store_path),
                1 if is_default else 0,
                timestamp,
                timestamp,
                normalized_license_status,
                str(plan_name).strip() or "Standard",
                _normalize_optional_date(expires_on),
                str(billing_contact_name).strip(),
                str(billing_email).strip(),
                str(billing_phone).strip(),
                normalized_billing_cycle,
                _to_amount(subscription_amount),
                _normalize_optional_date(renewal_due_on),
                _normalize_optional_date(last_payment_on),
                str(license_notes).strip(),
                _to_non_negative_int(grace_days),
            ),
        )
        row = db.execute(
            "SELECT id FROM organizations WHERE slug = ?",
            (normalized_slug,),
        ).fetchone()
        if not row:
            raise RuntimeError("Organization could not be created.")
        organization_id = int(row["id"])
        for hostname in normalized_hosts:
            existing = db.execute(
                """
                SELECT o.slug
                FROM organization_domains d
                JOIN organizations o ON o.id = d.organization_id
                WHERE d.hostname = ?
                """,
                (hostname,),
            ).fetchone()
            if existing and str(existing["slug"]) != normalized_slug:
                raise ValueError(
                    f"Hostname '{hostname}' is already assigned to organization '{existing['slug']}'."
                )
            db.execute(
                """
                INSERT OR IGNORE INTO organization_domains (organization_id, hostname, created_at)
                VALUES (?, ?, ?)
                """,
                (organization_id, hostname, timestamp),
            )
        db.commit()
    finally:
        db.close()

    _clear_organization_registry_caches()
    organization = get_organization_by_slug(settings, normalized_slug)
    if not organization:
        raise RuntimeError("Organization context could not be loaded.")
    return organization


def list_organizations(settings: AppConfig) -> list[OrganizationContext]:
    return list(_cached_list_organizations(str(settings.platform_registry_path)))


@lru_cache(maxsize=32)
def _cached_list_organizations(registry_path: str) -> tuple[OrganizationContext, ...]:
    db = sqlite3.connect(registry_path)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            _organization_select_query(
                where_clause="WHERE o.is_active = 1",
                tail_clause="ORDER BY o.is_default DESC, o.display_name, o.slug",
            )
        ).fetchall()
        return tuple(_row_to_context(row) for row in rows)
    finally:
        db.close()


def count_organizations(settings: AppConfig) -> int:
    return len(list_organizations(settings))


def count_organization_hostnames(settings: AppConfig) -> int:
    return sum(len(org.hostnames) for org in list_organizations(settings))


def count_organizations_by_license(settings: AppConfig, *statuses: str) -> int:
    target_statuses = {_normalize_license_status(status) for status in statuses}
    count = 0
    for organization in list_organizations(settings):
        access = get_organization_access_state(organization)
        if access["status"] in target_statuses:
            count += 1
    return count


def get_organization_by_slug(settings: AppConfig, slug: str) -> OrganizationContext | None:
    normalized_slug = _normalize_slug(slug)
    if not normalized_slug:
        return None
    return _cached_organization_by_slug(str(settings.platform_registry_path), normalized_slug)


@lru_cache(maxsize=256)
def _cached_organization_by_slug(
    registry_path: str,
    normalized_slug: str,
) -> OrganizationContext | None:
    db = sqlite3.connect(registry_path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            _organization_select_query(
                where_clause="WHERE o.slug = ? AND o.is_active = 1",
                tail_clause="LIMIT 1",
            ),
            (normalized_slug,),
        ).fetchone()
        return _row_to_context(row) if row else None
    finally:
        db.close()


def update_organization(
    settings: AppConfig,
    *,
    slug: str,
    display_name: str,
    hostnames: Iterable[str] = (),
    is_default: bool | None = None,
    license_status: str = LICENSE_STATUS_ACTIVE,
    plan_name: str = "Standard",
    expires_on: str = "",
    billing_contact_name: str = "",
    billing_email: str = "",
    billing_phone: str = "",
    billing_cycle: str = BILLING_CYCLE_MONTHLY,
    subscription_amount: float | int | str = 0,
    renewal_due_on: str = "",
    last_payment_on: str = "",
    license_notes: str = "",
    grace_days: int | str = 0,
) -> OrganizationContext:
    organization = get_organization_by_slug(settings, slug)
    if not organization:
        raise ValueError(f"Organization '{slug}' was not found.")

    normalized_hosts = [
        host for host in {_normalize_hostname(value) for value in hostnames} if host
    ]
    timestamp = datetime.now().isoformat(timespec="seconds")
    normalized_license_status = _normalize_license_status(license_status)
    normalized_billing_cycle = _normalize_billing_cycle(billing_cycle)

    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        if is_default is True:
            db.execute("UPDATE organizations SET is_default = 0")

        current_default = organization.is_default if is_default is None else bool(is_default)

        db.execute(
            """
            UPDATE organizations
            SET
                display_name = ?,
                is_default = ?,
                updated_at = ?,
                license_status = ?,
                plan_name = ?,
                expires_on = ?,
                billing_contact_name = ?,
                billing_email = ?,
                billing_phone = ?,
                billing_cycle = ?,
                subscription_amount = ?,
                renewal_due_on = ?,
                last_payment_on = ?,
                license_notes = ?,
                grace_days = ?
            WHERE slug = ?
            """,
            (
                str(display_name).strip() or organization.display_name,
                1 if current_default else 0,
                timestamp,
                normalized_license_status,
                str(plan_name).strip() or organization.plan_name or "Standard",
                _normalize_optional_date(expires_on),
                str(billing_contact_name).strip(),
                str(billing_email).strip(),
                str(billing_phone).strip(),
                normalized_billing_cycle,
                _to_amount(subscription_amount),
                _normalize_optional_date(renewal_due_on),
                _normalize_optional_date(last_payment_on),
                str(license_notes).strip(),
                _to_non_negative_int(grace_days),
                organization.slug,
            ),
        )

        conflict_rows = db.execute(
            """
            SELECT d.hostname, o.slug
            FROM organization_domains d
            JOIN organizations o ON o.id = d.organization_id
            WHERE d.hostname IN ({placeholders}) AND o.slug <> ?
            """.format(placeholders=",".join("?" for _ in normalized_hosts) or "''"),
            [*normalized_hosts, organization.slug],
        ).fetchall()
        if conflict_rows:
            conflict = conflict_rows[0]
            raise ValueError(
                f"Hostname '{conflict['hostname']}' is already assigned to organization '{conflict['slug']}'."
            )

        row = db.execute(
            "SELECT id FROM organizations WHERE slug = ?",
            (organization.slug,),
        ).fetchone()
        if not row:
            raise RuntimeError("Organization record was not found during update.")
        organization_id = int(row["id"])

        db.execute("DELETE FROM organization_domains WHERE organization_id = ?", (organization_id,))
        for hostname in normalized_hosts:
            db.execute(
                """
                INSERT INTO organization_domains (organization_id, hostname, created_at)
                VALUES (?, ?, ?)
                """,
                (organization_id, hostname, timestamp),
            )
        db.commit()
    finally:
        db.close()

    _clear_organization_registry_caches()
    updated = get_organization_by_slug(settings, organization.slug)
    if not updated:
        raise RuntimeError("Organization could not be reloaded after update.")
    return updated


def delete_organization(settings: AppConfig, *, slug: str) -> OrganizationContext:
    organization = get_organization_by_slug(settings, slug)
    if not organization:
        raise ValueError(f"Organization '{slug}' was not found.")
    if organization.is_default or organization.slug == settings.default_organization_slug:
        raise ValueError("The default organization cannot be deleted.")

    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("DELETE FROM organizations WHERE slug = ?", (organization.slug,))
        db.commit()
    finally:
        db.close()

    if organization.database_path.exists():
        organization.database_path.unlink(missing_ok=True)
    if organization.mock_store_path.exists():
        organization.mock_store_path.unlink(missing_ok=True)
    if organization.instance_dir.exists():
        shutil.rmtree(organization.instance_dir, ignore_errors=True)

    _clear_organization_registry_caches()
    return organization


def get_organization_by_host(settings: AppConfig, hostname: str) -> OrganizationContext | None:
    normalized_host = _normalize_hostname(hostname)
    if not normalized_host:
        return None
    return _cached_organization_by_host(str(settings.platform_registry_path), normalized_host)


@lru_cache(maxsize=256)
def _cached_organization_by_host(
    registry_path: str,
    normalized_host: str,
) -> OrganizationContext | None:
    db = sqlite3.connect(registry_path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            _organization_select_query(
                where_clause="WHERE d.hostname = ? AND o.is_active = 1",
                tail_clause="LIMIT 1",
            ),
            (normalized_host,),
        ).fetchone()
        return _row_to_context(row) if row else None
    finally:
        db.close()


def get_current_organization() -> OrganizationContext:
    if "organization_context" in g:
        return g.organization_context

    settings: AppConfig = current_app.config["APP_SETTINGS"]
    organization: OrganizationContext | None = None

    if has_request_context():
        session_slug = str(
            session.get("portal_organization_slug")
            or session.get("pending_organization_slug")
            or session.get("organization_slug")
            or ""
        ).strip()
        if session_slug:
            organization = get_organization_by_slug(settings, session_slug)
        if organization is None:
            organization = get_organization_by_host(settings, request.host)

    if organization is None:
        organization = get_organization_by_slug(settings, settings.default_organization_slug)

    if organization is None:
        organization = ensure_default_organization(settings)

    g.organization_context = organization
    return organization


def set_current_organization(organization: OrganizationContext) -> None:
    existing_db = g.pop("db", None)
    if existing_db is not None:
        existing_db.close()
    g.organization_context = organization


def get_current_organization_access_state() -> dict[str, Any]:
    if "organization_access_state" in g:
        return g.organization_access_state
    access_state = get_organization_access_state(get_current_organization())
    g.organization_access_state = access_state
    return access_state


def get_organization_access_state(organization: OrganizationContext) -> dict[str, Any]:
    expires_value = _parse_date(organization.expires_on)
    renewal_due_value = _parse_date(organization.renewal_due_on)
    today_value = date.today()
    effective_status = organization.license_status
    grace_days = max(0, int(organization.grace_days or 0))
    state = effective_status

    if effective_status != LICENSE_STATUS_SUSPENDED and expires_value and expires_value < today_value:
        effective_status = LICENSE_STATUS_EXPIRED
    elif effective_status not in LICENSE_STATUSES:
        effective_status = LICENSE_STATUS_ACTIVE

    days_remaining: int | None = None
    if expires_value:
        days_remaining = (expires_value - today_value).days

    grace_days_remaining: int | None = None
    if days_remaining is not None and days_remaining < 0 and grace_days > 0:
        overdue_days = abs(days_remaining)
        if overdue_days <= grace_days and organization.license_status in {
            LICENSE_STATUS_ACTIVE,
            LICENSE_STATUS_TRIAL,
        }:
            state = "grace"
            grace_days_remaining = grace_days - overdue_days
        else:
            state = effective_status
    elif effective_status in {LICENSE_STATUS_ACTIVE, LICENSE_STATUS_TRIAL}:
        if days_remaining is not None and 0 <= days_remaining <= 14:
            state = "expiring"
        else:
            state = effective_status
    else:
        state = effective_status

    access_allowed = effective_status in {LICENSE_STATUS_ACTIVE, LICENSE_STATUS_TRIAL}
    if state == "grace":
        access_allowed = True

    reason = "License active."
    if state == "grace":
        reason = "License expired, but the grace period is still active."
    elif effective_status == LICENSE_STATUS_TRIAL:
        reason = "Trial access is active."
        if days_remaining is not None and 0 <= days_remaining <= 7:
            reason = "Trial access is active and ends soon."
    elif state == "expiring":
        reason = "License active, but renewal is due soon."
    elif effective_status == LICENSE_STATUS_SUSPENDED:
        reason = "Organization access is suspended."
    elif effective_status == LICENSE_STATUS_EXPIRED:
        reason = "Organization license has expired."

    renewal_days_remaining: int | None = None
    if renewal_due_value:
        renewal_days_remaining = (renewal_due_value - today_value).days

    return {
        "status": effective_status,
        "state": state,
        "access_allowed": access_allowed,
        "expires_on": organization.expires_on,
        "expires_date": expires_value,
        "days_remaining": days_remaining,
        "grace_days": grace_days,
        "grace_days_remaining": grace_days_remaining,
        "renewal_due_on": organization.renewal_due_on,
        "renewal_due_date": renewal_due_value,
        "renewal_days_remaining": renewal_days_remaining,
        "reason": reason,
    }


def record_organization_license_event(
    settings: AppConfig,
    *,
    slug: str,
    event_type: str,
    title: str,
    actor_name: str = "Platform Super Admin",
    details: str = "",
    previous_status: str = "",
    next_status: str = "",
    previous_expires_on: str = "",
    next_expires_on: str = "",
    amount: float | int | str | None = None,
) -> None:
    organization = get_organization_by_slug(settings, slug)
    if not organization:
        raise ValueError(f"Organization '{slug}' was not found.")

    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            "SELECT id FROM organizations WHERE slug = ?",
            (organization.slug,),
        ).fetchone()
        if not row:
            raise ValueError(f"Organization '{slug}' was not found.")
        timestamp = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            INSERT INTO organization_license_events (
                organization_id,
                event_type,
                title,
                details,
                actor_name,
                previous_status,
                next_status,
                previous_expires_on,
                next_expires_on,
                amount,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["id"]),
                str(event_type).strip() or "license_update",
                str(title).strip() or "License updated",
                str(details).strip(),
                str(actor_name).strip() or "Platform Super Admin",
                str(previous_status).strip(),
                str(next_status).strip(),
                _normalize_optional_date(previous_expires_on),
                _normalize_optional_date(next_expires_on),
                _to_amount(amount) if amount not in {None, ""} else None,
                timestamp,
            ),
        )
        db.commit()
    finally:
        db.close()


def list_organization_license_events(
    settings: AppConfig,
    slug: str,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    organization = get_organization_by_slug(settings, slug)
    if not organization:
        return []
    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            """
            SELECT
                e.event_type,
                e.title,
                e.details,
                e.actor_name,
                e.previous_status,
                e.next_status,
                e.previous_expires_on,
                e.next_expires_on,
                e.amount,
                e.created_at
            FROM organization_license_events e
            JOIN organizations o ON o.id = e.organization_id
            WHERE o.slug = ?
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT ?
            """,
            (organization.slug, max(1, int(limit))),
        ).fetchall()
        return [
            {
                "event_type": str(row["event_type"] or ""),
                "title": str(row["title"] or ""),
                "details": str(row["details"] or ""),
                "actor_name": str(row["actor_name"] or ""),
                "previous_status": str(row["previous_status"] or ""),
                "next_status": str(row["next_status"] or ""),
                "previous_expires_on": str(row["previous_expires_on"] or ""),
                "next_expires_on": str(row["next_expires_on"] or ""),
                "amount": float(row["amount"] or 0),
                "created_at": str(row["created_at"] or ""),
            }
            for row in rows
        ]
    finally:
        db.close()


def apply_organization_license_action(
    settings: AppConfig,
    *,
    slug: str,
    action: str,
    actor_name: str = "Platform Super Admin",
) -> OrganizationContext:
    organization = get_organization_by_slug(settings, slug)
    if not organization:
        raise ValueError(f"Organization '{slug}' was not found.")

    current_expiry = _parse_date(organization.expires_on)
    today_value = date.today()
    base_date = current_expiry if current_expiry and current_expiry >= today_value else today_value

    target_status = organization.license_status
    target_expiry = organization.expires_on
    target_renewal = organization.renewal_due_on
    target_payment = organization.last_payment_on
    title = "License action applied"
    details = ""
    action_key = str(action or "").strip().lower()

    if action_key == "start_trial":
        target_status = LICENSE_STATUS_TRIAL
        target_expiry = (today_value + timedelta(days=14)).isoformat()
        target_renewal = target_expiry
        title = "Trial started"
        details = "A fresh 14-day trial was started from the platform portal."
    elif action_key == "activate_30":
        target_status = LICENSE_STATUS_ACTIVE
        target_expiry = (today_value + timedelta(days=30)).isoformat()
        target_renewal = target_expiry
        title = "30-day activation applied"
        details = "A 30-day active license window was applied."
    elif action_key == "renew_cycle":
        target_status = LICENSE_STATUS_ACTIVE
        next_expiry = _add_billing_cycle(base_date, organization.billing_cycle)
        target_expiry = next_expiry.isoformat()
        target_renewal = target_expiry
        target_payment = today_value.isoformat()
        title = "License renewed"
        details = f"The license was renewed for the next {organization.billing_cycle} cycle."
    elif action_key == "suspend":
        target_status = LICENSE_STATUS_SUSPENDED
        title = "Institution suspended"
        details = "Access was suspended from the platform control room."
    elif action_key == "mark_expired":
        target_status = LICENSE_STATUS_EXPIRED
        target_expiry = (today_value - timedelta(days=1)).isoformat()
        target_renewal = target_expiry
        title = "License marked expired"
        details = "The license was manually marked as expired."
    else:
        raise ValueError("Choose a valid license action.")

    updated = update_organization(
        settings,
        slug=organization.slug,
        display_name=organization.display_name,
        hostnames=organization.hostnames,
        is_default=organization.is_default,
        license_status=target_status,
        plan_name=organization.plan_name,
        expires_on=target_expiry,
        billing_contact_name=organization.billing_contact_name,
        billing_email=organization.billing_email,
        billing_phone=organization.billing_phone,
        billing_cycle=organization.billing_cycle,
        subscription_amount=organization.subscription_amount,
        renewal_due_on=target_renewal,
        last_payment_on=target_payment,
        license_notes=organization.license_notes,
        grace_days=organization.grace_days,
    )
    record_organization_license_event(
        settings,
        slug=organization.slug,
        event_type=action_key,
        title=title,
        details=details,
        actor_name=actor_name,
        previous_status=organization.license_status,
        next_status=updated.license_status,
        previous_expires_on=organization.expires_on,
        next_expires_on=updated.expires_on,
        amount=updated.subscription_amount,
    )
    return updated


def _organization_select_query(where_clause: str = "", tail_clause: str = "") -> str:
    return f"""
        SELECT
            o.slug,
            o.display_name,
            o.database_path,
            o.instance_dir,
            o.mock_store_path,
            o.is_default,
            o.license_status,
            o.plan_name,
            o.expires_on,
            o.billing_contact_name,
            o.billing_email,
            o.billing_phone,
            o.billing_cycle,
            o.subscription_amount,
            o.renewal_due_on,
            o.last_payment_on,
            o.license_notes,
            o.grace_days,
            GROUP_CONCAT(d.hostname, ',') AS hostnames
        FROM organizations o
        LEFT JOIN organization_domains d ON d.organization_id = o.id
        {where_clause}
        GROUP BY o.id
        {tail_clause}
    """


def _row_to_context(row: sqlite3.Row) -> OrganizationContext:
    hostnames_value = str(row["hostnames"] or "")
    hostnames = tuple(host for host in hostnames_value.split(",") if host)
    return OrganizationContext(
        slug=str(row["slug"]),
        display_name=str(row["display_name"]),
        database_path=Path(str(row["database_path"])).resolve(),
        instance_dir=Path(str(row["instance_dir"])).resolve(),
        mock_store_path=Path(str(row["mock_store_path"])).resolve(),
        hostnames=hostnames,
        is_default=bool(row["is_default"]),
        license_status=_normalize_license_status(str(row["license_status"] or LICENSE_STATUS_ACTIVE)),
        plan_name=str(row["plan_name"] or "Standard"),
        expires_on=str(row["expires_on"] or ""),
        billing_contact_name=str(row["billing_contact_name"] or ""),
        billing_email=str(row["billing_email"] or ""),
        billing_phone=str(row["billing_phone"] or ""),
        billing_cycle=_normalize_billing_cycle(str(row["billing_cycle"] or BILLING_CYCLE_MONTHLY)),
        subscription_amount=float(row["subscription_amount"] or 0),
        renewal_due_on=str(row["renewal_due_on"] or ""),
        last_payment_on=str(row["last_payment_on"] or ""),
        license_notes=str(row["license_notes"] or ""),
        grace_days=_to_non_negative_int(row["grace_days"]),
    )


def _ensure_registry_column(
    db: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_type: str,
) -> None:
    rows = db.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {row["name"] for row in rows}
    if column_name in existing:
        return
    db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def _clear_organization_registry_caches() -> None:
    _cached_list_organizations.cache_clear()
    _cached_organization_by_slug.cache_clear()
    _cached_organization_by_host.cache_clear()


def _normalize_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return cleaned.strip("-")


def _normalize_hostname(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = cleaned.split("/", 1)[0]
    cleaned = cleaned.split(":", 1)[0]
    return cleaned.strip(".")


def _normalize_license_status(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in LICENSE_STATUSES:
        return cleaned
    return LICENSE_STATUS_ACTIVE


def _normalize_billing_cycle(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if cleaned in BILLING_CYCLES:
        return cleaned
    return BILLING_CYCLE_MONTHLY


def _normalize_optional_date(value: str | date | None) -> str:
    if not value:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    cleaned = str(value).strip()
    if not cleaned:
        return ""
    parsed = _parse_date(cleaned)
    return parsed.isoformat() if parsed else ""


def _parse_date(value: str | None) -> date | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def _to_amount(value: float | int | str | None) -> float:
    try:
        return round(float(str(value or "0").strip()), 2)
    except ValueError:
        return 0.0


def _to_non_negative_int(value: Any) -> int:
    try:
        return max(0, int(str(value or "0").strip()))
    except (TypeError, ValueError):
        return 0


def _add_billing_cycle(base_date: date, billing_cycle: str) -> date:
    normalized_cycle = _normalize_billing_cycle(billing_cycle)
    months = {
        BILLING_CYCLE_MONTHLY: 1,
        BILLING_CYCLE_QUARTERLY: 3,
        BILLING_CYCLE_YEARLY: 12,
        BILLING_CYCLE_MANUAL: 1,
    }.get(normalized_cycle, 1)
    return _add_months(base_date, months)


def _add_months(base_date: date, months: int) -> date:
    month_index = base_date.month - 1 + months
    year = base_date.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(base_date.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    next_month = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    current_month = date(year, month, 1)
    return (next_month - current_month).days
