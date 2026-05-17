from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
import re
import sqlite3

from flask import current_app, g, has_request_context, request

from attendance_app.config import AppConfig


@dataclass(slots=True)
class OrganizationContext:
    slug: str
    display_name: str
    database_path: Path
    instance_dir: Path
    mock_store_path: Path
    hostnames: tuple[str, ...]
    is_default: bool = False


def init_platform_registry(registry_path: Path) -> None:
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(registry_path)
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
                is_default, is_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                display_name = excluded.display_name,
                database_path = excluded.database_path,
                instance_dir = excluded.instance_dir,
                mock_store_path = excluded.mock_store_path,
                is_default = excluded.is_default,
                updated_at = excluded.updated_at
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
            """
            SELECT
                o.slug,
                o.display_name,
                o.database_path,
                o.instance_dir,
                o.mock_store_path,
                o.is_default,
                GROUP_CONCAT(d.hostname, ',') AS hostnames
            FROM organizations o
            LEFT JOIN organization_domains d ON d.organization_id = o.id
            WHERE o.is_active = 1
            GROUP BY o.id
            ORDER BY o.is_default DESC, o.display_name, o.slug
            """
        ).fetchall()
        return [_row_to_context(row) for row in rows]
    finally:
        db.close()


def count_organizations(settings: AppConfig) -> int:
    return len(list_organizations(settings))


def count_organization_hostnames(settings: AppConfig) -> int:
    return sum(len(org.hostnames) for org in list_organizations(settings))


def get_organization_by_slug(settings: AppConfig, slug: str) -> OrganizationContext | None:
    normalized_slug = _normalize_slug(slug)
    if not normalized_slug:
        return None
    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            """
            SELECT
                o.slug,
                o.display_name,
                o.database_path,
                o.instance_dir,
                o.mock_store_path,
                o.is_default,
                GROUP_CONCAT(d.hostname, ',') AS hostnames
            FROM organizations o
            LEFT JOIN organization_domains d ON d.organization_id = o.id
            WHERE o.slug = ? AND o.is_active = 1
            GROUP BY o.id
            LIMIT 1
            """,
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
) -> OrganizationContext:
    organization = get_organization_by_slug(settings, slug)
    if not organization:
        raise ValueError(f"Organization '{slug}' was not found.")

    normalized_hosts = [
        host for host in {_normalize_hostname(value) for value in hostnames} if host
    ]
    timestamp = datetime.now().isoformat(timespec="seconds")

    db = sqlite3.connect(settings.platform_registry_path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys = ON")
        if is_default is True:
            db.execute("UPDATE organizations SET is_default = 0")

        if is_default is None:
            current_default = organization.is_default
        else:
            current_default = bool(is_default)

        db.execute(
            """
            UPDATE organizations
            SET display_name = ?, is_default = ?, updated_at = ?
            WHERE slug = ?
            """,
            (
                str(display_name).strip() or organization.display_name,
                1 if current_default else 0,
                timestamp,
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
            """
            SELECT
                o.slug,
                o.display_name,
                o.database_path,
                o.instance_dir,
                o.mock_store_path,
                o.is_default,
                GROUP_CONCAT(d.hostname, ',') AS hostnames
            FROM organization_domains d
            JOIN organizations o ON o.id = d.organization_id
            WHERE d.hostname = ? AND o.is_active = 1
            GROUP BY o.id
            LIMIT 1
            """,
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
    )


def _normalize_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower())
    return cleaned.strip("-")


def _normalize_hostname(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = cleaned.split("/", 1)[0]
    cleaned = cleaned.split(":", 1)[0]
    return cleaned.strip(".")
