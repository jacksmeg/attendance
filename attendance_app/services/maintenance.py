from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sqlite3

from attendance_app.db import get_db
from attendance_app.db import init_db
from attendance_app.services.settings import ADMIN_PASSWORD_HASH_KEY, ADMIN_USERNAME_KEY
from attendance_app.services.tenancy import OrganizationContext


def reset_system_data(instance_dir: Path, database_path: Path) -> Path | None:
    backup_path: Path | None = None
    if database_path.exists():
        backup_dir = instance_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"attendance-pre-reset-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        shutil.copy2(database_path, backup_path)

    db = get_db()
    for table_name in ("staff_selfie_audits", "attendance_events", "fingerprint_templates", "staff"):
        db.execute(f"DELETE FROM {table_name}")
    db.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('staff_selfie_audits', 'attendance_events', 'fingerprint_templates', 'staff')"
    )
    db.commit()

    _clear_directory(instance_dir / "staff_photos")
    _clear_directory(instance_dir / "staff_selfie_audits")
    _clear_directory(instance_dir / "enrollment_sessions")

    mock_store = instance_dir / "mock_fingerprint_store.json"
    if mock_store.exists():
        mock_store.unlink()

    return backup_path


def _clear_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def reset_organization_workspace(
    organization: OrganizationContext,
    *,
    fallback_admin_username: str = "admin",
) -> None:
    admin_security = _read_preserved_admin_security(
        organization.database_path,
        fallback_admin_username=fallback_admin_username,
    )

    if organization.database_path.exists():
        organization.database_path.unlink(missing_ok=True)

    for directory_name in (
        "staff_photos",
        "staff_selfie_audits",
        "enrollment_sessions",
        "system_branding",
    ):
        _clear_directory(organization.instance_dir / directory_name)

    if organization.mock_store_path.exists():
        organization.mock_store_path.unlink(missing_ok=True)

    init_db(organization.database_path)
    _restore_admin_security(organization.database_path, admin_security)


def _read_preserved_admin_security(
    database_path: Path,
    *,
    fallback_admin_username: str,
) -> dict[str, str]:
    if not Path(database_path).exists():
        return {"username": fallback_admin_username, "password_hash": ""}

    db = sqlite3.connect(database_path)
    db.row_factory = sqlite3.Row
    try:
        try:
            rows = db.execute(
                "SELECT key, value FROM app_settings WHERE key IN (?, ?)",
                (ADMIN_USERNAME_KEY, ADMIN_PASSWORD_HASH_KEY),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
    finally:
        db.close()

    payload = {str(row["key"]): str(row["value"] or "") for row in rows}
    return {
        "username": payload.get(ADMIN_USERNAME_KEY) or fallback_admin_username,
        "password_hash": payload.get(ADMIN_PASSWORD_HASH_KEY, ""),
    }


def _restore_admin_security(database_path: Path, admin_security: dict[str, str]) -> None:
    db = sqlite3.connect(database_path)
    try:
        timestamp = datetime.now().isoformat(timespec="seconds")
        db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (ADMIN_USERNAME_KEY, admin_security["username"], timestamp),
        )
        if admin_security.get("password_hash"):
            db.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (ADMIN_PASSWORD_HASH_KEY, admin_security["password_hash"], timestamp),
            )
        db.commit()
    finally:
        db.close()
