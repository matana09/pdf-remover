"""בדיקות אבטחה מקיפות. הרצה: venv\\Scripts\\python.exe tests\\test_security.py

דורש שהשרת ירוץ (המשימה המתוזמנת או run.bat).
משתמש בסיסמה זמנית: אם אין קובץ auth — קובע סיסמת בדיקה ומוחק אותה בסוף.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import auth as auth_mod  # noqa: E402

BASE = "http://127.0.0.1:8477"
FAILS = []
TEST_PASSWORD = "Test-Sec-9911!"

# auth.json קיים לפני הבדיקות? אם לא — ניצור זמני ונמחק בסוף.
CREATED_AUTH = not auth_mod.AUTH_FILE.exists()


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def req(path, data=None, headers=None, method=None, opener=None, raw_body=None):
    """מחזיר (status, body_dict_or_bytes, response_headers)."""
    h = {"Content-Type": "application/json"} if data is not None else {}
    h.update(headers or {})
    body = raw_body if raw_body is not None else (
        json.dumps(data).encode() if data is not None else None)
    r = urllib.request.Request(BASE + path, data=body, headers=h, method=method)
    op = opener or urllib.request.build_opener()
    try:
        resp = op.open(r, timeout=10)
        raw = resp.read()
        try:
            return resp.status, json.loads(raw), dict(resp.headers)
        except Exception:
            return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except Exception:
            return e.code, raw, dict(e.headers)


def authed_opener():
    """יוצר opener עם cookie התחברות תקף."""
    jar = CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    st, body, _ = req("/auth/login", {"password": TEST_PASSWORD}, opener=op)
    if st != 200:
        raise RuntimeError(f"login failed: {st} {body}")
    return op


print("\n[S1] בסיס: שרת פעיל וסיסמה מוגדרת")
st, body, _ = req("/health")
check("השרת פועל", st == 200 and body.get("ok"))

if CREATED_AUTH:
    st, body, _ = req("/auth/setup", {"password": TEST_PASSWORD})
    check("קביעת סיסמה ראשונית", st == 200)
else:
    print("  INFO  קיימת סיסמה אמיתית — בדיקות ההתחברות ירוצו מולה בלי לדעת אותה.")

print("\n[S2] כל ה-endpoints הרגישים חסומים ללא התחברות")
for path, method, data in [
    ("/upload", "POST", None),
    ("/page/abc/0", "GET", None),
    ("/search", "POST", {"session": "abc", "query": "x"}),
    ("/redact", "POST", {"session": "abc", "areas": []}),
    ("/download/abc", "GET", None),
    ("/cleanup/abc", "POST", None),
]:
    st, _, _ = req(path, data=data, method=method,
                   raw_body=b"" if (method == "POST" and data is None) else None)
    check(f"{method} {path} מחזיר 401", st == 401, f"got {st}")

print("\n[S3] setup חוזר נחסם כשכבר קיימת סיסמה")
st, _, _ = req("/auth/setup", {"password": "AnotherPass123"})
check("אי אפשר לדרוס סיסמה קיימת", st == 400, f"got {st}")

print("\n[S4] קובץ הסיסמה אינו מכיל את הסיסמה")
raw = auth_mod.AUTH_FILE.read_bytes()
check("הקובץ קיים", auth_mod.AUTH_FILE.exists())
if CREATED_AUTH:
    check("הסיסמה אינה בקובץ", TEST_PASSWORD.encode() not in raw)
rec = json.loads(raw)
check("נשמר רק hash+salt", set(rec) == {"algo", "iterations", "salt", "hash"})
check("PBKDF2 עם 600k איטרציות", rec["iterations"] >= 600_000)
check("הקובץ מחוץ לתיקיית הפרויקט", ROOT not in auth_mod.AUTH_FILE.parents)
import ctypes
attrs = ctypes.windll.kernel32.GetFileAttributesW(str(auth_mod.AUTH_FILE))
check("הקובץ מוסתר", attrs != -1 and bool(attrs & 0x02))

if CREATED_AUTH:
    print("\n[S5] סיסמה שגויה ונעילת brute force")
    st, _, _ = req("/auth/login", {"password": "wrong-password"})
    check("סיסמה שגויה נדחית", st == 401, f"got {st}")
    # עוד 4 כישלונות → נעילה
    for _ in range(4):
        req("/auth/login", {"password": "wrong-password"})
    st, body, _ = req("/auth/login", {"password": "wrong-password"})
    check("נעילה אחרי 5 כישלונות", st == 429, f"got {st}")
    st, _, _ = req("/auth/login", {"password": TEST_PASSWORD})
    check("גם הסיסמה הנכונה חסומה בזמן נעילה", st == 429, f"got {st}")
    print("  INFO  ממתין לפקיעת הנעילה (עד 35 שניות)...")
    deadline = time.time() + 40
    ok = False
    while time.time() < deadline:
        st, _, _ = req("/auth/status")
        _, body, _ = req("/auth/status")
        if body.get("lockout_seconds", 1) == 0:
            ok = True
            break
        time.sleep(2)
    st, _, _ = req("/auth/login", {"password": TEST_PASSWORD})
    check("התחברות תקינה אחרי הנעילה", ok and st == 200, f"got {st}")

    print("\n[S6] זרימה מאומתת + כותרות אבטחה")
    op = authed_opener()
    pdf = (ROOT / "tests" / "sample_payslip.pdf").read_bytes()
    st, up, _ = req("/upload", opener=op, raw_body=pdf, method="POST",
                    headers={"Content-Type": "application/octet-stream",
                             "X-Filename": "payslip.pdf"})
    check("העלאה עם התחברות עובדת", st == 200 and up.get("session"), f"got {st}")
    sid = up["session"]
    check("מזהה סשן באורך 32 hex", len(sid) == 32)

    st, png, hdrs = req(f"/page/{sid}/0", opener=op)
    check("תמונת עמוד נטענת", st == 200 and bytes(png)[:4] == b"\x89PNG")
    # HTTP headers מגיעים ב-lowercase — השוואה חסרת רישיות
    h = {k.lower(): v for k, v in hdrs.items()}
    cc = h.get("cache-control", "")
    check("Cache-Control: no-store על תמונות עמודים", "no-store" in cc, cc)
    check("X-Frame-Options: DENY", h.get("x-frame-options") == "DENY")
    check("Referrer-Policy: no-referrer", h.get("referrer-policy") == "no-referrer")
    csp = h.get("content-security-policy", "")
    check("CSP כולל frame-ancestors 'none'", "frame-ancestors 'none'" in csp)

    st, _, _ = req(f"/page/{sid}/999", opener=op)
    check("page_index מחוץ לטווח מחזיר 404 (לא 500)", st == 404, f"got {st}")

    print("\n[S7] טוקן לא תקף אינו עובד")
    jar = CookieJar()
    bad = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    st, _, _ = req(f"/page/{sid}/0", opener=bad,
                   headers={"Cookie": "pdfr_auth=forged-token-abcdef"})
    check("cookie מזויף נדחה", st == 401, f"got {st}")

    print("\n[S8] logout מבטל את הטוקן")
    st, _, _ = req("/auth/logout", opener=op, method="POST", raw_body=b"")
    check("logout מצליח", st == 200)
    st, _, _ = req(f"/page/{sid}/0", opener=op)
    check("הטוקן מת אחרי logout", st == 401, f"got {st}")

print("\n[S9] קבצים שהועלו אינם נכתבים לדיסק")
app_src = (ROOT / "app.py").read_text(encoding="utf-8")
import_lines = [ln for ln in app_src.splitlines() if ln.startswith(("import ", "from "))]
check("אין import של UploadFile (SpooledTemporaryFile כותב לדיסק)",
      not any("UploadFile" in ln for ln in import_lines))
check("אין tempfile בקוד השרת", "tempfile" not in app_src)
red_src = (ROOT / "redactor.py").read_text(encoding="utf-8")
check("redactor עובד על streams בלבד", "tempfile" not in red_src and '.save(' not in red_src)
# בדיקה דינמית: העלאה של קובץ גדול מ-1MB ובדיקה שלא נוצר קובץ temp חדש
if CREATED_AUTH:
    import os
    big = pdf + b"\n%" + b"P" * (2 * 1024 * 1024)  # מעל סף ה-spool של starlette
    tmp = Path(os.environ["TEMP"])
    before = {p.name for p in tmp.glob("tmp*")}
    op2 = authed_opener()
    st, up2, _ = req("/upload", opener=op2, raw_body=big, method="POST",
                     headers={"Content-Type": "application/octet-stream",
                              "X-Filename": "big.pdf"})
    after = {p.name for p in tmp.glob("tmp*")}
    check("העלאת 2MB+ תקינה", st == 200, f"got {st}")
    check("לא נוצר קובץ temp חדש בהעלאה", after == before, str(after - before))
    if st == 200:
        req(f"/cleanup/{up2['session']}", opener=op2, method="POST", raw_body=b"")

print("\n[S10] הגבלות רשת (קיימות מהשלב הקודם)")
st, _, _ = req("/health", headers={"Host": "evil.example.com"})
check("Host זר נחסם", st == 403, f"got {st}")
st, _, _ = req("/auth/login", {"password": "x"},
               headers={"Origin": "http://evil.example.com"})
check("Origin זר נחסם", st == 403, f"got {st}")

print("\n[S11] לוג השרת אינו מכיל שמות קבצים או תוכן")
log = ROOT / "logs" / "server.log"
if log.exists():
    log_text = log.read_text(encoding="utf-8", errors="replace")
    check("אין שמות PDF בלוג", ".pdf" not in log_text.lower())
    check("אין access log של נתיבים עם session id", "/page/" not in log_text)
else:
    print("  INFO  אין קובץ לוג — דילוג.")

# ניקוי: מחיקת סיסמת הבדיקה אם אנחנו יצרנו אותה
if CREATED_AUTH:
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(auth_mod.AUTH_FILE), 0x80)
        auth_mod.AUTH_FILE.unlink()
        print("\n  INFO  סיסמת הבדיקה הזמנית נמחקה.")
    except Exception as e:
        print(f"\n  WARN  לא הצלחתי למחוק את סיסמת הבדיקה: {e}")

print("\n" + "=" * 50)
if FAILS:
    print(f"נכשלו {len(FAILS)} בדיקות: {FAILS}")
    sys.exit(1)
print("כל בדיקות האבטחה עברו.")
