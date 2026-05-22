# JHIMS ATTENDANCE SYSTEM

This project is a full Python attendance application for organizations that want to record staff attendance with a fingerprint device.

It includes:

- A fingerprint kiosk screen for check-in and check-out
- An admin dashboard for staff management and live attendance overview
- SQLite storage for staff, fingerprints, and attendance logs
- CSV attendance export
- Serial probe tooling for USB fingerprint sensors
- A Windows setup checker for the detected MorphoSmart sensor
- Four fingerprint backends:
  - `mock` for demos and testing
  - `pyfingerprint` for many serial USB fingerprint modules
  - `http_bridge` for vendor SDK devices that need a local bridge service
  - `morphosmart` for the installed IDEMIA / Safran MorphoManager SDK stack

## 1. Quick Start

Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run the app:

```powershell
python app.py
```

Open:

- `http://127.0.0.1:5000/kiosk`
- `http://127.0.0.1:5000/admin/login`
- `http://127.0.0.1:5000/staff/login`

Default admin login:

- Username: `admin`
- Password: `admin123`

Change those before real deployment by setting environment variables.

## 2. Demo Mode

The app starts in `mock` fingerprint mode by default, which lets you test the full workflow immediately without touching your physical device.

Load sample records:

```powershell
python -m flask --app app seed-demo
```

Then use the kiosk page and choose a staff member from the dropdown to simulate a fingerprint scan.

## 3. Environment Variables

You can configure the system with these variables:

```text
ATTENDANCE_SECRET_KEY=change-me
ATTENDANCE_ADMIN_USER=admin
ATTENDANCE_ADMIN_PASSWORD=strong-password
ATTENDANCE_DB_PATH=C:\path\to\attendance.db
ATTENDANCE_FINGERPRINT_BACKEND=mock
ATTENDANCE_FINGERPRINT_PORT=COM3
ATTENDANCE_FINGERPRINT_BAUDRATE=57600
ATTENDANCE_FINGERPRINT_ADDRESS=0xFFFFFFFF
ATTENDANCE_FINGERPRINT_PASSWORD=0x00000000
ATTENDANCE_FINGERPRINT_TIMEOUT=12
ATTENDANCE_FINGERPRINT_ENROLL_TIMEOUT=75
ATTENDANCE_FINGERPRINT_BRIDGE_URL=http://127.0.0.1:9101
ATTENDANCE_FINGERPRINT_MORPHO_SDK_DIR=C:\Program Files\Morpho\MorphoManager\Client
ATTENDANCE_FINGERPRINT_MORPHO_BRIDGE_SCRIPT=C:\path\to\ATTENDANCE SYSTEM\tools\morphosmart_bridge.ps1
ATTENDANCE_FINGERPRINT_MORPHO_DEVICE_SERIAL=
ATTENDANCE_FINGERPRINT_MORPHO_USERNAME=
ATTENDANCE_FINGERPRINT_MORPHO_PASSWORD=
ATTENDANCE_FINGERPRINT_MORPHO_FINGER=RightIndex
ATTENDANCE_FINGERPRINT_MORPHO_THRESHOLD=FAR_5
ATTENDANCE_POWERSHELL_PATH=powershell.exe
ATTENDANCE_HOST=127.0.0.1
ATTENDANCE_PORT=5000
ATTENDANCE_DEBUG=true
```

## 4. Real Fingerprint Device Support

### Option A: `pyfingerprint`

Use this if your fingerprint module is one of the common serial sensors supported by the `pyfingerprint` Python package.

Install:

```powershell
python -m pip install pyfingerprint
```

Set:

```powershell
$env:ATTENDANCE_FINGERPRINT_BACKEND="pyfingerprint"
$env:ATTENDANCE_FINGERPRINT_PORT="COM3"
python app.py
```

What happens:

- Fingerprint enrollment stores the template on the sensor
- The software saves the sensor slot number and links it to a staff record
- Kiosk scans identify a sensor slot and record attendance automatically

### Option B: `http_bridge`

Use this when your hardware only works with a vendor SDK, desktop utility, or network appliance workflow.

Set:

```powershell
$env:ATTENDANCE_FINGERPRINT_BACKEND="http_bridge"
$env:ATTENDANCE_FINGERPRINT_BRIDGE_URL="http://127.0.0.1:9101"
python app.py
```

Expected bridge endpoints:

- `GET /health`
- `POST /enroll`
- `POST /identify`
- `POST /delete`

Expected example responses:

```json
{
  "backend": "vendor-bridge",
  "status": "ready"
}
```

```json
{
  "template_ref": "slot-12",
  "quality_score": 93,
  "message": "Enrollment complete"
}
```

```json
{
  "matched": true,
  "template_ref": "slot-12",
  "confidence": 88,
  "message": "Match found"
}
```

### Option C: `morphosmart`

Use this for the installed `MorphoSmart MSO300 / MSO301` software stack on Windows.

Set:

```powershell
$env:ATTENDANCE_FINGERPRINT_BACKEND="morphosmart"
$env:ATTENDANCE_FINGERPRINT_MORPHO_SDK_DIR="C:\Program Files\Morpho\MorphoManager\Client"
python app.py
```

Or use the included launcher for this exact PC setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\start_mso300.ps1
```

That launcher also sets a longer `ATTENDANCE_FINGERPRINT_ENROLL_TIMEOUT` so staff enrollment has more time than a normal kiosk scan.

Optional tuning:

```powershell
$env:ATTENDANCE_FINGERPRINT_MORPHO_DEVICE_SERIAL="251946674-1644S019535"
$env:ATTENDANCE_FINGERPRINT_MORPHO_FINGER="RightIndex"
$env:ATTENDANCE_FINGERPRINT_MORPHO_THRESHOLD="FAR_5"
```

Optional MorphoManager operator credentials:

```powershell
$env:ATTENDANCE_FINGERPRINT_MORPHO_USERNAME="your-morphomanager-user"
$env:ATTENDANCE_FINGERPRINT_MORPHO_PASSWORD="your-morphomanager-password"
```

Those credentials are now optional for the attendance app's normal scanner path. The `morphosmart` backend prefers the direct `ID1.MorphoSmart` device SDK first, and only uses MorphoManager health data as a secondary diagnostic path.

What this backend does:

- Loads the installed Morpho SDK DLLs from the local MorphoManager client folder
- Uses the included `tools/morphosmart_bridge.ps1` helper as a JSON bridge
- Stores the Morpho user ID as the app `template_ref`
- Uses the scanner itself for capture, identify, and delete operations where the direct SDK path is available
- Reports MorphoManager session readiness and missing credential state through the health check

Enrollment flow:

- Open `Staff`
- Click `Enroll Fingerprint`
- Wait for the enrollment page to open
- Click `Start Enrollment`
- Place the finger on the scanner and hold it still until the page returns

Quick health check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\morphosmart_bridge.ps1 -Action health
```

If Windows policy blocks local SDK inspection on your PC, retry the same command from an Administrator PowerShell window.

### Your currently detected sensor

As verified on `May 12, 2026`, this machine reports an external serial USB device on `COM6` with:

- `VID:PID = 079B:0024`
- Device name: `USB Serial Device (COM6)`
- Windows status: `Started`
- Driver binding: `usbser.inf`

The scanner sticker also identifies it as:

- `Safran Morpho`
- `MODEL: MSO 300`
- `P/N: 251946674`
- `S/N: 1644S019535`

That maps to the `IDEMIA / Sagem MorphoSmart MSO300/MSO301` family.

Important note: the official IDEMIA installation guide says the `MSO300` exposes an integrated USB driver that emulates an `RS232` serial port. On this machine, that is already happening through `COM6`, which means the basic USB connection is working. The remaining blocker is usually the vendor SDK or Morpho communication protocol, not a missing COM-port driver.

For this repo, the recommended path for this exact sensor is now:

1. Use `ATTENDANCE_FINGERPRINT_BACKEND=morphosmart`
2. Let the app call `tools/morphosmart_bridge.ps1`
3. Let that bridge load the installed MorphoManager SDK locally

Current status on this machine as of `May 13, 2026`:

- `MorphoManager Server` and `MSO USB Service` are both running
- The bridge now prepends the Morpho SDK client folder to the process `PATH` before loading the Morpho DLLs
- With that runtime fix in place, the direct `ID1.MorphoSmart` SDK can enumerate the attached `MSO300` successfully
- MorphoManager login and license state can still be inspected for diagnostics, but MorphoManager device registration is no longer the primary app path
- The MorphoManager GUI may still show license-limited `Biometric device` and `Biometric Identification` screens on this PC

That means the attendance app can now target the live `MSO300` through the direct Morpho SDK path even though the MorphoManager administration screens remain license-restricted.

## 5. Probe the connected USB sensor

This repo now includes a safe serial probe tool:

```powershell
python tools/probe_sensor.py
python tools/probe_sensor.py --port COM6
```

What it does:

- Lists serial USB devices
- Recognizes some known fingerprint sensor VID/PID pairs
- Opens the selected COM port
- Passively listens for bytes without sending commands by default

Advanced use:

```powershell
python tools/probe_sensor.py --port COM6 --baud 57600 --duration 10
```

If you later obtain official MorphoSmart command bytes or SDK behavior, the same tool can send a raw test frame:

```powershell
python tools/probe_sensor.py --port COM6 --send-hex 01020304
```

This repo also includes a Windows-specific setup checker for the detected MSO300:

```powershell
python tools/check_mso300_setup.py
python tools/check_mso300_setup.py --port COM6
```

It confirms:

- Whether Windows can still see the `079B:0024` sensor
- Which COM port it is using
- Which Windows driver is bound to it
- Whether the COM port opens successfully
- Whether any Morpho or IDEMIA software is already installed

## 6. Features Included

- Staff registration
- Role-based access for `Super Admin`, `HR/Admin`, `Department Manager`, `Supervisor`, and `Staff`
- Shift start/end and grace-minute management
- Fingerprint enrollment and removal
- Automatic check-in/check-out switching
- Staff self-service clock in / clock out
- Break start / break end tracking
- Mobile-friendly staff portal with today's attendance status
- GPS-aware mobile clocking
- Kiosk quick access with PIN/password and QR token support
- Staff QR badge generation for mobile quick access and kiosk scanners
- Late and early-checkout labeling
- Dashboard summary cards
- Attendance log filtering
- CSV export

## 7. Tests

Run:

```powershell
python -m unittest discover -s tests
```

## 8. Online Deployment

This repo is now prepared for a Render web deployment with:

- [render.yaml](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/render.yaml>)
- `gunicorn` production startup
- `/health` endpoint for platform checks
- persistent-disk paths for the SQLite database
- a cloud-safe `disabled` fingerprint backend

Important deployment note:

- The online version supports `admin`, `staff`, `GPS`, `QR`, and `PIN/password` access across many devices.
- The `MorphoSmart MSO300` fingerprint hardware does **not** move to the cloud automatically.
- For online deployment, the kiosk should use `QR` / `PIN` / `password`, or you should keep the fingerprint scanner on a local kiosk machine and bridge it separately.

Render setup:

1. Push this project to `GitHub`, `GitLab`, or `Bitbucket`
2. Open Render and create a new Blueprint deploy from the repo
3. Use the included `render.yaml`
4. Fill these secret environment variables in Render:
   - `ATTENDANCE_ADMIN_USER`
   - `ATTENDANCE_ADMIN_PASSWORD`
5. Deploy

The included Render configuration uses a persistent disk and stores the live database at:

```text
/var/data/instance/attendance.db
```

## 9. Important Hardware Note

The full software is built and ready, but exact live fingerprint capture depends on your hardware model and how it connects:

- USB serial fingerprint modules can often use `pyfingerprint`
- Vendor-specific biometric terminals may need the `http_bridge` mode

If you send me your exact fingerprint sensor model number or vendor name next, I can adapt the live hardware backend to that device more precisely.
