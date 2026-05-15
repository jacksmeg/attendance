from __future__ import annotations

import time

from .base import EnrollmentResult, FingerprintProvider, MatchResult


class PyFingerprintProvider(FingerprintProvider):
    name = "pyfingerprint"

    def __init__(
        self,
        port: str,
        baudrate: int,
        address: int,
        password: int,
        timeout: int = 12,
    ):
        self.port = port
        self.baudrate = baudrate
        self.address = address
        self.password = password
        self.timeout = timeout

    def enroll(self, staff_code: str) -> EnrollmentResult:
        sensor = self._load_sensor()
        self._wait_for_finger(sensor, "Place the finger on the sensor for the first scan.")
        sensor.convertImage(0x01)
        position, _ = sensor.searchTemplate()
        if position >= 0:
            raise RuntimeError(
                f"This fingerprint already exists on the sensor at slot {position}."
            )

        self._wait_for_removal(sensor)
        self._wait_for_finger(sensor, "Place the same finger again for confirmation.")
        sensor.convertImage(0x02)

        if sensor.compareCharacteristics() == 0:
            raise RuntimeError("The two fingerprint scans did not match. Please try again.")

        sensor.createTemplate()
        position = sensor.storeTemplate()
        return EnrollmentResult(
            template_ref=str(position),
            quality_score=100,
            message=(
                f"Fingerprint enrolled for {staff_code}. Sensor storage slot {position} is now linked."
            ),
            raw_payload={"sensor_slot": position},
        )

    def identify(self, hint: str | None = None) -> MatchResult | None:
        del hint
        sensor = self._load_sensor()
        self._wait_for_finger(sensor, "Place the finger on the sensor to identify the staff member.")
        sensor.convertImage(0x01)
        position, accuracy = sensor.searchTemplate()
        if position < 0:
            return None
        return MatchResult(
            template_ref=str(position),
            confidence=accuracy,
            message="Fingerprint matched successfully.",
            raw_payload={"sensor_slot": position, "accuracy": accuracy},
        )

    def delete(self, template_ref: str) -> None:
        sensor = self._load_sensor()
        sensor.deleteTemplate(int(template_ref))

    def healthcheck(self) -> dict[str, object]:
        try:
            sensor = self._load_sensor()
            template_count = sensor.getTemplateCount()
            storage_capacity = sensor.getStorageCapacity()
        except RuntimeError as exc:
            return {
                "backend": self.name,
                "status": "error",
                "details": str(exc),
            }
        return {
            "backend": self.name,
            "status": "ready",
            "templates": template_count,
            "capacity": storage_capacity,
            "details": f"Connected on {self.port} at {self.baudrate} baud.",
        }

    def _load_sensor(self):
        try:
            from pyfingerprint.pyfingerprint import PyFingerprint
        except ImportError as exc:
            raise RuntimeError(
                "pyfingerprint is not installed. Run 'pip install pyfingerprint' for compatible serial sensors."
            ) from exc

        try:
            sensor = PyFingerprint(self.port, self.baudrate, self.address, self.password)
        except Exception as exc:
            raise RuntimeError(
                f"Could not open the fingerprint sensor on {self.port}. Check the COM port and USB driver."
            ) from exc

        try:
            if not sensor.verifyPassword():
                raise RuntimeError("Fingerprint sensor password verification failed.")
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(
                "Fingerprint sensor responded unexpectedly. Check power, wiring, and sensor password."
            ) from exc
        return sensor

    def _wait_for_finger(self, sensor, message: str) -> None:
        started = time.monotonic()
        while time.monotonic() - started < self.timeout:
            if sensor.readImage():
                return
            time.sleep(0.25)
        raise RuntimeError(f"{message} Capture timed out after {self.timeout} seconds.")

    def _wait_for_removal(self, sensor) -> None:
        started = time.monotonic()
        while time.monotonic() - started < self.timeout:
            if not sensor.readImage():
                return
            time.sleep(0.2)
        raise RuntimeError("Please remove the finger from the sensor and try again.")
