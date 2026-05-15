from __future__ import annotations

from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import json

from .base import EnrollmentResult, FingerprintProvider, MatchResult


class HttpBridgeFingerprintProvider(FingerprintProvider):
    name = "http_bridge"

    def __init__(self, base_url: str, timeout: int = 12):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout

    def enroll(self, staff_code: str) -> EnrollmentResult:
        payload = self._post("enroll", {"staff_code": staff_code})
        template_ref = payload.get("template_ref")
        if not template_ref:
            raise RuntimeError("Bridge did not return a template reference during enrollment.")
        return EnrollmentResult(
            template_ref=str(template_ref),
            quality_score=payload.get("quality_score"),
            message=payload.get("message", "Enrollment completed through the bridge."),
            raw_payload=payload,
        )

    def identify(self, hint: str | None = None) -> MatchResult | None:
        payload = self._post("identify", {"hint": hint})
        if not payload.get("matched"):
            return None
        template_ref = payload.get("template_ref")
        if not template_ref:
            raise RuntimeError("Bridge reported a match without a template reference.")
        return MatchResult(
            template_ref=str(template_ref),
            confidence=payload.get("confidence"),
            message=payload.get("message", "Fingerprint identified successfully."),
            raw_payload=payload,
        )

    def delete(self, template_ref: str) -> None:
        self._post("delete", {"template_ref": template_ref})

    def healthcheck(self) -> dict[str, object]:
        try:
            data = self._get("health")
        except RuntimeError as exc:
            return {
                "backend": self.name,
                "status": "error",
                "details": str(exc),
            }
        data.setdefault("backend", self.name)
        data.setdefault("status", "ready")
        return data

    def _get(self, path: str) -> dict[str, object]:
        url = urljoin(self.base_url, path)
        request = Request(url, method="GET")
        return self._open_json(request)

    def _post(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        url = urljoin(self.base_url, path)
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._open_json(request)

    def _open_json(self, request: Request) -> dict[str, object]:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(
                f"Fingerprint bridge returned HTTP {exc.code}. Check the bridge service."
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"Fingerprint bridge is unreachable at {self.base_url}. Start the bridge service first."
            ) from exc
