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
    shift_id INTEGER,
    shift_start TEXT NOT NULL DEFAULT '09:00',
    shift_end TEXT NOT NULL DEFAULT '17:00',
    grace_minutes INTEGER NOT NULL DEFAULT 15,
    base_salary REAL NOT NULL DEFAULT 0,
    overtime_hourly_rate REAL NOT NULL DEFAULT 0,
    tax_deduction REAL NOT NULL DEFAULT 0,
    provident_fund REAL NOT NULL DEFAULT 0,
    health_insurance REAL NOT NULL DEFAULT 0,
    other_deduction REAL NOT NULL DEFAULT 0,
    payment_method TEXT NOT NULL DEFAULT 'Bank Transfer',
    bank_name TEXT,
    account_name TEXT,
    account_number TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    fingerprint_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    shift_start TEXT NOT NULL,
    shift_end TEXT NOT NULL,
    break_label TEXT NOT NULL DEFAULT 'Flexible',
    grace_minutes INTEGER NOT NULL DEFAULT 15,
    weekly_off TEXT NOT NULL DEFAULT 'Configured per department',
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_work_shifts_active
ON work_shifts(is_active, name);

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

CREATE TABLE IF NOT EXISTS payroll_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payroll_month TEXT NOT NULL,
    staff_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending',
    notes TEXT,
    processed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(payroll_month, staff_id),
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payroll_entries_month_status
ON payroll_entries(payroll_month, status);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_selfie_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id INTEGER NOT NULL,
    audit_type TEXT NOT NULL,
    login_identifier TEXT,
    auth_method TEXT NOT NULL,
    photo_filename TEXT NOT NULL,
    photo_mime_type TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    ip_address TEXT,
    device_name TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_staff_selfie_audits_staff_created
ON staff_selfie_audits(staff_id, created_at DESC);

CREATE TABLE IF NOT EXISTS admin_activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_type TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    actor_role TEXT,
    event_type TEXT NOT NULL,
    target_name TEXT,
    details TEXT,
    ip_address TEXT,
    device_name TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_activity_logs_created
ON admin_activity_logs(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS notification_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audience TEXT NOT NULL DEFAULT 'admin',
    category TEXT NOT NULL DEFAULT 'system',
    tone TEXT NOT NULL DEFAULT 'neutral',
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    action_url TEXT,
    target_staff_id INTEGER,
    is_read INTEGER NOT NULL DEFAULT 0,
    read_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (target_staff_id) REFERENCES staff(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notification_events_audience_read_created
ON notification_events(audience, is_read, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_notification_events_target_staff_created
ON notification_events(target_staff_id, created_at DESC, id DESC);
