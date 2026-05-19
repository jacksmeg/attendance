from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
import re
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
        db.commit()
    finally:
        db.close()


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
                billing_cycle, subscription_amount, renewal_due_on, last_payment_on, license_notes
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                license_notes = excluded.license_notes
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

    organization = get_organization_by_slug(settings, normalized_slug)
    if not organization:
        raise RuntimeError("Organization context could not be loaded.")
    return organization


def list_organizations(settings: AppConfig) -> list[OrganizationContext]:
    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            _organization_select_query(
                where_clause="WHERE o.is_active = 1",
                tail_clause="ORDER BY o.is_default DESC, o.display_name, o.slug",
            )
        ).fetchall()
        return [_row_to_context(row) for row in rows]
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
    db = sqlite3.connect(settings.platform_registry_path)
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
                license_notes = ?
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

    updated = get_organization_by_slug(settings, organization.slug)
    if not updated:
        raise RuntimeError("Organization could not be reloaded after update.")
    return updated


def get_organization_by_host(settings: AppConfig, hostname: str) -> OrganizationContext | None:
    normalized_host = _normalize_hostname(hostname)
    if not normalized_host:
        return None
    db = sqlite3.connect(settings.platform_registry_path)
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
        organization = get_organization_by_host(settings, request.host)
        if organization is None:
            session_slug = str(
                session.get("organization_slug")
                or session.get("pending_organization_slug")
                or ""
            ).strip()
            if session_slug:
                organization = get_organization_by_slug(settings, session_slug)

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
    return get_organization_access_state(get_current_organization())


def get_organization_access_state(organization: OrganizationContext) -> dict[str, Any]:
    expires_value = _parse_date(organization.expires_on)
    today_value = date.today()
    effective_status = organization.license_status

    if effective_status != LICENSE_STATUS_SUSPENDED and expires_value and expires_value < today_value:
        effective_status = LICENSE_STATUS_EXPIRED
    elif effective_status not in LICENSE_STATUSES:
        effective_status = LICENSE_STATUS_ACTIVE

    days_remaining: int | None = None
    if expires_value:
        days_remaining = (expires_value - today_value).days

    access_allowed = effective_status in {LICENSE_STATUS_ACTIVE, LICENSE_STATUS_TRIAL}
    reason = "License active."
    if effective_status == LICENSE_STATUS_TRIAL:
        reason = "Trial access is active."
    elif effective_status == LICENSE_STATUS_SUSPENDED:
        reason = "Organization access is suspended."
    elif effective_status == LICENSE_STATUS_EXPIRED:
        reason = "Organization license has expired."

    return {
        "status": effective_status,
        "access_allowed": access_allowed,
        "expires_on": organization.expires_on,
        "expires_date": expires_value,
        "days_remaining": days_remaining,
        "reason": reason,
    }


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
