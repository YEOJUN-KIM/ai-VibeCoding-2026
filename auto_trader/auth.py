"""개인용 관리자 로그인, 세션, CSRF 보호."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status

from .database import connect
from .settings import settings


SESSION_COOKIE = "auto_trader_session"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    username: str
    csrf_token: str
    expires_at: datetime


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("비밀번호는 12자 이상 128자 이하로 입력하세요.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return "$".join(
        (
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def hash_live_pin(pin: str) -> str:
    if len(pin) != 6 or not pin.isdigit():
        raise ValueError("LIVE PIN은 숫자 6자리여야 합니다.")
    # 로그인 비밀번호와 같은 강도의 해시를 사용하되 입력 규칙만 별도로 둔다.
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "$".join(("scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
                     base64.urlsafe_b64encode(salt).decode("ascii"),
                     base64.urlsafe_b64encode(digest).decode("ascii")))


def set_live_pin(pin: str) -> None:
    encoded = hash_live_pin(pin)
    with connect() as conn:
        user = conn.execute("SELECT id FROM admin_users ORDER BY id LIMIT 1 FOR UPDATE").fetchone()
        if not user:
            raise ValueError("먼저 관리자 계정을 생성하세요.")
        conn.execute(
            """UPDATE admin_users SET live_pin_hash=%s,pin_failed_attempts=0,pin_locked_until=NULL
               WHERE id=%s""", (encoded, user["id"])
        )
        conn.execute("UPDATE auth_sessions SET live_authorized_until=NULL WHERE user_id=%s", (user["id"],))


def live_pin_status(request: Request, user: AuthenticatedUser) -> tuple[bool, datetime | None]:
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with connect() as conn:
        row = conn.execute(
            """SELECT u.live_pin_hash,s.live_authorized_until FROM auth_sessions s
               JOIN admin_users u ON u.id=s.user_id WHERE s.token_hash=%s AND u.id=%s""",
            (token_hash, user.id),
        ).fetchone()
    if not row:
        return False, None
    until = row["live_authorized_until"]
    return bool(row["live_pin_hash"]), until if until and until > datetime.now(timezone.utc) else None


def verify_live_pin(request: Request, user: AuthenticatedUser, pin: str) -> datetime:
    now = datetime.now(timezone.utc)
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with connect() as conn:
        row = conn.execute("SELECT * FROM admin_users WHERE id=%s FOR UPDATE", (user.id,)).fetchone()
        if not row or not row["live_pin_hash"]:
            raise ValueError("LIVE PIN이 설정되지 않았습니다.")
        if row["pin_locked_until"] and row["pin_locked_until"] > now:
            raise PermissionError("PIN 입력이 잠겼습니다. 잠시 후 다시 시도하세요.")
        if not verify_password(pin, row["live_pin_hash"]):
            failures = row["pin_failed_attempts"] + 1
            locked_until = None
            if failures >= settings.login_max_failures:
                locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                failures = 0
            conn.execute("UPDATE admin_users SET pin_failed_attempts=%s,pin_locked_until=%s WHERE id=%s",
                         (failures, locked_until, user.id))
            raise PermissionError("LIVE PIN이 올바르지 않습니다.")
        authorized_until = now + timedelta(minutes=5)
        updated = conn.execute(
            """UPDATE auth_sessions SET live_authorized_until=%s
               WHERE token_hash=%s AND user_id=%s RETURNING id""",
            (authorized_until, token_hash, user.id),
        ).fetchone()
        if not updated:
            raise PermissionError("로그인 세션이 유효하지 않습니다.")
        conn.execute("UPDATE admin_users SET pin_failed_attempts=0,pin_locked_until=NULL WHERE id=%s", (user.id,))
    return authorized_until


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def create_admin(username: str, password: str) -> None:
    username = username.strip()
    if not 3 <= len(username) <= 50:
        raise ValueError("관리자 아이디는 3자 이상 50자 이하로 입력하세요.")
    password_hash = hash_password(password)
    with connect() as conn:
        conn.execute("LOCK TABLE admin_users IN EXCLUSIVE MODE")
        count = conn.execute("SELECT count(*) AS count FROM admin_users").fetchone()["count"]
        if count:
            raise ValueError("관리자 계정이 이미 존재합니다.")
        conn.execute(
            "INSERT INTO admin_users(username, password_hash) VALUES (%s, %s)",
            (username, password_hash),
        )


def authenticate(username: str, password: str) -> tuple[str, AuthenticatedUser] | None:
    now = datetime.now(timezone.utc)
    with connect() as conn:
        user = conn.execute(
            "SELECT * FROM admin_users WHERE username=%s FOR UPDATE", (username.strip(),)
        ).fetchone()
        if not user:
            hashlib.scrypt(
                password.encode("utf-8"), salt=b"unknown-user-salt", n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
            )
            return None
        if user["locked_until"] and user["locked_until"] > now:
            return None
        if not verify_password(password, user["password_hash"]):
            failures = user["failed_attempts"] + 1
            locked_until = None
            if failures >= settings.login_max_failures:
                locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                failures = 0
            conn.execute(
                "UPDATE admin_users SET failed_attempts=%s, locked_until=%s WHERE id=%s",
                (failures, locked_until, user["id"]),
            )
            return None

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        csrf_token = secrets.token_urlsafe(24)
        expires_at = now + timedelta(minutes=settings.session_minutes)
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= now()")
        conn.execute(
            "UPDATE admin_users SET failed_attempts=0, locked_until=NULL, last_login_at=now() WHERE id=%s",
            (user["id"],),
        )
        conn.execute(
            "INSERT INTO auth_sessions(user_id, token_hash, csrf_token, expires_at) VALUES (%s,%s,%s,%s)",
            (user["id"], token_hash, csrf_token, expires_at),
        )
        return raw_token, AuthenticatedUser(user["id"], user["username"], csrf_token, expires_at)


def session_from_request(request: Request) -> AuthenticatedUser | None:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with connect() as conn:
        row = conn.execute(
            """SELECT u.id, u.username, s.csrf_token, s.expires_at
               FROM auth_sessions s JOIN admin_users u ON u.id=s.user_id
               WHERE s.token_hash=%s AND s.expires_at > now()""",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE auth_sessions SET last_seen_at=now() WHERE token_hash=%s", (token_hash,))
        return AuthenticatedUser(row["id"], row["username"], row["csrf_token"], row["expires_at"])


def require_user(request: Request) -> AuthenticatedUser:
    user = session_from_request(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    return user


def require_csrf(
    request: Request, user: AuthenticatedUser = Depends(require_user)
) -> AuthenticatedUser:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not hmac.compare_digest(supplied, user.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="보안 토큰이 올바르지 않습니다.")
    return user


def delete_session(request: Request) -> None:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    with connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=%s", (token_hash,))
