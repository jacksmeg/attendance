# Online Deployment Guide

This system is ready for an online multi-device deployment with:

- admin web access
- staff web/mobile access
- GPS clocking
- QR clocking
- PIN/password kiosk clocking
- persistent uploaded photos and branding
- persistent SQLite storage on a Render disk

## Important Fingerprint Note

The cloud deployment does **not** move the MorphoSmart fingerprint hardware to the internet by itself.

Online mode uses:

- QR
- PIN/password
- mobile/staff portal

Fingerprint enrollment and live fingerprint verification remain on the **local kiosk installation** unless you build a separate hardware bridge service.

## Files Already Prepared

- [render.yaml](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/render.yaml>)
- [app.py](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/app.py>)
- [attendance_app/config.py](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/attendance_app/config.py>)
- [attendance_app/fingerprint/disabled.py](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/attendance_app/fingerprint/disabled.py>)
- [attendance_app/views.py](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/attendance_app/views.py>)
- [.python-version](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/.python-version>)

## Render Environment Variables

Set these in Render:

- `ATTENDANCE_ADMIN_USER`
- `ATTENDANCE_ADMIN_PASSWORD`
- `ATTENDANCE_SECRET_KEY`

These are already covered in `render.yaml`:

- `ATTENDANCE_FINGERPRINT_BACKEND=disabled`
- `ATTENDANCE_INSTANCE_DIR=/var/data/instance`
- `ATTENDANCE_DB_PATH=/var/data/instance/attendance.db`
- `ATTENDANCE_MOCK_STORE_PATH=/var/data/instance/mock_fingerprint_store.json`

## Deploy Steps

1. Initialize Git in this project if it is not already initialized.
2. Create a GitHub, GitLab, or Bitbucket repository.
3. Push this project to that remote repository.
4. In Render, create a new Blueprint deployment from that repository.
5. Let Render read [render.yaml](</C:/Users/El/Desktop/ATTENDANCE SYSTEM/render.yaml>).
6. Fill the secret environment variables.
7. Deploy.

## After Deploy

Check:

- `/health`
- `/admin/login`
- `/staff/login`
- `/kiosk`

Then:

1. change the admin password
2. add real staff
3. upload staff photos
4. assign QR/PIN/password access
5. start live clocking

## Local Fingerprint Kiosk Strategy

Recommended setup for mixed online + fingerprint use:

- Keep the online server on Render for staff/admin access.
- Keep one local kiosk machine with the MorphoSmart scanner for fingerprint capture.
- Use the online system for reporting, mobile use, staff management, QR, and PIN/password access.
