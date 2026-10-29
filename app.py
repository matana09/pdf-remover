"""שרת מקומי בלבד — מאזין על 127.0.0.1, אפס קריאות רשת יוצאות."""
from __future__ import annotations

import io
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from threading import Timer

import fitz
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

import auth
import redactor

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

STATIC = Path(__file__).parent / "static"
PORT = 8477
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB

# רק כתובות מקומיות מותרות ב-Host. חוסם DNS rebinding: אתר זדוני שפתוח
# בדפדפן לא יוכל לפנות לשרת דרך דומיין שמצביע ל-127.0.0.1.
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"}

AUTH_COOKIE = "pdfr_auth"

# נתיבים שאינם דורשים התחברות: הדף עצמו (מכיל רק את מסך ההתחברות עד
# שמתחברים), קבצי static (ללא מידע רגיש), ונקודות ה-auth וה-health.
PUBLIC_PATHS = {
    "/", "/app.js", "/style.css", "/health",
    "/auth/status", "/auth/setup", "/auth/login", "/auth/logout",
}


@app.middleware("http")
async def security_gate(request: Request, call_next):
    host = request.headers.get("host", "")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "גישה מותרת מהמחשב המקומי בלבד"}, status_code=403)
    # חוסם דפים חיצוניים מלשלוח בקשות לשרת (CSRF)
    origin = request.headers.get("origin")
    if origin and origin not in {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}:
        return JSONResponse({"detail": "מקור בקשה לא מורשה"}, status_code=403)

    # אימות: כל מה שאינו ציבורי דורש טוקן התחברות תקף
    if request.url.path not in PUBLIC_PATHS:
        if not auth.token_valid(request.cookies.get(AUTH_COOKIE)):
            return JSONResponse({"detail": "נדרשת התחברות"}, status_code=401)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # קריטי: אוסר על הדפדפן לכתוב תגובות לדיסק. בלי זה, תמונות העמודים
    # (תוכן המסמך המלא!) היו נשמרות ב-disk cache של הדפדפן.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    # אוסר על הדף לטעון או לשלוח משאבים כלשהם מחוץ למקור עצמו
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "form-action 'none'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )
    return response

# סשנים בזיכרון בלבד — שום דבר לא נכתב לדיסק חוץ מהפלט שהמשתמש מוריד
SESSIONS: dict[str, dict] = {}
MAX_SESSIONS = 5
# השרת רץ ברקע כל הזמן (autostart), ולכן מסמך שנשכח לא נשאר בזיכרון לנצח.
SESSION_TTL_SECONDS = 30 * 60


def _drop(session_id: str):
    sess = SESSIONS.pop(session_id, None)
    if sess:
        try:
            sess["doc"].close()
        except Exception:
            pass


def purge_expired():
    """מוחק סשנים שלא היתה בהם פעילות מעל SESSION_TTL_SECONDS."""
    now = time.time()
    for sid in [s for s, v in SESSIONS.items() if now - v["touched"] > SESSION_TTL_SECONDS]:
        _drop(sid)


def touch(session_id: str) -> dict:
    """מאחזר סשן פעיל ומעדכן את זמן השימוש האחרון."""
    purge_expired()
    sess = SESSIONS.get(session_id)
    if not sess:
        raise HTTPException(404, "הסשן פג או אינו קיים. יש להעלות את הקובץ מחדש.")
    sess["touched"] = time.time()
    return sess


class RedactRequest(BaseModel):
    session: str
    areas: list[dict]          # [{"page": int, "rects": [[x0,y0,x1,y1],...]}]
    forbidden_values: list[str] = []   # ערכים לאימות שנמחקו
    rasterize: bool = False
    black_boxes: bool = False          # ברירת מחדל: אזור ריק, לא מלבן שחור


class SearchRequest(BaseModel):
    session: str
    query: str


class PasswordRequest(BaseModel):
    password: str


def _auth_cookie_response(payload: dict) -> JSONResponse:
    """תשובה עם cookie התחברות. HttpOnly — JS בדף אינו יכול לקרוא אותו;
    SameSite=Strict — הדפדפן לא ישלח אותו מבקשות שמקורן באתר אחר."""
    resp = JSONResponse(payload)
    resp.set_cookie(
        AUTH_COOKIE, auth.issue_token(),
        httponly=True, samesite="strict", path="/",
    )
    return resp


@app.get("/auth/status")
def auth_status(request: Request):
    return {
        "password_set": auth.password_is_set(),
        "authenticated": auth.token_valid(request.cookies.get(AUTH_COOKIE)),
        "lockout_seconds": auth.lockout_remaining(),
    }


@app.post("/auth/setup")
def auth_setup(req: PasswordRequest):
    """קביעת סיסמה בכניסה הראשונה. נחסם אם כבר קיימת סיסמה."""
    try:
        auth.set_password(req.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _auth_cookie_response({"ok": True})


@app.post("/auth/login")
def auth_login(req: PasswordRequest):
    if not auth.password_is_set():
        raise HTTPException(400, "לא הוגדרה סיסמה")
    remaining = auth.lockout_remaining()
    if remaining > 0:
        raise HTTPException(429, f"יותר מדי ניסיונות. נסה שוב בעוד {remaining} שניות.")
    if not auth.verify_password(req.password):
        remaining = auth.lockout_remaining()
        if remaining > 0:
            raise HTTPException(429, f"יותר מדי ניסיונות. נסה שוב בעוד {remaining} שניות.")
        raise HTTPException(401, "סיסמה שגויה")
    return _auth_cookie_response({"ok": True})


@app.post("/auth/logout")
def auth_logout(request: Request):
    auth.revoke_token(request.cookies.get(AUTH_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(STATIC / "app.js")


@app.get("/style.css")
def style_css():
    return FileResponse(STATIC / "style.css")


@app.post("/upload")
async def upload(request: Request):
    """מקבל קובץ כגוף בקשה גולמי, ישירות לזיכרון.

    בכוונה לא UploadFile של FastAPI: הוא משתמש ב-SpooledTemporaryFile,
    שכותב קבצים מעל 1MB לתיקיית ה-temp בדיסק — הפרה של הדרישה ששום
    קובץ לא יישמר בשום צורה. הזרימה כאן נשארת בזיכרון בלבד.

    כותרת X-Session: אם נשלחת ותקפה, עמודי הקובץ ממוזגים לסוף המסמך
    הקיים בסשן (מיזוג כמה מסמכים למסמך אחד). אחרת נפתח סשן חדש.
    """
    data = b""
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "הקובץ גדול מדי (מקסימום 100MB)")

    from urllib.parse import unquote
    filename = unquote(request.headers.get("x-filename", "")) or "document.pdf"
    append_to = request.headers.get("x-session", "").strip()

    if append_to:
        # --- מיזוג לסשן קיים ---
        sess = touch(append_to)
        if len(sess["bytes"]) + len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "המסמך הממוזג גדול מדי (מקסימום 100MB)")
        start = sess["doc"].page_count
        try:
            # עמודים שכבר נמצאים במסמך מדולגים — מיזוג של אותו קובץ
            # פעמיים לא מוסיף אותו שוב
            result = redactor.merge_into(
                sess["doc"], data,
                existing_sigs={s for s in sess["page_sigs"] if s})
        except Exception:
            raise HTTPException(400, f'"{filename}" אינו PDF תקין')
        added, skipped, new_sigs = result["added"], result["skipped"], result["sigs"]
        session_id = append_to
    else:
        # --- סשן חדש ---
        try:
            doc = redactor.open_doc(data)
        except Exception:
            raise HTTPException(400, f'"{filename}" אינו PDF תקין')
        if doc.page_count == 0:
            # בלי זה היה נפתח סשן ריק שאין ממנו מה להפיק
            doc.close()
            raise HTTPException(400, f'"{filename}" אינו מכיל עמודים')

        # הגבלת זיכרון: סשנים שפגו, ואחריהם אלה שלא נגעו בהם הכי הרבה זמן.
        # לפי touched ולא לפי סדר היצירה — אחרת סשן שהמשתמש עובד עליו
        # ברגע זה היה נמחק לטובת העלאה חדשה, רק מפני שנפתח קודם.
        purge_expired()
        while len(SESSIONS) >= MAX_SESSIONS:
            _drop(min(SESSIONS, key=lambda sid: SESSIONS[sid]["touched"]))

        session_id = secrets.token_hex(16)
        start, added, skipped = 0, doc.page_count, []
        # הקובץ הראשון נכנס כמות שהוא, כולל עמודים שחוזרים בתוכו —
        # הסינון מתחיל רק מהמיזוג השני והלאה
        new_sigs = redactor.page_signatures(doc)
        sess = SESSIONS[session_id] = {
            "doc": doc, "bytes": b"",
            "name": filename,
            "sources": [],
            "pages": [],
            "candidates": [],
            "page_sigs": [],
            "touched": time.time(),
        }

    # ניתוח רק העמודים החדשים, וצבירה למצב הסשן
    if added:
        info = redactor.analyze(sess["doc"], start_page=start)
        sess["pages"].extend(info["pages"])
        sess["candidates"].extend(info["candidates"])
        sess["page_sigs"].extend(new_sigs)
        sess["sources"].append({"name": filename, "start": start, "pages": added})
        # הבייטים המעודכנים משמשים ב-/redact כעותק טרי לעבודה
        sess["bytes"] = sess["doc"].tobytes()

    return {
        "session": session_id,
        "filename": filename,
        "added": added,
        # מספרי העמודים בקובץ שהועלה (1-based) שכבר היו במסמך ולכן דולגו
        "skipped_pages": [i + 1 for i in skipped],
        "sources": sess["sources"],
        "pages": sess["pages"],
        "candidates": sess["candidates"],
    }


@app.get("/page/{session_id}/{page_index}")
def page_png(session_id: str, page_index: int):
    sess = touch(session_id)
    if not 0 <= page_index < sess["doc"].page_count:
        raise HTTPException(404, "עמוד לא קיים")
    png = redactor.render_page_png(sess["doc"], page_index)
    return Response(content=png, media_type="image/png")


@app.post("/search")
def search(req: SearchRequest):
    sess = touch(req.session)
    if not req.query.strip():
        return {"results": []}
    return {"results": redactor.search_text(sess["doc"], req.query.strip())}


@app.post("/redact")
def redact(req: RedactRequest):
    """מייצר את קובץ הפלט.

    רשימת areas ריקה מותרת בכוונה: כך אפשר למזג מסמכים (או לנקות מטאדאטה
    ממסמך יחיד) גם בלי לסמן דבר להסרה.
    """
    sess = touch(req.session)

    # עובדים על עותק טרי מהבייטים המקוריים
    doc = redactor.open_doc(sess["bytes"])
    try:
        out_bytes = redactor.apply_redactions(
            doc, req.areas, rasterize=req.rasterize, black_boxes=req.black_boxes)
    finally:
        doc.close()

    # האימות נעשה על האזורים עצמם בקובץ הסופי — לא על ערכים שהדפדפן שלח
    report = redactor.verify(out_bytes, req.areas, search_terms=req.forbidden_values)
    sess["output"] = out_bytes
    return {"report": report, "size": len(out_bytes)}


@app.get("/download/{session_id}")
def download(session_id: str):
    sess = touch(session_id)
    if "output" not in sess:
        raise HTTPException(404, "אין קובץ מוכן להורדה")
    if len(sess.get("sources", [])) > 1:
        name = f"merged_{len(sess['sources'])}_files_redacted.pdf"
    else:
        name = Path(sess["name"]).stem + "_redacted.pdf"
    return Response(
        content=sess["output"],
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name.encode("ascii", "ignore").decode() or "redacted.pdf"}"; filename*=UTF-8\'\'{_quote(name)}'},
    )


@app.post("/cleanup/{session_id}")
def cleanup(session_id: str):
    _drop(session_id)
    return {"ok": True}


@app.get("/health")
def health():
    """נקודת בדיקה ל-watchdog ולסקריפט ה-restart."""
    purge_expired()
    return {"ok": True, "app": "pdf-redactor", "sessions": len(SESSIONS)}


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


def _port_owner() -> str | None:
    """מחזיר תיאור התהליך שתופס את הפורט, אם הוא תפוס."""
    import socket
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", PORT))
        return None
    except OSError:
        return f"פורט {PORT} תפוס"
    finally:
        s.close()


if __name__ == "__main__":
    import uvicorn

    busy = _port_owner()
    if busy:
        # קורה כששרת PDF Redactor אחר כבר רץ (למשל דרך ה-Scheduled Task).
        # יציאה נקייה במקום קריסה — כדי שה-watchdog לא ינסה שוב ושוב.
        print(f"[PDF Redactor] {busy}. סביר שהשרת כבר פועל — פתח http://127.0.0.1:{PORT}")
        if "--no-browser" not in sys.argv:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        sys.exit(0)

    # ברקע (autostart) אין טעם לפתוח דפדפן
    if "--no-browser" not in sys.argv:
        Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    # 127.0.0.1 בלבד — אין גישה מהרשת
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
