from __future__ import annotations

from base64 import b64encode
from datetime import datetime
from typing import Any, Mapping

from attendance_app.auth import STAFF, hash_secret, new_qr_token, normalize_access_role, secret_matches
from attendance_app.db import get_db
from attendance_app.services.shifts import find_shift_id_by_window


def list_staff(
    search: str = "",
    department: str = "",
    active_only: bool = False,
    fingerprint_adapter: str | None = None,
    department_scope: str = "",
) -> list[dict[str, Any]]:
    db = get_db()
    join_clause = """
        LEFT JOIN fingerprint_templates f
            ON f.staff_id = s.id AND f.is_active = 1
    """
    params: list[Any] = []
    if fingerprint_adapter:
        join_clause += " AND f.adapter = ?"
        params.append(fingerprint_adapter)

    query = """
        SELECT
            s.*,
            f.adapter AS fingerprint_adapter,
            f.template_ref,
            f.template_format,
            f.quality_score,
            f.enrolled_at
        FROM staff s
    """
    query += join_clause
    query += """
        WHERE 1 = 1
    """

    if search:
        query += """
            AND (
                s.staff_code LIKE ?
                OR s.first_name LIKE ?
                OR s.last_name LIKE ?
                OR s.department LIKE ?
                OR s.role LIKE ?
                OR s.access_role LIKE ?
            )
        """
        wildcard = f"%{search}%"
        params.extend([wildcard, wildcard, wildcard, wildcard, wildcard, wildcard])

    if department_scope:
        query += " AND s.department = ?"
        params.append(department_scope)
    if department:
        query += " AND s.department = ?"
        params.append(department)
    if active_only:
        query += " AND s.is_active = 1"

    query += " ORDER BY s.department, s.last_name, s.first_name"
    rows = db.execute(query, params).fetchall()
    return [_decorate_staff_row(dict(row), ensure_token=True) for row in rows]


def get_staff(
    staff_id: int,
    fingerprint_adapter: str | None = None,
    department_scope: str = "",
) -> dict[str, Any] | None:
    db = get_db()
    join_clause = """
        LEFT JOIN fingerprint_templates f
            ON f.staff_id = s.id AND f.is_active = 1
    """
    params: list[Any] = []
    if fingerprint_adapter:
        join_clause += " AND f.adapter = ?"
        params.append(fingerprint_adapter)

    query = """
        SELECT
            s.*,
            f.adapter AS fingerprint_adapter,
            f.template_ref,
            f.template_format,
            f.quality_score,
            f.enrolled_at
        FROM staff s
        """
    query += join_clause
    query += """
        WHERE s.id = ?
        """
    params.append(staff_id)
    if department_scope:
        query += " AND s.department = ?"
        params.append(department_scope)

    row = db.execute(query, params).fetchone()
    return _decorate_staff_row(dict(row), ensure_token=True) if row else None


def get_staff_by_code(staff_code: str) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT *
        FROM staff
        WHERE staff_code = ? AND is_active = 1
        LIMIT 1
        """,
        (staff_code.strip().upper(),),
    ).fetchone()
    return _decorate_staff_row(dict(row), ensure_token=True) if row else None


def get_staff_by_login_identifier(login_identifier: str) -> dict[str, Any] | None:
    value = login_identifier.strip()
    if not value:
        return None
    db = get_db()
    row = db.execute(
        """
        SELECT *
        FROM staff
        WHERE
            is_active = 1
            AND (
                UPPER(staff_code) = UPPER(?)
                OR LOWER(email) = LOWER(?)
            )
        LIMIT 1
        """,
        (value, value),
    ).fetchone()
    return _decorate_staff_row(dict(row), ensure_token=True) if row else None


def get_staff_by_qr_token(qr_token: str) -> dict[str, Any] | None:
    db = get_db()
    row = db.execute(
        """
        SELECT *
        FROM staff
        WHERE qr_token = ? AND is_active = 1
        LIMIT 1
        """,
        (qr_token.strip(),),
    ).fetchone()
    return _decorate_staff_row(dict(row), ensure_token=False) if row else None


def authenticate_staff(
    staff_code: str = "",
    login_identifier: str = "",
    password: str = "",
    pin: str = "",
) -> dict[str, Any] | None:
    staff = get_staff_by_login_identifier(login_identifier or staff_code)
    if not staff:
        return None
    if password and secret_matches(staff.get("password_hash"), password):
        return staff
    if pin and secret_matches(staff.get("pin_hash"), pin):
        return staff
    return None


def verify_staff_reset_identity(
    *,
    login_identifier: str,
    date_of_birth: str,
    phone: str = "",
    email: str = "",
) -> dict[str, Any] | None:
    staff = get_staff_by_login_identifier(login_identifier)
    if not staff:
        return None
    if str(staff.get("date_of_birth", "")).strip() != date_of_birth.strip():
        return None

    phone_match = bool(phone and _normalize_phone(staff.get("phone", "")) == _normalize_phone(phone))
    email_match = bool(email and str(staff.get("email", "")).strip().lower() == email.strip().lower())
    if not (phone_match or email_match):
        return None
    return staff


def reset_staff_credentials(
    staff_id: int,
    *,
    password: str = "",
    pin: str = "",
) -> None:
    updates: list[str] = []
    params: list[Any] = []
    if password:
        updates.append("password_hash = ?")
        params.append(hash_secret(password))
    if pin:
        updates.append("pin_hash = ?")
        params.append(hash_secret(pin))
    if not updates:
        return

    db = get_db()
    updates.append("updated_at = ?")
    params.append(datetime.now().isoformat(timespec="seconds"))
    params.append(staff_id)
    db.execute(
        f"""
        UPDATE staff
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        params,
    )
    db.commit()


def create_staff(data: Mapping[str, Any]) -> int:
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    shift_start = str(data.get("shift_start", "09:00"))
    shift_end = str(data.get("shift_end", "17:00"))
    grace_minutes = int(data.get("grace_minutes", 15))
    shift_id = find_shift_id_by_window(shift_start, shift_end, grace_minutes)
    cursor = db.execute(
        """
        INSERT INTO staff (
            staff_code, first_name, last_name, email, phone, photo_filename,
            ghana_card_number, nationality, sex, date_of_birth, place_of_birth, residential_address, digital_address,
            department, role, access_role,
            password_hash, pin_hash, qr_token,
            allow_mobile_clock, allow_pin_clock, allow_qr_clock,
            shift_id, shift_start, shift_end, grace_minutes,
            base_salary, overtime_hourly_rate, tax_deduction, provident_fund, health_insurance, other_deduction,
            payment_method, bank_name, account_name, account_number,
            is_active,
            fingerprint_enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(data["staff_code"]).strip().upper(),
            data["first_name"],
            data["last_name"],
            data.get("email", ""),
            data.get("phone", ""),
            data.get("photo_filename"),
            data.get("ghana_card_number", ""),
            data.get("nationality", ""),
            data.get("sex", ""),
            data.get("date_of_birth", ""),
            data.get("place_of_birth", ""),
            data.get("residential_address", ""),
            data.get("digital_address", ""),
            data["department"],
            data["role"],
            normalize_access_role(str(data.get("access_role", STAFF))),
            hash_secret(str(data["portal_password"])) if data.get("portal_password") else None,
            hash_secret(str(data["portal_pin"])) if data.get("portal_pin") else None,
            str(data.get("qr_token") or new_qr_token()),
            int(bool(data.get("allow_mobile_clock", True))),
            int(bool(data.get("allow_pin_clock", True))),
            int(bool(data.get("allow_qr_clock", True))),
            shift_id,
            shift_start,
            shift_end,
            grace_minutes,
            float(data.get("base_salary", 0) or 0),
            float(data.get("overtime_hourly_rate", 0) or 0),
            float(data.get("tax_deduction", 0) or 0),
            float(data.get("provident_fund", 0) or 0),
            float(data.get("health_insurance", 0) or 0),
            float(data.get("other_deduction", 0) or 0),
            data.get("payment_method", "Bank Transfer"),
            data.get("bank_name", ""),
            data.get("account_name", ""),
            data.get("account_number", ""),
            int(bool(data.get("is_active", True))),
            0,
            now,
            now,
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def update_staff(staff_id: int, data: Mapping[str, Any]) -> None:
    db = get_db()
    shift_start = str(data.get("shift_start", "09:00"))
    shift_end = str(data.get("shift_end", "17:00"))
    grace_minutes = int(data.get("grace_minutes", 15))
    shift_id = find_shift_id_by_window(shift_start, shift_end, grace_minutes)
    updates = [
        "staff_code = ?",
        "first_name = ?",
        "last_name = ?",
        "email = ?",
        "phone = ?",
        "photo_filename = ?",
        "ghana_card_number = ?",
        "nationality = ?",
        "sex = ?",
        "date_of_birth = ?",
        "place_of_birth = ?",
        "residential_address = ?",
        "digital_address = ?",
        "department = ?",
        "role = ?",
        "access_role = ?",
        "allow_mobile_clock = ?",
        "allow_pin_clock = ?",
        "allow_qr_clock = ?",
        "shift_id = ?",
        "shift_start = ?",
        "shift_end = ?",
        "grace_minutes = ?",
        "base_salary = ?",
        "overtime_hourly_rate = ?",
        "tax_deduction = ?",
        "provident_fund = ?",
        "health_insurance = ?",
        "other_deduction = ?",
        "payment_method = ?",
        "bank_name = ?",
        "account_name = ?",
        "account_number = ?",
        "is_active = ?",
        "updated_at = ?",
    ]
    params: list[Any] = [
        str(data["staff_code"]).strip().upper(),
        data["first_name"],
        data["last_name"],
        data.get("email", ""),
        data.get("phone", ""),
        data.get("photo_filename"),
        data.get("ghana_card_number", ""),
        data.get("nationality", ""),
        data.get("sex", ""),
        data.get("date_of_birth", ""),
        data.get("place_of_birth", ""),
        data.get("residential_address", ""),
        data.get("digital_address", ""),
        data["department"],
        data["role"],
        normalize_access_role(str(data.get("access_role", STAFF))),
        int(bool(data.get("allow_mobile_clock", True))),
        int(bool(data.get("allow_pin_clock", True))),
        int(bool(data.get("allow_qr_clock", True))),
        shift_id,
        shift_start,
        shift_end,
        grace_minutes,
        float(data.get("base_salary", 0) or 0),
        float(data.get("overtime_hourly_rate", 0) or 0),
        float(data.get("tax_deduction", 0) or 0),
        float(data.get("provident_fund", 0) or 0),
        float(data.get("health_insurance", 0) or 0),
        float(data.get("other_deduction", 0) or 0),
        data.get("payment_method", "Bank Transfer"),
        data.get("bank_name", ""),
        data.get("account_name", ""),
        data.get("account_number", ""),
        int(bool(data.get("is_active", True))),
        datetime.now().isoformat(timespec="seconds"),
    ]

    if data.get("portal_password"):
        updates.append("password_hash = ?")
        params.append(hash_secret(str(data["portal_password"])))
    if data.get("portal_pin"):
        updates.append("pin_hash = ?")
        params.append(hash_secret(str(data["portal_pin"])))
    if data.get("regenerate_qr"):
        updates.append("qr_token = ?")
        params.append(new_qr_token())

    params.append(staff_id)
    db.execute(
        f"""
        UPDATE staff
        SET {", ".join(updates)}
        WHERE id = ?
        """,
        params,
    )
    db.commit()


def update_staff_access_role(
    staff_id: int,
    access_role: str,
    *,
    is_active: bool,
    department_scope: str = "",
) -> bool:
    db = get_db()
    params: list[Any] = [
        normalize_access_role(access_role),
        int(bool(is_active)),
        datetime.now().isoformat(timespec="seconds"),
        staff_id,
    ]
    query = """
        UPDATE staff
        SET access_role = ?, is_active = ?, updated_at = ?
        WHERE id = ?
    """
    if department_scope:
        query += " AND department = ?"
        params.append(department_scope)

    cursor = db.execute(query, params)
    db.commit()
    return cursor.rowcount > 0


def mark_ghana_card_verified(
    staff_id: int,
    *,
    verified_at: str,
    verified_by: str,
) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE staff
        SET ghana_card_verified_at = ?, ghana_card_verified_by = ?, updated_at = ?
        WHERE id = ?
        """,
        (verified_at, verified_by, datetime.now().isoformat(timespec="seconds"), staff_id),
    )
    db.commit()


def rotate_staff_qr_token(staff_id: int) -> str:
    db = get_db()
    token = new_qr_token()
    db.execute(
        "UPDATE staff SET qr_token = ?, updated_at = ? WHERE id = ?",
        (token, datetime.now().isoformat(timespec="seconds"), staff_id),
    )
    db.commit()
    return token


def upsert_fingerprint(
    staff_id: int,
    adapter: str,
    template_ref: str,
    template_format: str = "",
    template_data: bytes | None = None,
    quality_score: int | None = None,
    notes: str = "",
) -> None:
    db = get_db()
    enrolled_at = datetime.now().isoformat(timespec="seconds")
    db.execute(
        "UPDATE fingerprint_templates SET is_active = 0 WHERE staff_id = ?",
        (staff_id,),
    )
    db.execute(
        """
        INSERT INTO fingerprint_templates (
            staff_id, adapter, template_ref, template_format, template_data,
            quality_score, notes, enrolled_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            staff_id,
            adapter,
            template_ref,
            template_format or None,
            template_data,
            quality_score,
            notes,
            enrolled_at,
        ),
    )
    db.execute(
        """
        UPDATE staff
        SET fingerprint_enabled = 1, updated_at = ?
        WHERE id = ?
        """,
        (datetime.now().isoformat(timespec="seconds"), staff_id),
    )
    db.commit()


def remove_fingerprint(staff_id: int) -> None:
    db = get_db()
    db.execute(
        "UPDATE fingerprint_templates SET is_active = 0 WHERE staff_id = ?",
        (staff_id,),
    )
    db.execute(
        """
        UPDATE staff
        SET fingerprint_enabled = 0, updated_at = ?
        WHERE id = ?
        """,
        (datetime.now().isoformat(timespec="seconds"), staff_id),
    )
    db.commit()


def get_staff_by_template_ref(
    template_ref: str,
    adapter: str | None = None,
) -> dict[str, Any] | None:
    db = get_db()
    params: list[Any] = [template_ref]
    adapter_clause = ""
    if adapter:
        adapter_clause = " AND f.adapter = ?"
        params.append(adapter)

    row = db.execute(
        """
        SELECT
            s.*,
            f.adapter AS fingerprint_adapter,
            f.template_ref,
            f.template_format,
            f.quality_score,
            f.enrolled_at
        FROM fingerprint_templates f
        JOIN staff s ON s.id = f.staff_id
        WHERE f.template_ref = ? AND f.is_active = 1 AND s.is_active = 1
        """
        + adapter_clause,
        params,
    ).fetchone()
    return _decorate_staff_row(dict(row), ensure_token=True) if row else None


def count_active_fingerprints(adapter: str | None = None) -> int:
    db = get_db()
    params: list[Any] = []
    adapter_clause = ""
    if adapter:
        adapter_clause = " AND f.adapter = ?"
        params.append(adapter)

    row = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM fingerprint_templates f
        JOIN staff s ON s.id = f.staff_id
        WHERE f.is_active = 1 AND s.is_active = 1
        """
        + adapter_clause,
        params,
    ).fetchone()
    return int(row["count"]) if row else 0


def count_active_staff(department_scope: str = "") -> int:
    db = get_db()
    query = "SELECT COUNT(*) AS count FROM staff WHERE is_active = 1"
    params: list[Any] = []
    if department_scope:
        query += " AND department = ?"
        params.append(department_scope)
    row = db.execute(query, params).fetchone()
    return int(row["count"]) if row else 0


def list_fingerprint_candidates(adapter: str) -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            f.template_ref,
            f.template_format,
            f.template_data
        FROM fingerprint_templates f
        JOIN staff s ON s.id = f.staff_id
        WHERE
            f.is_active = 1
            AND s.is_active = 1
            AND f.adapter = ?
            AND f.template_data IS NOT NULL
            AND f.template_format IS NOT NULL
        ORDER BY f.id
        """,
        (adapter,),
    ).fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        template_data = row["template_data"]
        if not template_data:
            continue
        candidates.append(
            {
                "template_ref": row["template_ref"],
                "template_format": row["template_format"],
                "template_data_base64": b64encode(bytes(template_data)).decode("ascii"),
            }
        )
    return candidates


def list_departments(department_scope: str = "") -> list[str]:
    db = get_db()
    query = "SELECT DISTINCT department FROM staff WHERE department <> ''"
    params: list[Any] = []
    if department_scope:
        query += " AND department = ?"
        params.append(department_scope)
    query += " ORDER BY department"
    rows = db.execute(query, params).fetchall()
    return [row["department"] for row in rows]


def list_mock_scan_choices() -> list[dict[str, Any]]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            s.id,
            s.staff_code,
            s.first_name,
            s.last_name,
            s.department,
            f.template_ref
        FROM staff s
        JOIN fingerprint_templates f ON f.staff_id = s.id AND f.is_active = 1
        WHERE s.is_active = 1 AND f.adapter = 'mock'
        ORDER BY s.last_name, s.first_name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _decorate_staff_row(row: dict[str, Any], ensure_token: bool) -> dict[str, Any]:
    row["access_role"] = normalize_access_role(str(row.get("access_role", STAFF)))
    if ensure_token and not row.get("qr_token") and row.get("id"):
        row["qr_token"] = _ensure_qr_token(int(row["id"]))
    return row


def _ensure_qr_token(staff_id: int) -> str:
    db = get_db()
    token = new_qr_token()
    db.execute(
        "UPDATE staff SET qr_token = ?, updated_at = ? WHERE id = ? AND (qr_token IS NULL OR qr_token = '')",
        (token, datetime.now().isoformat(timespec="seconds"), staff_id),
    )
    db.commit()
    row = db.execute("SELECT qr_token FROM staff WHERE id = ?", (staff_id,)).fetchone()
    return str(row["qr_token"]) if row and row["qr_token"] else token


def _normalize_phone(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())
