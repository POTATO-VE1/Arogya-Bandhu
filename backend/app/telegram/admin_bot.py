"""Admin Telegram bot — only responds to the admin's phone number.

Provides conversational flow for creating/listing/deleting staff accounts.
All new users get a random secure password that the admin can share.

Security: Admin must verify by sharing their Telegram contact (phone).
Only the phone matching ADMIN_PHONE_NUMBER gets admin access.

T12 follow-up: verified-admin state is persisted in the
`telegram_sessions.is_admin` column so an admin doesn't have to
re-verify after a server restart.
"""
from __future__ import annotations

import random
import re
import string
from typing import Any

from app.config import settings
from app.db import SessionLocal, now_utc
from app.models import User
from app.security import hash_password
from app.telegram.sessions import Session, get_session, save_session

MAX_ADMIN_STATES = 1000  # bound the in-memory conversation-state cache


# ── Admin verification ───────────────────────────────────────────────────────
# Only `_pending_admin_verify` stays in memory (it's transient — the
# user sends a contact share within seconds or abandons). The actual
# verified-admin flag lives on the persisted Session.

_pending_admin_verify: dict[int, bool] = {}


def _evict_admin_pending_if_full() -> None:
    if len(_pending_admin_verify) > MAX_ADMIN_STATES:
        # pop the first 10% by insertion order
        for k in list(_pending_admin_verify.keys())[: MAX_ADMIN_STATES // 10]:
            _pending_admin_verify.pop(k, None)


def is_verified_admin(telegram_id: int) -> bool:
    """Check if this telegram user has been verified as admin.

    Reads from the persisted Session (which was rehydrated in
    get_session) so verified state survives a restart.
    """
    s = get_session(telegram_id)
    return s.admin


def request_admin_verify(telegram_id: int) -> None:
    _pending_admin_verify[telegram_id] = True
    _evict_admin_pending_if_full()


def handle_contact_share(telegram_id: int, phone: str) -> bool:
    """Handle a contact share from Telegram. Returns True if admin verified."""
    if telegram_id not in _pending_admin_verify:
        return False
    _pending_admin_verify.pop(telegram_id, None)

    if not settings.ADMIN_PHONE_NUMBER:
        return False

    def normalize(p: str) -> str:
        p = p.strip().replace(" ", "").replace("-", "")
        if not p.startswith("+"):
            p = "+91" + p
        return p

    if normalize(phone) == normalize(settings.ADMIN_PHONE_NUMBER):
        s = get_session(telegram_id)
        s.admin = True
        save_session(s)
        return True
    return False


def generate_password(length: int = 12) -> str:
    """Random password: 2 uppercase, 2 digits, 8 lowercase."""
    chars = string.ascii_lowercase
    upper = random.sample(string.ascii_uppercase, 2)
    digits = random.sample(string.digits, 2)
    rest = random.choices(chars, k=length - 4)
    pw = upper + digits + rest
    random.shuffle(pw)
    return "".join(pw)


# ── Conversation state (in-memory only; admin flow is short-lived) ──────────
_admin_states: dict[int, dict[str, Any]] = {}


def _evict_admin_states_if_full() -> None:
    if len(_admin_states) > MAX_ADMIN_STATES:
        for k in list(_admin_states.keys())[: MAX_ADMIN_STATES // 10]:
            _admin_states.pop(k, None)


VALID_ROLES = {
    "doctor": {
        "label": "Doctor",
        "fields": [
            ("display_name", "Doctor's full name (e.g. Dr. Priya Sharma)"),
            ("department", "Department (e.g. Surgery, Medicine, Orthopedics)"),
        ],
    },
    "nurse": {
        "label": "Nurse",
        "fields": [
            ("display_name", "Nurse's full name (e.g. Nurse Kavitha)"),
            ("ward", "Ward assignment (e.g. Ward-4, ICU, Emergency)"),
            ("supervisor", "Supervisor username (e.g. dr.priya) — required for reporting hierarchy"),
        ],
    },
    "staff": {
        "label": "Hospital Staff / Intern",
        "fields": [
            ("display_name", "Full name"),
            ("department", "Department or ward"),
            ("supervisor", "Supervisor username (e.g. dr.priya or nurse.kavitha) — required for reporting hierarchy"),
        ],
    },
}


def _validate_supervisor(s, username: str) -> tuple[bool, str]:
    """Return (ok, message). Supervisor must exist and be a doctor or nurse
    in the same hospital. Prevents creating orphan staff with no reporting line.
    """
    if not username:
        return False, "[X] Supervisor is required. Enter a valid username (e.g., dr.priya)."
    u = s.query(User).filter(
        User.username == username,
        User.hospital_code == settings.HOSPITAL_CODE,
    ).first()
    if not u:
        return False, f"[X] Supervisor '@{username}' not found. Create them first or use an existing doctor's username."
    if u.role not in ("doctor", "nurse", "admin"):
        return False, f"[X] Supervisor '@{username}' is a {u.role}, not a doctor/nurse. Choose a doctor or nurse."
    return True, f"supervisor {u.display_name} ({u.role})"


def _cleanup(telegram_id: int) -> None:
    _admin_states.pop(telegram_id, None)


def _send_fn(token: str, chat_id: int, text: str) -> None:
    """Send message via Telegram API."""
    import httpx
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": chat_id, "text": text,
                              "disable_web_page_preview": True}, timeout=10.0)
    except Exception:
        pass


def handle_admin_message(token: str, telegram_id: int, chat_id: int, text: str) -> bool:
    """Handle a message from the admin. Returns True if handled."""
    if not settings.ADMIN_PHONE_NUMBER:
        return False

    # If they're still in the verification flow but typed a text message
    # instead of sharing a contact, prompt them again.
    if telegram_id in _pending_admin_verify:
        _send_fn(token, chat_id, (
            "[M] Please share your contact to verify admin access.\n\n"
            "Tap the button below or send /cancel to abort."
        ))
        return True

    if not is_verified_admin(telegram_id):
        return False

    state = _admin_states.get(telegram_id)

    # ── /cancel at any time ──────────────────────────────────────────────
    if text.lower() in ("/cancel", "cancel", "exit", "quit"):
        if state:
            _cleanup(telegram_id)
            _send_fn(token, chat_id, "[OK] Operation cancelled.")
        return True

    # ── /create command ──────────────────────────────────────────────────
    if text.lower().startswith("/create"):
        _admin_states[telegram_id] = {"step": "role", "data": {}}
        _evict_admin_states_if_full()
        _send_fn(token, chat_id, (
            "[USER] Create New Staff Account\n\n"
            "What is the role?\n"
            "1. Doctor\n"
            "2. Nurse\n"
            "3. Staff / Intern\n\n"
            "Type the number or role name:"
        ))
        return True

    # ── /list command ────────────────────────────────────────────────────
    if text.lower().startswith("/list"):
        s = SessionLocal()
        try:
            users = s.query(User).filter(
                User.hospital_code == settings.HOSPITAL_CODE,
            ).order_by(User.role, User.created_at).all()
            if not users:
                _send_fn(token, chat_id, "No staff accounts found.")
                return True
            # Build reporting structure: doctors first, then their reports.
            doctors = [u for u in users if u.role == "doctor"]
            nurses = [u for u in users if u.role == "nurse"]
            staff = [u for u in users if u.role == "staff"]
            admins = [u for u in users if u.role == "admin"]
            role_icons = {"admin": "[A]", "doctor": "[D]", "nurse": "[N]", "staff": "[S]"}
            lines = ["[+] Staff Accounts (reporting hierarchy):\n"]
            for group in (admins, doctors, nurses, staff):
                for u in group:
                    icon = role_icons.get(u.role, "[?]")
                    ward = f" [{u.ward}]" if u.ward else ""
                    sup = f" → @{u.supervisor}" if u.supervisor else ""
                    tg = f" [TG:{u.telegram_id}]" if u.telegram_id else " [TG:unlinked]"
                    lines.append(f"{icon} {u.display_name} (@{u.username}) — {u.role.upper()}{ward}{sup}{tg}")
            lines.append(f"\nTotal: {len(users)} (doctors: {len(doctors)}, nurses: {len(nurses)}, staff: {len(staff)}, admins: {len(admins)})")
            _send_fn(token, chat_id, "\n".join(lines))
        finally:
            s.close()
        return True

    # ── /delete command ──────────────────────────────────────────────────
    if text.lower().startswith("/delete"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            _send_fn(token, chat_id, "Usage: /delete <username>")
            return True
        username = parts[1].strip().lower()
        s = SessionLocal()
        try:
            target = s.query(User).filter(
                User.username == username,
                User.hospital_code == settings.HOSPITAL_CODE,
            ).first()
            if not target:
                _send_fn(token, chat_id, f"[X] User '@{username}' not found.")
                return True
            if target.role == "admin":
                _send_fn(token, chat_id, "[X] Cannot delete admin account.")
                return True
            display = target.display_name
            s.delete(target)
            s.commit()
            _send_fn(token, chat_id, f"[OK] Deleted {display} (@{username}).")
        finally:
            s.close()
        return True

    # ── /help command ────────────────────────────────────────────────────
    if text.lower().startswith("/adminhelp"):
        _send_fn(token, chat_id, (
            "[CFG] Admin Commands:\n\n"
            "/create — Create a new staff account\n"
            "/list — List all staff accounts\n"
            "/delete <username> — Remove a staff account\n"
            "/adminhelp — Show this help\n"
            "/cancel — Cancel current operation"
        ))
        return True

    if state:
        return _handle_conversation(token, telegram_id, chat_id, text, state)

    return False


def _handle_conversation(
    token: str, telegram_id: int, chat_id: int, text: str, state: dict
) -> bool:
    """Drive the user creation conversation."""
    step = state["step"]
    data = state["data"]

    if step == "role":
        role_map = {"1": "doctor", "2": "nurse", "3": "staff",
                     "doctor": "doctor", "nurse": "nurse", "staff": "staff",
                     "intern": "staff"}
        role = role_map.get(text.strip().lower())
        if not role:
            _send_fn(token, chat_id, "Invalid choice. Type 1, 2, or 3:")
            return True
        data["role"] = role
        role_info = VALID_ROLES[role]
        state["step"] = "username"
        _send_fn(token, chat_id, (
            f"[OK] Role: {role_info['label']}\n\n"
            "Choose a username (lowercase, alphanumeric, dots, hyphens):\n"
            "Example: dr.priya, nurse.kavitha"
        ))
        return True

    if step == "username":
        username = text.strip().lower()
        if not re.match(r"^[a-z][a-z0-9._-]{2,49}$", username):
            _send_fn(token, chat_id, (
                "Invalid username. Rules:\n"
                "- Start with a letter\n"
                "- Lowercase letters, numbers, dots, hyphens, underscores\n"
                "- 3-50 characters\n\n"
                "Try again:"
            ))
            return True
        s = SessionLocal()
        try:
            existing = s.query(User).filter(User.username == username).first()
            if existing:
                _send_fn(token, chat_id, f"[X] Username '@{username}' already taken. Try another:")
                return True
        finally:
            s.close()
        data["username"] = username
        role_info = VALID_ROLES[data["role"]]
        field_name, field_prompt = role_info["fields"][0]
        state["step"] = f"field_{field_name}"
        state["field_index"] = 0
        _send_fn(token, chat_id, f"[OK] Username: @{username}\n\n{field_prompt}:")
        return True

    if step.startswith("field_"):
        field_name = step.replace("field_", "")
        role_info = VALID_ROLES[data["role"]]
        idx = state.get("field_index", 0)

        if field_name == "supervisor":
            # Supervisor is REQUIRED for nurse/staff, must reference an
            # existing doctor/nurse in the same hospital.
            value = text.strip().lstrip("@").lower()
            s = SessionLocal()
            try:
                ok, msg = _validate_supervisor(s, value)
            finally:
                s.close()
            if not ok:
                _send_fn(token, chat_id, msg + "\n\nTry again with a valid doctor/nurse username:")
                return True
            data[field_name] = value
        else:
            if not text.strip() or text.strip().lower() in ("skip", "-"):
                _send_fn(token, chat_id, "This field is required. Please enter a value:")
                return True
            data[field_name] = text.strip()

        idx += 1
        if idx < len(role_info["fields"]):
            next_field, next_prompt = role_info["fields"][idx]
            state["step"] = f"field_{next_field}"
            state["field_index"] = idx
            _send_fn(token, chat_id, f"[OK] {field_name}: {data[field_name]}\n\n{next_prompt}:")
        else:
            password = generate_password()
            data["password"] = password

            lines = [
                "[+] Account Summary:\n",
                f"Username: @{data['username']}",
                f"Name: {data['display_name']}",
                f"Role: {VALID_ROLES[data['role']]['label']}",
            ]
            if data.get("department"):
                lines.append(f"Department: {data['department']}")
            if data.get("ward"):
                lines.append(f"Ward: {data['ward']}")
            if data.get("supervisor"):
                lines.append(f"Supervisor: {data['supervisor']}")
            lines.append(f"\n[K] Password: {password}")
            lines.append("\n[!] Save this password — it won't be shown again.")
            lines.append("\nType 'yes' to create, 'no' to cancel:")

            state["step"] = "confirm"
            _send_fn(token, chat_id, "\n".join(lines))
        return True

    if step == "confirm":
        if text.strip().lower() not in ("yes", "y", "confirm", "create"):
            _cleanup(telegram_id)
            _send_fn(token, chat_id, "[X] Account creation cancelled.")
            return True

        s = SessionLocal()
        try:
            new_user = User(
                hospital_code=settings.HOSPITAL_CODE,
                username=data["username"],
                display_name=data["display_name"],
                password_hash=hash_password(data["password"]),
                role=data["role"],
                ward=data.get("ward") or data.get("department"),
                supervisor=data.get("supervisor"),
                created_at=now_utc(),
            )
            s.add(new_user)
            s.commit()

            _cleanup(telegram_id)
            _send_fn(token, chat_id, (
                f"[OK] Account created successfully!\n\n"
                f"Username: @{data['username']}\n"
                f"Password: {data['password']}\n"
                f"Role: {VALID_ROLES[data['role']]['label']}\n\n"
                f"Share these credentials with the staff member."
            ))
        except Exception as e:
            s.rollback()
            _cleanup(telegram_id)
            _send_fn(token, chat_id, f"[X] Failed to create account: {e}")
        finally:
            s.close()
        return True

    _cleanup(telegram_id)
    return False
