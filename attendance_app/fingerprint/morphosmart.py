from __future__ import annotations

from pathlib import Path
import copy
import json
import subprocess
import time

from .base import EnrollmentResult, FingerprintProvider, MatchResult


_HEALTH_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, object]]] = {}
_HEALTH_TTL_SECONDS = 12.0


class MorphoSmartFingerprintProvider(FingerprintProvider):
    name = "morphosmart"

    def __init__(
        self,
        sdk_dir: Path,
        script_path: Path,
        powershell_path: str = "powershell.exe",
        timeout: int = 20,
        enroll_timeout: int | None = None,
        device_serial: str = "",
        manager_username: str = "",
        manager_password: str = "",
        finger: str = "RightIndex",
        threshold: str = "FAR_5",
    ) -> None:
        self.sdk_dir = Path(sdk_dir)
        self.script_path = Path(script_path)
        self.powershell_path = powershell_path
        self.timeout = timeout
        self.enroll_timeout = enroll_timeout if enroll_timeout is not None else timeout
        self.device_serial = device_serial.strip()
        self.manager_username = manager_username.strip()
        self.manager_password = manager_password
        self.finger = finger
        self.threshold = threshold

    def enroll(self, staff_code: str) -> EnrollmentResult:
        payload = self._invoke(
            "enroll",
            {"staff_code": staff_code},
            timeout=self.enroll_timeout,
        )
        template_ref = payload.get("template_ref")
        if not template_ref:
            raise RuntimeError("MorphoSmart enrollment did not return a template reference.")
        return EnrollmentResult(
            template_ref=str(template_ref),
            quality_score=payload.get("quality_score"),
            message=str(payload.get("message", "MorphoSmart enrollment completed.")),
            raw_payload=payload,
        )

    def enroll_with_progress(
        self,
        staff_code: str,
        progress_path: Path,
        preview_path: Path,
    ) -> EnrollmentResult:
        payload = self._invoke(
            "enroll",
            {"staff_code": staff_code},
            timeout=self.enroll_timeout,
            progress_path=Path(progress_path),
            preview_path=Path(preview_path),
        )
        template_ref = payload.get("template_ref")
        if not template_ref:
            raise RuntimeError("MorphoSmart enrollment did not return a template reference.")
        return EnrollmentResult(
            template_ref=str(template_ref),
            quality_score=payload.get("quality_score"),
            message=str(payload.get("message", "MorphoSmart enrollment completed.")),
            raw_payload=payload,
        )

    def identify(self, hint: str | None = None) -> MatchResult | None:
        payload = self._invoke("identify", {"hint": hint})
        if not payload.get("matched"):
            return None
        template_ref = payload.get("template_ref")
        if not template_ref:
            raise RuntimeError("MorphoSmart identification returned a match without a template reference.")
        return MatchResult(
            template_ref=str(template_ref),
            confidence=payload.get("confidence"),
            message=str(payload.get("message", "MorphoSmart identified a user.")),
            raw_payload=payload,
        )

    def identify_candidates(self, candidates: list[dict[str, object]]) -> MatchResult | None:
        payload = self._invoke("identify", {"candidates": candidates})
        if not payload.get("matched"):
            return None
        template_ref = payload.get("template_ref")
        if not template_ref:
            raise RuntimeError("MorphoSmart identification returned a match without a template reference.")
        return MatchResult(
            template_ref=str(template_ref),
            confidence=payload.get("confidence"),
            message=str(payload.get("message", "MorphoSmart identified a user.")),
            raw_payload=payload,
        )

    def delete(self, template_ref: str) -> None:
        self._invoke("delete", {"template_ref": template_ref})

    def healthcheck(self) -> dict[str, object]:
        cache_key = (
            str(self.sdk_dir),
            self.device_serial,
            self.manager_username,
        )
        cached = _HEALTH_CACHE.get(cache_key)
        now = time.monotonic()
        if cached and now - cached[0] < _HEALTH_TTL_SECONDS:
            return copy.deepcopy(cached[1])

        try:
            payload = self._invoke("health", {})
        except RuntimeError as exc:
            return {
                "backend": self.name,
                "status": "error",
                "details": str(exc),
            }
        payload = self._normalize_health_payload(payload)
        payload.setdefault("backend", self.name)
        _HEALTH_CACHE[cache_key] = (now, copy.deepcopy(payload))
        return payload

    def _invoke(
        self,
        action: str,
        payload: dict[str, object],
        timeout: int | None = None,
        progress_path: Path | None = None,
        preview_path: Path | None = None,
    ) -> dict[str, object]:
        if not self.script_path.exists():
            raise RuntimeError(
                f"Morpho bridge script was not found at {self.script_path}. "
                "Check ATTENDANCE_FINGERPRINT_MORPHO_BRIDGE_SCRIPT."
            )
        if not self.sdk_dir.exists():
            raise RuntimeError(
                f"Morpho SDK directory was not found at {self.sdk_dir}. "
                "Check ATTENDANCE_FINGERPRINT_MORPHO_SDK_DIR."
            )

        effective_timeout = timeout if timeout is not None else self.timeout
        command = [
            self.powershell_path,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-Action",
            action,
            "-SdkDir",
            str(self.sdk_dir),
            "-TimeoutSeconds",
            str(effective_timeout),
            "-Finger",
            self.finger,
            "-Threshold",
            self.threshold,
            "-PayloadJson",
            json.dumps(payload),
        ]
        if self.device_serial:
            command.extend(["-DeviceSerial", self.device_serial])
        if self.manager_username:
            command.extend(["-ManagerUsername", self.manager_username])
        if self.manager_password:
            command.extend(["-ManagerPassword", self.manager_password])
        if progress_path:
            command.extend(["-ProgressJsonPath", str(progress_path)])
        if preview_path:
            command.extend(["-PreviewImagePath", str(preview_path)])

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=effective_timeout + 8,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"PowerShell executable '{self.powershell_path}' could not be started."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"The MorphoSmart bridge timed out after {effective_timeout + 8} seconds."
            ) from exc

        payload_data = self._parse_json_output(completed.stdout)
        if completed.returncode != 0:
            error_message = str(
                payload_data.get("error")
                or payload_data.get("details")
                or completed.stderr.strip()
                or completed.stdout.strip()
                or f"MorphoSmart bridge failed with exit code {completed.returncode}."
            )
            raise RuntimeError(error_message)

        return payload_data

    @staticmethod
    def _parse_json_output(stdout: str) -> dict[str, object]:
        lines = [line.strip() for line in stdout.splitlines() if line.strip()]
        if not lines:
            return {}
        for line in reversed(lines):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        raise RuntimeError("MorphoSmart bridge did not return valid JSON output.")

    @staticmethod
    def _normalize_health_payload(payload: dict[str, object]) -> dict[str, object]:
        normalized = copy.deepcopy(payload)
        direct_sdk = normalized.get("direct_sdk")
        if not isinstance(direct_sdk, dict):
            return normalized

        user_monikers_error = str(direct_sdk.get("user_monikers_error", "") or "")
        if "Base not found" not in user_monikers_error:
            return normalized

        direct_sdk["user_monikers_error"] = ""
        direct_sdk["user_monikers_status"] = "NoBase"
        direct_sdk["enrolled_user_count"] = 0

        if normalized.get("status") == "ready":
            normalized["status"] = "no_enrolled_users"
            normalized["details"] = (
                "Morpho SDK reached the scanner, but the scanner has no enrolled fingerprints yet."
            )

        normalized["user_monikers_error"] = ""
        normalized["user_monikers_status"] = "NoBase"
        normalized["enrolled_user_count"] = 0
        return normalized
