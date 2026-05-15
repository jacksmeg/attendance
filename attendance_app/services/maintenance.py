from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

from attendance_app.db import get_db


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
