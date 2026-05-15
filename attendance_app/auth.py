from __future__ import annotations

from functools import wraps
import hmac
from secrets import token_urlsafe

from flask import flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

SUPER_ADMIN = "Super Admin"
HR_ADMIN = "HR/Admin"
DEPARTMENT_MANAGER = "Department Manager"
SUPERVISOR = "Supervisor"
STAFF = "Staff"

ACCESS_ROLE_CHOICES = [
    SUPER_ADMIN,
    HR_ADMIN,
    DEPARTMENT_MANAGER,
    SUPERVISOR,
    STAFF,
]
ADMIN_PANEL_ROLES = {SUPER_ADMIN, HR_ADMIN, DEPARTMENT_MANAGER, SUPERVISOR}
STAFF_MANAGEMENT_ROLES = {SUPER_ADMIN, HR_ADMIN, DEPARTMENT_MANAGER}
SETTINGS_ROLES = {SUPER_ADMIN, HR_ADMIN}
REPORTING_ROLES = {SUPER_ADMIN, HR_ADMIN, DEPARTMENT_MANAGER, SUPERVISOR}
DEPARTMENT_SCOPED_ROLES = {DEPARTMENT_MANAGER, SUPERVISOR}


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_authenticated"):
            flash("Sign in as an administrator to continue.", "warning")
            return redirect(url_for("app.admin_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def credentials_match(expected_username: str, expected_password: str, username: str, password: str) -> bool:
    return hmac.compare_digest(expected_username, username) and hmac.compare_digest(
        expected_password,
        password,
    )


def normalize_access_role(value: str) -> str:
    cleaned = (value or "").strip().lower()
    for role in ACCESS_ROLE_CHOICES:
        if role.lower() == cleaned:
            return role
    return STAFF


def hash_secret(secret: str) -> str:
    return generate_password_hash(secret)


def secret_matches(secret_hash: str | None, secret: str) -> bool:
    if not secret_hash or not secret:
        return False
    return check_password_hash(secret_hash, secret)


def new_qr_token() -> str:
    return token_urlsafe(18)


def clear_user_session() -> None:
    session.clear()


def start_platform_admin_session(username: str) -> None:
    clear_user_session()
    session["admin_authenticated"] = True
    session["staff_authenticated"] = False
    session["is_platform_admin"] = True
    session["admin_username"] = username
    session["display_name"] = username
    session["access_role"] = SUPER_ADMIN
    session["managed_department"] = ""


def start_staff_session(staff: dict[str, object]) -> None:
    access_role = normalize_access_role(str(staff.get("access_role", STAFF)))
    full_name = f"{staff.get('first_name', '')} {staff.get('last_name', '')}".strip() or str(
        staff.get("staff_code", "Staff")
    )
    clear_user_session()
    session["staff_authenticated"] = True
    session["staff_id"] = int(staff["id"])
    session["staff_code"] = str(staff.get("staff_code", ""))
    session["staff_name"] = full_name
    session["display_name"] = full_name
    session["staff_department"] = str(staff.get("department", ""))
    session["access_role"] = access_role
    session["managed_department"] = str(staff.get("department", "")) if access_role in DEPARTMENT_SCOPED_ROLES else ""
    session["admin_authenticated"] = access_role in ADMIN_PANEL_ROLES
    session["admin_username"] = full_name if access_role in ADMIN_PANEL_ROLES else ""
    session["is_platform_admin"] = False


def current_access_role() -> str:
    return normalize_access_role(str(session.get("access_role", ""))) if session.get("access_role") else ""


def current_department_scope() -> str:
    if is_platform_admin():
        return ""
    role = current_access_role()
    if role in DEPARTMENT_SCOPED_ROLES:
        return str(session.get("managed_department", "") or session.get("staff_department", ""))
    return ""


def current_display_name() -> str:
    return str(
        session.get("display_name")
        or session.get("staff_name")
        or session.get("admin_username")
        or "Guest"
    )


def is_platform_admin() -> bool:
    return bool(session.get("is_platform_admin"))


def is_staff_authenticated() -> bool:
    return bool(session.get("staff_authenticated"))


def has_any_role(*roles: str) -> bool:
    if is_platform_admin():
        return True
    current = current_access_role()
    allowed = {normalize_access_role(role) for role in roles}
    return bool(current and current in allowed)


def staff_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not is_staff_authenticated():
            flash("Sign in as a staff member to continue.", "warning")
            return redirect(url_for("app.staff_login", next=request.path))
        return view(*args, **kwargs)

    return wrapped_view


def roles_required(*roles: str):
    allowed_roles = {normalize_access_role(role) for role in roles}

    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if not session.get("admin_authenticated") and not is_staff_authenticated():
                flash("Sign in to continue.", "warning")
                login_endpoint = "app.admin_login" if request.path.startswith("/admin") else "app.staff_login"
                return redirect(url_for(login_endpoint, next=request.path))

            if is_platform_admin():
                return view(*args, **kwargs)

            current = current_access_role()
            if current not in allowed_roles:
                flash("You are not authorized for that area.", "warning")
                if is_staff_authenticated():
                    return redirect(url_for("app.staff_home"))
                return redirect(url_for("app.kiosk"))
            return view(*args, **kwargs)

        return wrapped_view

    return decorator
