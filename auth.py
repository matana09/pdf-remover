"""אימות סיסמה למערכת.

עקרונות:
- הסיסמה עצמה לא נשמרת בשום מקום, בשום צורה. נשמר רק hash מסוג
  PBKDF2-HMAC-SHA256 עם 600,000 איטרציות ו-salt אקראי — לא ניתן לשחזר
  ממנו את הסיסמה.
- קובץ ה-hash יושב ב-%LOCALAPPDATA%\\PdfRedactor (מוגן ב-ACL של המשתמש,
  מחוץ לתיקיית הפרויקט) ומסומן כקובץ מוסתר.
- טוקני התחברות חיים בזיכרון בלבד ומתים עם עצירת השרת.
- נעילה אחרי 5 ניסיונות כושלים, עם הכפלת זמן ההמתנה בכל כישלון נוסף.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

PBKDF2_ITERATIONS = 600_000
MIN_PASSWORD_LEN = 8

AUTH_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PdfRedactor"
AUTH_FILE = AUTH_DIR / "auth.json"

# טוקנים פעילים: token -> זמן פעילות אחרון. בזיכרון בלבד.
_TOKENS: dict[str, float] = {}
TOKEN_TTL_SECONDS = 8 * 3600

# הגנת brute force
MAX_FAILURES_BEFORE_LOCK = 5
_failed_attempts = 0
_locked_until = 0.0


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def password_is_set() -> bool:
    return AUTH_FILE.exists()


def set_password(password: str) -> None:
    """קביעת סיסמה ראשונית. מותר רק כשאין סיסמה קיימת."""
    if password_is_set():
        raise ValueError("סיסמה כבר קיימת")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"הסיסמה חייבת להכיל לפחות {MIN_PASSWORD_LEN} תווים")

    salt = secrets.token_bytes(32)
    record = {
        "algo": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "hash": _hash(password, salt).hex(),
    }
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(record), encoding="utf-8")
    _hide_file(AUTH_FILE)


def verify_password(password: str) -> bool:
    """אימות סיסמה בהשוואה קבועת-זמן. כולל מנגנון נעילה."""
    global _failed_attempts, _locked_until

    remaining = lockout_remaining()
    if remaining > 0:
        return False

    try:
        record = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        salt = bytes.fromhex(record["salt"])
        expected = bytes.fromhex(record["hash"])
    except Exception:
        return False

    ok = hmac.compare_digest(_hash(password, salt), expected)
    if ok:
        _failed_attempts = 0
        _locked_until = 0.0
    else:
        _failed_attempts += 1
        if _failed_attempts >= MAX_FAILURES_BEFORE_LOCK:
            # 30 שניות, ומכפיל בכל כישלון נוסף: 30, 60, 120...
            factor = 2 ** (_failed_attempts - MAX_FAILURES_BEFORE_LOCK)
            _locked_until = time.time() + 30 * factor
    return ok


def lockout_remaining() -> int:
    """שניות שנותרו לנעילה, או 0 אם אין נעילה."""
    return max(0, int(_locked_until - time.time()))


def issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _TOKENS[token] = time.time()
    return token


def token_valid(token: str | None) -> bool:
    if not token:
        return False
    now = time.time()
    # ניקוי טוקנים שפגו
    for t in [t for t, ts in _TOKENS.items() if now - ts > TOKEN_TTL_SECONDS]:
        _TOKENS.pop(t, None)
    if token in _TOKENS:
        _TOKENS[token] = now
        return True
    return False


def revoke_token(token: str | None) -> None:
    if token:
        _TOKENS.pop(token, None)


def _hide_file(path: Path) -> None:
    """מסמן את הקובץ כמוסתר ב-Windows. כישלון כאן אינו קריטי."""
    try:
        import ctypes
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass
