"""בדיקת שכבת ה-HTTP של מיזוג מסמכים.

מריץ מופע שרת מבודד על פורט 8478 עם אימות מנוטרל, כדי לבדוק את קוד
ה-endpoint האמיתי בלי לגעת בשרת הפעיל או בסיסמה של המשתמש.
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

import app as appmod  # noqa: E402
import auth as auth_mod  # noqa: E402

TEST_PORT = 8478
BASE = f"http://127.0.0.1:{TEST_PORT}"
FAILS = []

# ניטרול האימות במופע הבדיקה בלבד (בתהליך נפרד מהשרת הפעיל)
auth_mod.token_valid = lambda token: True
appmod.ALLOWED_HOSTS = {f"127.0.0.1:{TEST_PORT}", f"localhost:{TEST_PORT}"}


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def post_pdf(path_bytes, filename, session=None):
    headers = {"Content-Type": "application/octet-stream", "X-Filename": filename}
    if session:
        headers["X-Session"] = session
    r = urllib.request.Request(BASE + "/upload", data=path_bytes, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def post_json(path, payload):
    r = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                               headers={"Content-Type": "application/json"}, method="POST")
    try:
        resp = urllib.request.urlopen(r, timeout=60)
        return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def start_server():
    import socket
    import uvicorn

    # אם הפורט תפוס, הבדיקות היו רצות מול שרת אחר (אולי עם קוד ישן)
    # ומדווחות PASS מטעה. עדיף להיכשל מיד.
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", TEST_PORT))
    except OSError:
        raise SystemExit(
            f"פורט {TEST_PORT} תפוס — עצור את התהליך שמאזין עליו והרץ שוב.")
    finally:
        probe.close()

    cfg = uvicorn.Config(appmod.app, host="127.0.0.1", port=TEST_PORT, log_level="error")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/health", timeout=1)
            return server
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("שרת הבדיקה לא עלה")


print("\n[M1] הפעלת שרת בדיקה מבודד")
start_server()
check("שרת הבדיקה פעיל", True)

payslip = (ROOT / "tests" / "sample_payslip.pdf").read_bytes()
statement = (ROOT / "tests" / "sample_statement.pdf").read_bytes()
hebrew = (ROOT / "tests" / "sample_hebrew.pdf").read_bytes()

print("\n[M2] העלאת המסמך הראשון")
st, r1 = post_pdf(payslip, "payslip.pdf")
check("העלאה ראשונה מצליחה", st == 200, f"got {st}")
sid = r1["session"]
check("עמוד אחד במסמך הראשון", len(r1["pages"]) == 1, str(len(r1["pages"])))
check("מקור אחד ברשימה", len(r1["sources"]) == 1)
check("start=0 למקור הראשון", r1["sources"][0]["start"] == 0)
n_cand_1 = len(r1["candidates"])
check("זוהו מועמדים במסמך הראשון", n_cand_1 == 6, str(n_cand_1))

print("\n[M3] מיזוג המסמך השני לאותו סשן")
st, r2 = post_pdf(statement, "statement.pdf", session=sid)
check("מיזוג מצליח", st == 200, f"got {st}")
check("אותו מזהה סשן", r2["session"] == sid)
check("סך העמודים 3", len(r2["pages"]) == 3, str(len(r2["pages"])))
check("שני מקורות ברשימה", len(r2["sources"]) == 2)
check("המקור השני מתחיל בעמוד 1", r2["sources"][1]["start"] == 1, str(r2["sources"][1]))
check("למקור השני 2 עמודים", r2["sources"][1]["pages"] == 2)
check("שמות הקבצים נשמרו",
      [s["name"] for s in r2["sources"]] == ["payslip.pdf", "statement.pdf"])
check("מספרי העמודים רציפים",
      [p["index"] for p in r2["pages"]] == [0, 1, 2])
check("המועמדים מצטברים ולא נמחקים", len(r2["candidates"]) > n_cand_1,
      f"{n_cand_1} -> {len(r2['candidates'])}")
check("אין כפילות מועמדים מהמסמך הראשון",
      sum(1 for c in r2["candidates"] if c["page"] == 0) == n_cand_1)
pages_2 = {c["page"] for c in r2["candidates"] if c["page"] > 0}
check("מועמדים מהמסמך השני בעמודים 1-2", pages_2 == {1, 2}, str(sorted(pages_2)))

print("\n[M4] מיזוג מסמך שלישי (מסמך שונה)")
st, r3 = post_pdf(hebrew, "hebrew.pdf", session=sid)
check("מיזוג שלישי מצליח", st == 200, f"got {st}")
check("סך העמודים 4", len(r3["pages"]) == 4, str(len(r3["pages"])))
check("שלושה מקורות", len(r3["sources"]) == 3)
check("המקור השלישי מתחיל בעמוד 3", r3["sources"][2]["start"] == 3)
check("לא דולג דבר במסמך שונה", r3["skipped_pages"] == [], str(r3["skipped_pages"]))

print("\n[M4ב] מיזוג חוזר של קובץ שכבר במסמך אינו מוסיף עמודים")
before_pages = len(r3["pages"])
before_sources = len(r3["sources"])
st, dup = post_pdf(payslip, "payslip_copy.pdf", session=sid)
check("הבקשה מצליחה (לא שגיאה)", st == 200, f"got {st}")
check("לא נוספו עמודים", dup["added"] == 0, str(dup.get("added")))
check("העמוד הכפול מדווח ללקוח", dup["skipped_pages"] == [1], str(dup["skipped_pages"]))
check("סך העמודים לא השתנה", len(dup["pages"]) == before_pages, str(len(dup["pages"])))
check("לא נוסף מקור לרשימה", len(dup["sources"]) == before_sources, str(len(dup["sources"])))
check("אינדקסי העמודים נשארו רציפים",
      [pg["index"] for pg in dup["pages"]] == list(range(before_pages)))

# אותו תוכן שנשמר מחדש — בייטים שונים לגמרי, ובכל זאת אותו עמוד
resaved = fitz.open(stream=payslip, filetype="pdf").tobytes(garbage=4, deflate=True)
check("(הבייטים אכן שונים מהמקור)", resaved != payslip)
st, dup2 = post_pdf(resaved, "payslip_resaved.pdf", session=sid)
check("עותק שנשמר מחדש מזוהה ככפילות", dup2["added"] == 0, str(dup2.get("added")))
check("סך העמודים עדיין לא השתנה", len(dup2["pages"]) == before_pages)

# קובץ שחלקו כפול וחלקו חדש — רק החדש נכנס
mixed = fitz.open()
mixed.insert_pdf(fitz.open(stream=payslip, filetype="pdf"))
mixed.new_page(width=300, height=300)
mixed[1].insert_text((40, 60), "unique merge marker 7788")
mixed_bytes = mixed.tobytes()
mixed.close()
st, mx = post_pdf(mixed_bytes, "mixed.pdf", session=sid)
check("מקובץ מעורב נוסף רק העמוד החדש", mx["added"] == 1, str(mx.get("added")))
check("העמוד הכפול בקובץ המעורב דולג", mx["skipped_pages"] == [1], str(mx["skipped_pages"]))
check("נוסף עמוד אחד בלבד", len(mx["pages"]) == before_pages + 1, str(len(mx["pages"])))
check("המקור החדש נרשם עם עמוד אחד",
      mx["sources"][-1]["name"] == "mixed.pdf" and mx["sources"][-1]["pages"] == 1,
      str(mx["sources"][-1]))
r3 = mx

print("\n[M5] רינדור עמודים מכל המקורות")
for i in range(5):
    try:
        resp = urllib.request.urlopen(f"{BASE}/page/{sid}/{i}", timeout=15)
        png = resp.read()
        check(f"עמוד {i} מרונדר", resp.status == 200 and png[:4] == b"\x89PNG")
    except Exception as e:
        check(f"עמוד {i} מרונדר", False, str(e))

print("\n[M6] חיפוש חוצה מסמכים")
st, s = post_json("/search", {"session": sid, "query": "Israel Israeli"})
hits = {r["page"] for r in s["results"]}
check("המונח מופיע פעם אחת בלבד — לא הוכפל במיזוג", hits == {0}, str(sorted(hits)))

print("\n[M7] הסרה על המסמך הממוזג")
areas = [{"page": c["page"], "rects": c["rects"]} for c in r3["candidates"]]
areas += [{"page": r["page"], "rects": r["rects"]} for r in s["results"]]
st, red = post_json("/redact", {"session": sid, "areas": areas,
                                "forbidden_values": ["Israel Israeli"],
                                "rasterize": False, "black_boxes": False})
check("ההסרה מצליחה", st == 200, f"got {st}")
check("דוח האימות נקי", red["report"]["clean"],
      f"dirty={red['report'].get('dirty_regions')} terms={red['report'].get('term_leaks')}")
check("כל האזורים נבדקו בקובץ הסופי",
      red["report"]["regions_clean"] == red["report"]["regions_checked"],
      f"{red['report']['regions_clean']}/{red['report']['regions_checked']}")
check("המטאדאטה נוקתה", red["report"]["metadata_empty"],
      str(red["report"].get("remaining_metadata")))

print("\n[M8] הורדת המסמך הממוזג")
resp = urllib.request.urlopen(f"{BASE}/download/{sid}", timeout=30)
out = resp.read()
disp = resp.headers["Content-Disposition"]
check("שם הקובץ משקף מיזוג", "merged_4_files_redacted.pdf" in disp, disp)
d = fitz.open(stream=out, filetype="pdf")
check("5 עמודים בקובץ שהורד", d.page_count == 5, str(d.page_count))
text = "".join(p.get_text() for p in d)
for secret in ["123456782", "987654321", "5500005555555559", "55443322", "Israel Israeli"]:
    check(f"'{secret}' הוסר מהמסמך הממוזג", secret not in text)
check("תוכן לא רגיש מכל המקורות נשמר",
      "Payslip" in text and "Transactions" in text and "Cardholder" in text
      and "unique merge marker 7788" in text)
check("תלוש השכר מופיע פעם אחת בלבד", text.count("Payslip") == 1,
      str(text.count("Payslip")))
meta = {k: v for k, v in d.metadata.items() if v and k not in ("format", "encryption")}
check("אין מטאדאטה משום מסמך מקור", not meta, str(meta))
raw = out.decode("latin-1")
check("הערכים אינם בבייטים הגולמיים",
      "123456782" not in raw and "5500005555555559" not in raw)
d.close()

print("\n[M9] מיזוג לסשן לא קיים נדחה")
st, err = post_pdf(payslip, "x.pdf", session="0" * 32)
check("סשן לא קיים מחזיר 404", st == 404, f"got {st}")

print("\n[M10] קובץ פגום במיזוג אינו הורס את הסשן")
st, err = post_pdf(b"this is not a pdf at all", "broken.pdf", session=sid)
check("קובץ לא תקין נדחה", st == 400, f"got {st}")
check("הודעת השגיאה כוללת את שם הקובץ", "broken.pdf" in str(err.get("detail", "")), str(err))
resp = urllib.request.urlopen(f"{BASE}/page/{sid}/0", timeout=15)
check("הסשן שרד ועדיין עובד", resp.status == 200)

print("\n[M11] מיזוג בלבד — בלי לסמן דבר להסרה")
st, a1 = post_pdf(payslip, "a.pdf")
sid2 = a1["session"]
st, a2 = post_pdf(statement, "b.pdf", session=sid2)
check("שני מסמכים בסשן חדש", len(a2["sources"]) == 2 and len(a2["pages"]) == 3)

st, red2 = post_json("/redact", {"session": sid2, "areas": [],
                                 "forbidden_values": [], "rasterize": False,
                                 "black_boxes": False})
check("רשימת אזורים ריקה מתקבלת", st == 200, f"got {st}")
check("הדוח נקי", red2["report"]["clean"], str(red2["report"]))
check("אפס אזורים נבדקו", red2["report"]["regions_checked"] == 0)

resp = urllib.request.urlopen(f"{BASE}/download/{sid2}", timeout=30)
out2 = resp.read()
check("שם הקובץ משקף מיזוג", "merged_2_files" in resp.headers["Content-Disposition"])
d2 = fitz.open(stream=out2, filetype="pdf")
check("3 עמודים בקובץ הממוזג", d2.page_count == 3, str(d2.page_count))
t2 = "".join(p.get_text() for p in d2)
check("כל התוכן נשמר במלואו",
      "Payslip" in t2 and "Dana Cohen" in t2 and "Transactions" in t2)
check("מידע רגיש נשאר — לא ביקשנו להסיר", "123456782" in t2 and "987654321" in t2)
meta2 = {k: v for k, v in d2.metadata.items() if v and k not in ("format", "encryption")}
check("המטאדאטה נוקתה גם במיזוג בלבד", not meta2, str(meta2))
d2.close()

print("\n[M12] מסמך יחיד בלי סימונים — ניקוי מטאדאטה בלבד")
st, s1 = post_pdf(statement, "solo.pdf")
sid3 = s1["session"]
st, red3 = post_json("/redact", {"session": sid3, "areas": [],
                                 "forbidden_values": [], "rasterize": False,
                                 "black_boxes": False})
check("מתקבל 200 גם למסמך יחיד", st == 200, f"got {st}")
resp = urllib.request.urlopen(f"{BASE}/download/{sid3}", timeout=30)
out3 = resp.read()
check("שם הקובץ הרגיל", "solo_redacted.pdf" in resp.headers["Content-Disposition"],
      resp.headers["Content-Disposition"])
d3 = fitz.open(stream=out3, filetype="pdf")
meta3 = {k: v for k, v in d3.metadata.items() if v and k not in ("format", "encryption")}
check("המטאדאטה של המסמך המקורי נמחקה", not meta3, str(meta3))
orig_meta = fitz.open(stream=statement, filetype="pdf").metadata
check("(במקור אכן הייתה מטאדאטה)", bool(orig_meta.get("author")), str(orig_meta.get("author")))
check("התוכן לא נפגע", "Dana Cohen" in "".join(p.get_text() for p in d3))
d3.close()

print("\n" + "=" * 50)
if FAILS:
    print(f"נכשלו {len(FAILS)} בדיקות: {FAILS}")
    sys.exit(1)
print("כל בדיקות המיזוג עברו.")
