from __future__ import annotations

from .disabled import DisabledFingerprintProvider
from .http_bridge import HttpBridgeFingerprintProvider
from .mock import MockFingerprintProvider
from .morphosmart import MorphoSmartFingerprintProvider
from .pyfingerprint_adapter import PyFingerprintProvider


def build_provider(settings):
    backend = settings.fingerprint_backend.lower()
    if backend in {"disabled", "none", "remote"}:
        return DisabledFingerprintProvider()
    if backend == "mock":
        return MockFingerprintProvider(settings.mock_store_path)
    if backend == "pyfingerprint":
        return PyFingerprintProvider(
            port=settings.fingerprint_port,
            baudrate=settings.fingerprint_baudrate,
            address=settings.fingerprint_address,
            password=settings.fingerprint_password,
            timeout=settings.fingerprint_timeout,
        )
    if backend in {"bridge", "http_bridge"}:
        return HttpBridgeFingerprintProvider(
            base_url=settings.fingerprint_bridge_url,
            timeout=settings.fingerprint_timeout,
        )
    if backend in {"morphosmart", "morpho", "mso300"}:
        return MorphoSmartFingerprintProvider(
            sdk_dir=settings.fingerprint_morpho_sdk_dir,
            script_path=settings.fingerprint_morpho_bridge_script,
            powershell_path=settings.fingerprint_powershell_path,
            timeout=settings.fingerprint_timeout,
            enroll_timeout=settings.fingerprint_enroll_timeout,
            device_serial=settings.fingerprint_morpho_device_serial,
            manager_username=settings.fingerprint_morpho_username,
            manager_password=settings.fingerprint_morpho_password,
            finger=settings.fingerprint_morpho_finger,
            threshold=settings.fingerprint_morpho_threshold,
        )
    raise RuntimeError(
        f"Unsupported fingerprint backend '{settings.fingerprint_backend}'. "
        "Use disabled, mock, pyfingerprint, http_bridge, or morphosmart."
    )
