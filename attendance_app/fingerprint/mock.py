from __future__ import annotations

from pathlib import Path
import json
import uuid

from .base import EnrollmentResult, FingerprintProvider, MatchResult


class MockFingerprintProvider(FingerprintProvider):
    name = "mock"

    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def enroll(self, staff_code: str) -> EnrollmentResult:
        payload = self._read_store()
        token = f"MOCK-{staff_code.upper()}-{uuid.uuid4().hex[:8]}"
        payload[token] = {"staff_code": staff_code}
        self._write_store(payload)
        return EnrollmentResult(
            template_ref=token,
            quality_score=100,
            message="Mock fingerprint assigned successfully.",
            raw_payload={"staff_code": staff_code},
        )

    def identify(self, hint: str | None = None) -> MatchResult | None:
        payload = self._read_store()
        if not hint:
            raise RuntimeError("Mock mode requires selecting a staff member to simulate a scan.")
        if hint not in payload:
            if not hint.startswith("MOCK-"):
                return None
            return MatchResult(
                template_ref=hint,
                confidence=100,
                message="Mock scan matched from a database-linked token.",
                raw_payload={},
            )
        return MatchResult(
            template_ref=hint,
            confidence=100,
            message="Mock scan matched successfully.",
            raw_payload=payload[hint],
        )

    def delete(self, template_ref: str) -> None:
        payload = self._read_store()
        payload.pop(template_ref, None)
        self._write_store(payload)

    def healthcheck(self) -> dict[str, object]:
        payload = self._read_store()
        return {
            "backend": self.name,
            "status": "ready",
            "templates": len(payload),
            "details": "Demo mode. Select a staff member on the kiosk page to simulate a fingerprint.",
        }

    def _read_store(self) -> dict[str, dict[str, str]]:
        if not self.store_path.exists():
            return {}
        return json.loads(self.store_path.read_text(encoding="utf-8"))

    def _write_store(self, payload: dict[str, dict[str, str]]) -> None:
        self.store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
