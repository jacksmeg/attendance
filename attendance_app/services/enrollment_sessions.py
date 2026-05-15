from __future__ import annotations

from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import threading
import uuid

from attendance_app.fingerprint.base import FingerprintProvider
from attendance_app.services.staff import upsert_fingerprint


def start_enrollment_session(
    *,
    instance_dir: Path,
    provider: FingerprintProvider,
    app,
    staff_id: int,
    staff_code: str,
    staff_name: str,
) -> str:
    session_id = uuid.uuid4().hex
    session_dir = _session_dir(instance_dir, session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    status_path = session_dir / "status.json"
    preview_path = session_dir / "preview.png"
    _write_status(
        status_path,
        {
            "session_id": session_id,
            "staff_id": staff_id,
            "staff_code": staff_code,
            "staff_name": staff_name,
            "backend": provider.name,
            "state": "starting",
            "complete": False,
            "message": "Preparing the MorphoSmart scanner for enrollment...",
            "updated_at": _now_iso(),
            "preview_available": False,
        },
    )

    worker = threading.Thread(
        target=_run_enrollment_worker,
        kwargs={
            "app": app,
            "provider": provider,
            "staff_id": staff_id,
            "staff_code": staff_code,
            "staff_name": staff_name,
            "status_path": status_path,
            "preview_path": preview_path,
        },
        daemon=True,
    )
    worker.start()
    return session_id


def read_enrollment_session(instance_dir: Path, session_id: str) -> dict[str, Any] | None:
    status_path = _session_dir(instance_dir, session_id) / "status.json"
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "session_id": session_id,
            "state": "error",
            "complete": True,
            "message": "Enrollment status could not be read.",
            "updated_at": _now_iso(),
            "preview_available": False,
        }


def get_enrollment_preview_path(instance_dir: Path, session_id: str) -> Path:
    return _session_dir(instance_dir, session_id) / "preview.png"


def _run_enrollment_worker(
    *,
    app,
    provider: FingerprintProvider,
    staff_id: int,
    staff_code: str,
    staff_name: str,
    status_path: Path,
    preview_path: Path,
) -> None:
    try:
        if hasattr(provider, "enroll_with_progress"):
            enrollment = provider.enroll_with_progress(
                staff_code=staff_code,
                progress_path=status_path,
                preview_path=preview_path,
            )
        else:
            enrollment = provider.enroll(staff_code)

        with app.app_context():
            upsert_fingerprint(
                staff_id=staff_id,
                adapter=provider.name,
                template_ref=enrollment.template_ref,
                template_format=str(enrollment.raw_payload.get("template_format", "")),
                template_data=_decode_template_data(enrollment.raw_payload.get("template_data_base64")),
                quality_score=enrollment.quality_score,
                notes=enrollment.message,
            )

        _write_status(
            status_path,
            {
                **(read_status_or_default(status_path)),
                "state": "completed",
                "complete": True,
                "success": True,
                "message": f"Fingerprint enrolled for {staff_name}.",
                "template_ref": enrollment.template_ref,
                "quality_score": enrollment.quality_score,
                "updated_at": _now_iso(),
                "preview_available": preview_path.exists(),
            },
        )
    except Exception as exc:
        _write_status(
            status_path,
            {
                **(read_status_or_default(status_path)),
                "state": "error",
                "complete": True,
                "success": False,
                "message": str(exc),
                "updated_at": _now_iso(),
                "preview_available": preview_path.exists(),
            },
        )


def read_status_or_default(status_path: Path) -> dict[str, Any]:
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(status_path: Path, payload: dict[str, Any]) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _session_dir(instance_dir: Path, session_id: str) -> Path:
    return Path(instance_dir) / "enrollment_sessions" / session_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_template_data(value: Any) -> bytes | None:
    if not value:
        return None
    return b64decode(str(value))
