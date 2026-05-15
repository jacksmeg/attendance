from __future__ import annotations

import argparse
import sys
import time

import serial
import serial.tools.list_ports


KNOWN_DEVICES: dict[tuple[int, int], str] = {
    (0x079B, 0x0024): "IDEMIA / Sagem MorphoSmart MSO300/MSO301 family",
    (0x06CB, 0x00A1): "Synaptics Windows Biometric reader",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect serial USB devices and probe a fingerprint sensor port safely."
    )
    parser.add_argument(
        "--port",
        help="Specific serial port to probe, for example COM6.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=57600,
        help="Baud rate to use when probing the port. Default: 57600",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=5.0,
        help="How long to passively listen for bytes after opening the port. Default: 5 seconds",
    )
    parser.add_argument(
        "--send-hex",
        default="",
        help="Optional hex payload to send after opening the port, for advanced protocol testing.",
    )
    args = parser.parse_args()

    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("No serial ports were found.")
        return 1

    print("Detected serial ports:")
    selected = None
    for port in ports:
        label = known_label(port.vid, port.pid)
        print(
            f"  - {port.device}: {port.description} | hwid={port.hwid}"
            + (f" | recognized={label}" if label else "")
        )
        if args.port and port.device.upper() == args.port.upper():
            selected = port

    if not args.port:
        print("\nTip: rerun with --port COM6 to probe a specific sensor.")
        return 0

    if selected is None:
        print(f"\nPort {args.port} was not found.")
        return 2

    print(f"\nOpening {selected.device} at {args.baud} baud...")
    payload = normalize_hex(args.send_hex)

    try:
        with serial.Serial(selected.device, baudrate=args.baud, timeout=0.2, write_timeout=1) as ser:
            print("Port opened successfully.")
            if payload:
                print(f"Sending {len(payload)} byte(s): {payload.hex(' ').upper()}")
                ser.write(payload)
                ser.flush()

            deadline = time.monotonic() + args.duration
            chunks: list[bytes] = []
            while time.monotonic() < deadline:
                chunk = ser.read(ser.in_waiting or 1)
                if chunk:
                    chunks.append(chunk)
                    print(f"RX {len(chunk)} byte(s): {chunk.hex(' ').upper()}")
                time.sleep(0.05)

            if not chunks:
                print("No bytes were received during the probe window.")
                print("That is normal for sensors that wait for a vendor command before scanning.")
            else:
                joined = b"".join(chunks)
                print(f"Combined payload: {joined.hex(' ').upper()}")
    except serial.SerialException as exc:
        print(f"Serial probe failed: {exc}")
        return 3

    return 0


def known_label(vid: int | None, pid: int | None) -> str:
    if vid is None or pid is None:
        return ""
    return KNOWN_DEVICES.get((vid, pid), "")


def normalize_hex(hex_text: str) -> bytes:
    clean = "".join(hex_text.split())
    if not clean:
        return b""
    if len(clean) % 2:
        raise SystemExit("Hex payload must contain an even number of characters.")
    try:
        return bytes.fromhex(clean)
    except ValueError as exc:
        raise SystemExit(f"Invalid hex payload: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
