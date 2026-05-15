from __future__ import annotations

from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import json

from attendance_app.fingerprint.morphosmart import (
    _HEALTH_CACHE,
    MorphoSmartFingerprintProvider,
)


class MorphoSmartFingerprintProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        _HEALTH_CACHE.clear()
        self.provider = MorphoSmartFingerprintProvider(
            sdk_dir=Path("tests"),
            script_path=Path("tests") / "bridge.ps1",
            powershell_path="powershell.exe",
            timeout=5,
            enroll_timeout=14,
            device_serial="",
            manager_username="operator1",
            manager_password="secret-pass",
            finger="RightIndex",
            threshold="FAR_5",
        )

    @patch("attendance_app.fingerprint.morphosmart.Path.exists", return_value=True)
    @patch("attendance_app.fingerprint.morphosmart.subprocess.run")
    def test_enroll_parses_successful_bridge_response(self, run_mock, _exists_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"template_ref":"8d7d4e8d-dcb8-4c79-bfcb-1d24f1d7fd4c","quality_score":91,"message":"ok"}\n',
            stderr="",
        )

        enrollment = self.provider.enroll("EMP-200")

        self.assertEqual(enrollment.template_ref, "8d7d4e8d-dcb8-4c79-bfcb-1d24f1d7fd4c")
        self.assertEqual(enrollment.quality_score, 91)
        self.assertEqual(enrollment.message, "ok")
        run_mock.assert_called_once()
        command = run_mock.call_args.args[0]
        timeout_index = command.index("-TimeoutSeconds")
        self.assertEqual(command[timeout_index + 1], "14")

    @patch("attendance_app.fingerprint.morphosmart.Path.exists", return_value=True)
    @patch("attendance_app.fingerprint.morphosmart.subprocess.run")
    def test_identify_returns_none_for_unmatched_scan(self, run_mock, _exists_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"matched":false,"message":"no match"}\n',
            stderr="",
        )

        match = self.provider.identify()

        self.assertIsNone(match)

    @patch("attendance_app.fingerprint.morphosmart.Path.exists", return_value=True)
    @patch("attendance_app.fingerprint.morphosmart.subprocess.run")
    def test_identify_candidates_passes_template_payload_to_bridge(self, run_mock, _exists_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"matched":true,"template_ref":"tpl-1","confidence":88,"message":"ok"}\n',
            stderr="",
        )

        match = self.provider.identify_candidates(
            [
                {
                    "template_ref": "tpl-1",
                    "template_format": "MorphoPKCompV2",
                    "template_data_base64": "YWJj",
                }
            ]
        )

        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.template_ref, "tpl-1")
        command = run_mock.call_args.args[0]
        payload_index = command.index("-PayloadJson")
        payload = json.loads(command[payload_index + 1])
        self.assertEqual(payload["candidates"][0]["template_ref"], "tpl-1")
        self.assertEqual(payload["candidates"][0]["template_format"], "MorphoPKCompV2")

    @patch("attendance_app.fingerprint.morphosmart.Path.exists", return_value=True)
    @patch("attendance_app.fingerprint.morphosmart.subprocess.run")
    def test_healthcheck_returns_error_payload_on_bridge_failure(self, run_mock, _exists_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout='{"backend":"morphosmart","status":"error","error":"Failed to connect to device"}\n',
            stderr="",
        )

        health = self.provider.healthcheck()

        self.assertEqual(health["backend"], "morphosmart")
        self.assertEqual(health["status"], "error")
        self.assertIn("Failed to connect to device", health["details"])

    @patch("attendance_app.fingerprint.morphosmart.Path.exists", return_value=True)
    @patch("attendance_app.fingerprint.morphosmart.subprocess.run")
    def test_healthcheck_passes_manager_credentials_to_bridge(self, run_mock, _exists_mock) -> None:
        run_mock.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"backend":"morphosmart","status":"needs_credentials"}\n',
            stderr="",
        )

        self.provider.healthcheck()

        command = run_mock.call_args.args[0]
        self.assertIn("-ManagerUsername", command)
        self.assertIn("operator1", command)
        self.assertIn("-ManagerPassword", command)
        self.assertIn("secret-pass", command)

    def test_healthcheck_normalizes_base_not_found_as_no_enrolled_users(self) -> None:
        payload = self.provider._normalize_health_payload(
            {
                "backend": "morphosmart",
                "status": "ready",
                "details": "Morpho SDK reached the scanner successfully.",
                "direct_sdk": {
                    "status": "ready",
                    "details": "Morpho SDK reached the scanner successfully.",
                    "user_monikers_error": "Base not found",
                },
            }
        )

        self.assertEqual(payload["status"], "no_enrolled_users")
        self.assertIn("no enrolled fingerprints", payload["details"])
        self.assertEqual(payload["direct_sdk"]["user_monikers_error"], "")


if __name__ == "__main__":
    unittest.main()
