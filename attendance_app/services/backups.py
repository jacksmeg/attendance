from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
import json
import shutil
import sqlite3
import zipfile
from uuid import uuid4

from attendance_app.db import init_db
from attendance_app.services.tenancy import OrganizationContext

TENANT_DIRECTORY_NAMES = (
    "staff_photos",
    "system_branding",
    "staff_selfie_audits",
    "enrollment_sessions",
)
TENANT_FILE_NAMES = ("mock_fingerprint_store.json",)

BACKUP_REASON_MANUAL = "manual"
BACKUP_REASON_AUTOMATIC = "automatic"
BACKUP_REASON_PRE_RESTORE = "pre-restore"
AUTO_BACKUP_INTERVAL = timedelta(hours=24)
MAX_BACKUP_ARCHIVES = 20
BACKUP_MANIFEST_NAME = "backup-manifest.json"


@dataclass(slots=True)
class BackupSnapshot:
    name: str
    path: Path
    reason: str
    created_at: datetime
    size_bytes: int
    created_label: str
    size_label: str
    reason_label: str


def get_backup_directory(organization: OrganizationContext) -> Path:
    backup_dir = organization.instance_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def create_organization_backup(
    organization: OrganizationContext,
    *,
    reason: str = BACKUP_REASON_MANUAL,
    note: str = "",
    keep: int = MAX_BACKUP_ARCHIVES,
) -> Path:
    backup_dir = get_backup_directory(organization)
    timestamp = datetime.now()
    reason_slug = _slugify_reason(reason)
    archive_name = f"{organization.slug}-{reason_slug}-backup-{timestamp.strftime('%Y%m%d-%H%M%S')}.zip"
    archive_path = backup_dir / archive_name

    if not organization.database_path.exists():
        init_db(organization.database_path)

    temp_dir = backup_dir / f"_snapshot-{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        database_snapshot = temp_dir / "attendance.db"
        _snapshot_database(organization.database_path, database_snapshot)

        manifest = {
            "organization_slug": organization.slug,
            "display_name": organization.display_name,
            "created_at": timestamp.isoformat(timespec="seconds"),
            "reason": reason,
            "note": note,
        }

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(BACKUP_MANIFEST_NAME, json.dumps(manifest, indent=2))
            archive.write(database_snapshot, "database/attendance.db")
            _write_tenant_media(archive, organization)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    _prune_old_archives(backup_dir, keep=keep)
    return archive_path


def list_organization_backups(
    organization: OrganizationContext,
    *,
    limit: int = 8,
) -> list[BackupSnapshot]:
    backup_dir = get_backup_directory(organization)
    snapshots: list[BackupSnapshot] = []
    for path in sorted(backup_dir.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        manifest = _read_manifest(path)
        created_at = _parse_manifest_datetime(manifest.get("created_at", ""))
        if created_at is None:
            created_at = datetime.fromtimestamp(path.stat().st_mtime)
        reason = str(manifest.get("reason", "") or _infer_reason_from_name(path.name)).strip() or BACKUP_REASON_MANUAL
        snapshots.append(
            BackupSnapshot(
                name=path.name,
                path=path,
                reason=reason,
                created_at=created_at,
                size_bytes=path.stat().st_size,
                created_label=created_at.strftime("%d %b %Y, %I:%M %p"),
                size_label=_humanize_bytes(path.stat().st_size),
                reason_label=_reason_label(reason),
            )
        )
        if len(snapshots) >= limit:
            break
    return snapshots


def ensure_automatic_backup(organization: OrganizationContext) -> Path | None:
    recent = next(
        (
            snapshot
            for snapshot in list_organization_backups(organization, limit=10)
            if snapshot.reason == BACKUP_REASON_AUTOMATIC
        ),
        None,
    )
    if recent and datetime.now() - recent.created_at < AUTO_BACKUP_INTERVAL:
        return None
    return create_organization_backup(
        organization,
        reason=BACKUP_REASON_AUTOMATIC,
        note="Scheduled automatic platform snapshot.",
    )


def ensure_automatic_backups(organizations: Iterable[OrganizationContext]) -> list[Path]:
    created: list[Path] = []
    for organization in organizations:
        snapshot = ensure_automatic_backup(organization)
        if snapshot:
            created.append(snapshot)
    return created


def resolve_backup_archive_path(organization: OrganizationContext, backup_name: str) -> Path:
    backup_dir = get_backup_directory(organization).resolve()
    candidate = (backup_dir / backup_name).resolve()
    if candidate.parent != backup_dir or not candidate.exists() or candidate.suffix.lower() != ".zip":
        raise ValueError("Backup archive was not found.")
    return candidate


def restore_organization_backup(organization: OrganizationContext, backup_name: str) -> Path:
    archive_path = resolve_backup_archive_path(organization, backup_name)
    pre_restore_archive = create_organization_backup(
        organization,
        reason=BACKUP_REASON_PRE_RESTORE,
        note=f"Created automatically before restoring {archive_path.name}.",
    )

    backup_dir = get_backup_directory(organization)
    temp_dir = backup_dir / f"_restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(temp_dir)

        database_snapshot = temp_dir / "database" / "attendance.db"
        if not database_snapshot.exists():
            raise ValueError("Backup archive is missing the database snapshot.")

        _clear_tenant_media(organization, keep_database=True)
        organization.database_path.parent.mkdir(parents=True, exist_ok=True)
        _restore_database_snapshot(database_snapshot, organization.database_path)

        instance_root = temp_dir / "instance"
        for directory_name in TENANT_DIRECTORY_NAMES:
            source_dir = instance_root / directory_name
            if source_dir.exists():
                shutil.copytree(source_dir, organization.instance_dir / directory_name, dirs_exist_ok=True)

        mock_store_source = instance_root / "mock_fingerprint_store.json"
        if mock_store_source.exists():
            organization.mock_store_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(mock_store_source, organization.mock_store_path)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    init_db(organization.database_path)
    return pre_restore_archive


def _write_tenant_media(archive: zipfile.ZipFile, organization: OrganizationContext) -> None:
    for directory_name in TENANT_DIRECTORY_NAMES:
        source_dir = organization.instance_dir / directory_name
        if not source_dir.exists():
            continue
        for file_path in source_dir.rglob("*"):
            if not file_path.is_file():
                continue
            archive.write(
                file_path,
                (Path("instance") / directory_name / file_path.relative_to(source_dir)).as_posix(),
            )

    if organization.mock_store_path.exists():
        archive.write(organization.mock_store_path, (Path("instance") / TENANT_FILE_NAMES[0]).as_posix())


def _clear_tenant_media(organization: OrganizationContext, *, keep_database: bool = False) -> None:
    if not keep_database and organization.database_path.exists():
        organization.database_path.unlink()

    for directory_name in TENANT_DIRECTORY_NAMES:
        target_dir = organization.instance_dir / directory_name
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

    if organization.mock_store_path.exists():
        organization.mock_store_path.unlink()


def _snapshot_database(source_path: Path, destination_path: Path) -> None:
    source_db = sqlite3.connect(str(source_path))
    source_db.row_factory = sqlite3.Row
    destination_db = sqlite3.connect(str(destination_path))
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
        source_db.close()


def _restore_database_snapshot(source_path: Path, destination_path: Path) -> None:
    source_db = sqlite3.connect(str(source_path))
    destination_db = sqlite3.connect(str(destination_path))
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
        source_db.close()


def _read_manifest(archive_path: Path) -> dict[str, str]:
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            with archive.open(BACKUP_MANIFEST_NAME, "r") as manifest_file:
                payload = manifest_file.read().decode("utf-8")
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return {}


def _parse_manifest_datetime(value: str) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _infer_reason_from_name(name: str) -> str:
    lowered = name.lower()
    if "-automatic-backup-" in lowered:
        return BACKUP_REASON_AUTOMATIC
    if "-pre-restore-backup-" in lowered:
        return BACKUP_REASON_PRE_RESTORE
    return BACKUP_REASON_MANUAL


def _prune_old_archives(backup_dir: Path, *, keep: int) -> None:
    archives = sorted(backup_dir.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
    for extra_path in archives[keep:]:
        extra_path.unlink(missing_ok=True)


def _slugify_reason(reason: str) -> str:
    return (
        str(reason or BACKUP_REASON_MANUAL)
        .strip()
        .lower()
        .replace(" ", "-")
        .replace("_", "-")
    )


def _reason_label(reason: str) -> str:
    mapping = {
        BACKUP_REASON_MANUAL: "Manual Backup",
        BACKUP_REASON_AUTOMATIC: "Automatic Backup",
        BACKUP_REASON_PRE_RESTORE: "Pre-Restore Safety Backup",
    }
    return mapping.get(str(reason).strip().lower(), "Backup Snapshot")


def _humanize_bytes(size_bytes: int) -> str:
    value = float(size_bytes)
    units = ["B", "KB", "MB", "GB"]
    unit_index = 0
    while value >= 1024 and unit_index < len(units) - 1:
        value /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(value)} {units[unit_index]}"
    return f"{value:.1f} {units[unit_index]}"
