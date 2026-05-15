from __future__ import annotations

from .base import EnrollmentResult, FingerprintProvider, MatchResult


class DisabledFingerprintProvider(FingerprintProvider):
    name = "disabled"

    def enroll(self, staff_code: str) -> EnrollmentResult:
        raise RuntimeError(
            "Fingerprint enrollment is disabled in cloud mode. Use PIN, password, or QR access online."
        )

    def identify(self, hint: str | None = None) -> MatchResult | None:
        raise RuntimeError(
            "Fingerprint scanning is disabled in cloud mode. Use PIN, password, or QR access instead."
        )

    def delete(self, template_ref: str) -> None:
        return None

    def healthcheck(self) -> dict[str, object]:
        return {
            "backend": self.name,
            "status": "remote_access_only",
            "details": (
                "Cloud deployment is live for admin, staff, GPS, QR, and PIN/password access. "
                "Fingerprint hardware remains local unless you add a separate bridge service."
            ),
        }
