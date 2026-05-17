# Multi-Organization Setup

This system now supports serving multiple institutions from one codebase while keeping each institution's data separate.

## How isolation works

- Each institution gets its own SQLite database.
- Each institution gets its own file storage folder for:
  - staff photos
  - system logo
  - selfie audit captures
  - enrollment session previews
  - mock fingerprint store
- The active institution is selected by the request hostname.

That means:

- `attendance.school-a.com` can use one database
- `attendance.hospital-b.com` can use a different database
- both can run on the same deployed application without mixing staff, attendance logs, photos, or settings

## Create a new institution

Run this from the project root:

```powershell
flask create-organization --slug school-a --name "School A" --hostname attendance.school-a.com
```

You can add more than one hostname:

```powershell
flask create-organization --slug hospital-b --name "Hospital B" --hostname attendance.hospital-b.com --hostname hospital-b-attendance.example.org
```

## List all institutions

```powershell
flask list-organizations
```

## Seed demo data into one institution

```powershell
flask seed-demo --slug school-a
```

## Reset one institution only

```powershell
flask reset-live-data --slug school-a
```

## Default institution

The app still keeps one default organization for:

- localhost
- local development
- any host that is not explicitly mapped yet

You can control the default slug with:

```powershell
ATTENDANCE_DEFAULT_ORGANIZATION_SLUG
```

## What to do for each new customer

1. Point the customer's subdomain or domain to the same app deployment.
2. Create the institution with `flask create-organization`.
3. Log in on that hostname.
4. Set that institution's:
   - system name
   - logo
   - location rules
   - staff
   - shifts
   - credentials

From that point onward, the institution works in its own isolated workspace.
