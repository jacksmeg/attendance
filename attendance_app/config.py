from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
import os


@dataclass(slots=True)
class AppConfig:
    app_name: str
    software_version: str
    copyright_notice: str
    secret_key: str
    web_push_contact_email: str
    web_push_vapid_public_key: str
    web_push_vapid_private_key: str
    shift_alert_runner_enabled: bool
    shift_alert_poll_seconds: int
    admin_username: str
    admin_password: str
    database_path: Path
    instance_dir: Path
    platform_registry_path: Path
    default_organization_slug: str
    mock_store_path: Path
    fingerprint_backend: str
    fingerprint_port: str
    fingerprint_baudrate: int
    fingerprint_address: int
    fingerprint_password: int
    fingerprint_timeout: int
    fingerprint_enroll_timeout: int
    fingerprint_bridge_url: str
    fingerprint_morpho_sdk_dir: Path
    fingerprint_morpho_bridge_script: Path
    fingerprint_morpho_device_serial: str
    fingerprint_morpho_username: str
    fingerprint_morpho_password: str
    fingerprint_morpho_finger: str
    fingerprint_morpho_threshold: str
    fingerprint_powershell_path: str
    host: str
    port: int
    debug: bool


def load_config(
    project_root: Path,
    overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    instance_dir = Path(
        os.getenv("ATTENDANCE_INSTANCE_DIR", str(project_root / "instance"))
    )
    settings = AppConfig(
        app_name=os.getenv("ATTENDANCE_APP_NAME", "JHIMS ATTENDANCE SYSTEM"),
        software_version=os.getenv("ATTENDANCE_SOFTWARE_VERSION", "Version 1.0.0"),
        copyright_notice=os.getenv(
            "ATTENDANCE_COPYRIGHT_NOTICE",
            "All rights reserved to Jackstudios",
        ),
        secret_key=os.getenv("ATTENDANCE_SECRET_KEY", "change-me-before-production"),
        web_push_contact_email=os.getenv(
            "ATTENDANCE_WEB_PUSH_CONTACT_EMAIL",
            "alerts@jhimssoftware.com",
        ),
        web_push_vapid_public_key=os.getenv("ATTENDANCE_WEB_PUSH_VAPID_PUBLIC_KEY", ""),
        web_push_vapid_private_key=os.getenv("ATTENDANCE_WEB_PUSH_VAPID_PRIVATE_KEY", ""),
        shift_alert_runner_enabled=os.getenv(
            "ATTENDANCE_SHIFT_ALERT_RUNNER_ENABLED",
            "true" if os.getenv("RENDER") else "false",
        ).lower()
        == "true",
        shift_alert_poll_seconds=int(
            os.getenv("ATTENDANCE_SHIFT_ALERT_POLL_SECONDS", "60")
        ),
        admin_username=os.getenv("ATTENDANCE_ADMIN_USER", "admin"),
        admin_password=os.getenv("ATTENDANCE_ADMIN_PASSWORD", "admin123"),
        database_path=Path(
            os.getenv("ATTENDANCE_DB_PATH", str(instance_dir / "attendance.db"))
        ),
        instance_dir=instance_dir,
        platform_registry_path=Path(
            os.getenv(
                "ATTENDANCE_PLATFORM_REGISTRY_PATH",
                str(instance_dir / "platform_registry.db"),
            )
        ),
        default_organization_slug=os.getenv(
            "ATTENDANCE_DEFAULT_ORGANIZATION_SLUG",
            "default",
        ),
        mock_store_path=Path(
            os.getenv(
                "ATTENDANCE_MOCK_STORE_PATH",
                str(instance_dir / "mock_fingerprint_store.json"),
            )
        ),
        fingerprint_backend=os.getenv("ATTENDANCE_FINGERPRINT_BACKEND", "mock").lower(),
        fingerprint_port=os.getenv("ATTENDANCE_FINGERPRINT_PORT", "COM3"),
        fingerprint_baudrate=int(os.getenv("ATTENDANCE_FINGERPRINT_BAUDRATE", "57600")),
        fingerprint_address=int(os.getenv("ATTENDANCE_FINGERPRINT_ADDRESS", "0xFFFFFFFF"), 0),
        fingerprint_password=int(
            os.getenv("ATTENDANCE_FINGERPRINT_PASSWORD", "0x00000000"),
            0,
        ),
        fingerprint_timeout=int(os.getenv("ATTENDANCE_FINGERPRINT_TIMEOUT", "12")),
        fingerprint_enroll_timeout=int(
            os.getenv("ATTENDANCE_FINGERPRINT_ENROLL_TIMEOUT", "180")
        ),
        fingerprint_bridge_url=os.getenv(
            "ATTENDANCE_FINGERPRINT_BRIDGE_URL",
            "http://127.0.0.1:9101",
        ),
        fingerprint_morpho_sdk_dir=Path(
            os.getenv(
                "ATTENDANCE_FINGERPRINT_MORPHO_SDK_DIR",
                r"C:\Program Files\Morpho\MorphoManager\Client",
            )
        ),
        fingerprint_morpho_bridge_script=Path(
            os.getenv(
                "ATTENDANCE_FINGERPRINT_MORPHO_BRIDGE_SCRIPT",
                str(project_root / "tools" / "morphosmart_bridge.ps1"),
            )
        ),
        fingerprint_morpho_device_serial=os.getenv(
            "ATTENDANCE_FINGERPRINT_MORPHO_DEVICE_SERIAL",
            "",
        ),
        fingerprint_morpho_username=os.getenv(
            "ATTENDANCE_FINGERPRINT_MORPHO_USERNAME",
            "",
        ),
        fingerprint_morpho_password=os.getenv(
            "ATTENDANCE_FINGERPRINT_MORPHO_PASSWORD",
            "",
        ),
        fingerprint_morpho_finger=os.getenv(
            "ATTENDANCE_FINGERPRINT_MORPHO_FINGER",
            "RightIndex",
        ),
        fingerprint_morpho_threshold=os.getenv(
            "ATTENDANCE_FINGERPRINT_MORPHO_THRESHOLD",
            "FAR_5",
        ),
        fingerprint_powershell_path=os.getenv(
            "ATTENDANCE_POWERSHELL_PATH",
            "powershell.exe",
        ),
        host=os.getenv(
            "ATTENDANCE_HOST",
            "0.0.0.0" if os.getenv("RENDER") else "127.0.0.1",
        ),
        port=int(os.getenv("PORT", os.getenv("ATTENDANCE_PORT", "5000"))),
        debug=os.getenv(
            "ATTENDANCE_DEBUG",
            "false" if os.getenv("RENDER") else "true",
        ).lower() == "true",
    )

    if overrides:
        for key, value in overrides.items():
            if hasattr(settings, key):
                setattr(settings, key, value)

    settings.database_path = Path(settings.database_path).expanduser().resolve()
    settings.instance_dir = Path(settings.instance_dir).expanduser().resolve()
    settings.platform_registry_path = (
        Path(settings.platform_registry_path).expanduser().resolve()
    )
    settings.mock_store_path = Path(settings.mock_store_path).expanduser().resolve()
    settings.fingerprint_morpho_sdk_dir = (
        Path(settings.fingerprint_morpho_sdk_dir).expanduser().resolve()
    )
    settings.fingerprint_morpho_bridge_script = (
        Path(settings.fingerprint_morpho_bridge_script).expanduser().resolve()
    )
    return settings
