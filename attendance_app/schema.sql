CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_code TEXT NOT NULL UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    photo_filename TEXT,
    ghana_card_number TEXT,
    nationality TEXT,
    sex TEXT,
    date_of_birth TEXT,
    place_of_birth TEXT,
    residential_address TEXT,
    digital_address TEXT,
    ghana_card_verified_at TEXT,
    ghana_card_verified_by TEXT,
    department TEXT NOT NULL,
    role TEXT NOT NULL,
    access_role TEXT NOT NULL DEFAULT 'Staff',
    password_hash TEXT,
    pin_hash TEXT,
    qr_token TEXT,
    allow_mobile_clock INTEGER NOT NULL DEFAULT 1,
    allow_pin_clock INTEGER NOT NULL DEFAULT 1,
    allow_qr_clock INTEGER NOT NULL DEFAULT 1,
    shift_start TEXT NOT NULL DEFAULT '09:00',
    shift_end TEXT NOT NULL DEFAULT '17:00',
    grace_minutes INTEGER NOT NULL DEFAULT 15,
    is_active INTEGER NOT NULL DEFAULT 1,
    fingerprint_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fingerprint_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL,
    adapter TEXT NOT NULL,
    template_ref TEXT NOT NULL,
    template_format TEXT,
    template_data BLOB,
    quality_score INTEGER,
    notes TEXT,
    enrolled_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attendance_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL,
    attendance_date TEXT NOT NULL,
    event_time TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status_label TEXT NOT NULL,
    method TEXT NOT NULL,
    template_ref TEXT,
    match_score INTEGER,
    device_name TEXT,
    latitude REAL,
    longitude REAL,
    gps_accuracy REAL,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attendance_events_staff_date
ON attendance_events(staff_id, attendance_date, event_time DESC);

CREATE INDEX IF NOT EXISTS idx_attendance_events_date
ON attendance_events(attendance_date, event_time DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
