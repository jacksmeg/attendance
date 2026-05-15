from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import serial
import serial.tools.list_ports


TARGET_VID = 0x079B
TARGET_PID = 0x0024
VENDOR_HINTS = ("Morpho", "IDEMIA", "Safran", "Sagem")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check whether a MorphoSmart MSO300/MSO301-style sensor is visible and usable on Windows."
    )
    parser.add_argument(
        "--port",
        help="Specific port to test, for example COM6. If omitted, the first matching MSO device is used.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=57600,
        help="Baud rate for the serial open test. Default: 57600",
    )
    args = parser.parse_args()

    ports = list(serial.tools.list_ports.comports())
    matches = [port for port in ports if port.vid == TARGET_VID and port.pid == TARGET_PID]

    print("MorphoSmart MSO setup check")
    print("===========================")

    if not matches:
        print("No serial device matching VID 079B and PID 0024 was found.")
        print("If the sensor is connected, unplug and replug it, then rerun this tool.")
        return 1

    print("\nDetected matching device(s):")
    for port in matches:
        print(f"- Port: {port.device}")
        print(f"  Description: {port.description}")
        print(f"  HWID: {port.hwid}")
        if port.serial_number:
            print(f"  Serial: {port.serial_number}")
        if port.manufacturer:
            print(f"  Manufacturer: {port.manufacturer}")

    selected = pick_port(matches, args.port)
    if selected is None:
        print(f"\nRequested port {args.port} was not found among the matching devices.")
        return 2

    print(f"\nUsing port: {selected.device}")
    if sys.platform.startswith("win"):
        instance_id = extract_instance_id(selected.hwid) or find_instance_id_from_pnputil()
        if instance_id:
            print_windows_pnp_details(instance_id)
        else:
            print("Could not extract a Windows instance ID from the port HWID.")

    open_ok = test_serial_open(selected.device, args.baud)
    print_installed_vendor_folders()

    if open_ok:
        print("\nResult: The sensor is present and the COM port opens successfully.")
        print("That usually means the USB side is installed correctly, and the remaining work is SDK or protocol integration.")
        return 0

    print("\nResult: The sensor was found, but the COM port open test failed.")
    return 3


def pick_port(matches: list[serial.tools.list_ports_common.ListPortInfo], requested: str | None):
    if not requested:
        return matches[0]

    requested_upper = requested.upper()
    for port in matches:
        if port.device.upper() == requested_upper:
            return port
    return None


def extract_instance_id(hwid: str) -> str:
    upper_hwid = hwid.upper()
    marker = "USB VID:PID="
    if marker not in upper_hwid:
        return ""

    serial_token = upper_hwid.split("SER=", 1)[1].split(" ", 1)[0] if "SER=" in upper_hwid else ""
    if not serial_token:
        return ""

    serial_token = serial_token.strip("\\")
    return f"USB\\VID_{TARGET_VID:04X}&PID_{TARGET_PID:04X}\\{serial_token}"


def print_windows_pnp_details(instance_id: str) -> None:
    print(f"\nWindows device details for {instance_id}:")
    try:
        result = subprocess.run(
            ["pnputil", "/enum-devices", "/instanceid", instance_id],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print(f"  Could not run pnputil: {exc}")
        return

    if result.returncode != 0:
        message = result.stdout.strip() or result.stderr.strip() or f"exit code {result.returncode}"
        print(f"  pnputil failed: {message}")
        return

    interesting = (
        "Device Description:",
        "Class Name:",
        "Manufacturer Name:",
        "Status:",
        "Driver Name:",
    )
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if any(line.startswith(prefix) for prefix in interesting):
            print(f"  {line}")


def find_instance_id_from_pnputil() -> str:
    try:
        result = subprocess.run(
            ["pnputil", "/enum-devices", "/connected"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""

    if result.returncode != 0:
        return ""

    target = f"VID_{TARGET_VID:04X}&PID_{TARGET_PID:04X}"
    lines = result.stdout.splitlines()
    for index, line in enumerate(lines):
        if target not in line.upper():
            continue

        for backtrack in range(index, max(-1, index - 8), -1):
            candidate = lines[backtrack].strip()
            if candidate.startswith("Instance ID:"):
                return candidate.split(":", 1)[1].strip()
    return ""


def test_serial_open(port_name: str, baudrate: int) -> bool:
    print(f"\nSerial open test on {port_name} at {baudrate} baud:")
    try:
        with serial.Serial(port_name, baudrate=baudrate, timeout=0.2, write_timeout=1):
            print("  Port opened successfully.")
            return True
    except serial.SerialException as exc:
        print(f"  Serial open failed: {exc}")
        return False


def print_installed_vendor_folders() -> None:
    roots = [Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")]
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if child.is_dir() and any(hint.lower() in child.name.lower() for hint in VENDOR_HINTS):
                found.append(child)

    print("\nInstalled vendor folders:")
    if found:
        for path in found:
            print(f"  {path}")
    else:
        print("  No Morpho/IDEMIA/Safran/Sagem program folders were found in Program Files.")


if __name__ == "__main__":
    sys.exit(main())
