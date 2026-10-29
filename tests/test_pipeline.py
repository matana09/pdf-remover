"""בדיקות אימות מקצה לקצה למנוע ההסרה. הרצה: venv\\Scripts\\python.exe tests\\test_pipeline.py"""
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import detectors  # noqa: E402
import redactor  # noqa: E402

TMP = Path(__file__).parent
FAILS = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


# ---------- 1. checksums ----------
print("\n[1] אימות checksums")
check("ת\"ז תקינה מזוהה", detectors.is_valid_israeli_id("123456782"))
check("ת\"ז לא תקינה נדחית", not detectors.is_valid_israeli_id("123456789"))
check("Luhn תקין", detectors.luhn_ok("4580458045804580"))
check("Luhn לא תקין נדחה", not detectors.luhn_ok("4580458045804581"))

# ---------- 2. זיהוי בעברית ----------
print("\n[2] זיהוי במסמך עברי")
HEB_FONT = Path("C:/Windows/Fonts/arial.ttf")
heb_pdf = TMP / "sample_hebrew.pdf"
doc = fitz.open()
page = doc.new_page()
page.insert_font(fontname="heb", fontfile=str(HEB_FONT))
lines = [
    "תלוש שכר 07/2026",
    "שם: ישראל ישראלי",
    "ת.ז. 123456782",
    "מספר חשבון: 87654321",
    "בנק לאומי סניף 900",
    "טלפון 052-1234567",
]


def to_visual_rtl(s: str) -> str:
    """מדמה bidi אמיתי: מהפך רק רצפי עברית, ספרות נשארות LTR."""
    import re as _re
    parts = _re.split(r"([֐-׿\s]+)", s)
    return "".join(p[::-1] if _re.search(r"[֐-׿]", p) else p for p in reversed(parts))


y = 80
for ln in lines:
    page.insert_text((60, y), to_visual_rtl(ln), fontsize=13, fontname="heb")
    y += 28
doc.save(heb_pdf)
doc.close()

hdoc = redactor.open_doc(heb_pdf.read_bytes())
hinfo = redactor.analyze(hdoc)
kinds = {c["kind"] for c in hinfo["candidates"]}
print("    זוהו:", sorted(kinds))
check("ת\"ז זוהתה בעברית", "id_number" in kinds)
check("טלפון זוהה בעברית", "phone" in kinds)
check("חשבון בנק זוהה בעברית", "bank_account" in kinds, "מילת מפתח עברית")

# ---------- 3. הסרה אמיתית, לא ריבוע ----------
print("\n[3] הסרה אמיתית מול ריבוע שחור")
src = (TMP / "sample_payslip.pdf").read_bytes()
d = redactor.open_doc(src)
info = redactor.analyze(d)
areas = [{"page": c["page"], "rects": c["rects"]} for c in info["candidates"]]
out = redactor.apply_redactions(d, areas)
d.close()
rep = redactor.verify(out, areas)
check("כל האזורים ריקים בקובץ הסופי", rep["clean"], f"dirty={rep['dirty_regions']}")
check("נבדקו כל האזורים", rep["regions_checked"] == sum(len(a["rects"]) for a in areas))
check("המטאדאטה נוקתה", rep["metadata_empty"], str(rep["remaining_metadata"]))

# בדיקה שהמספרים לא קיימים בבייטים הגולמיים של הקובץ (לא רק בטקסט המחולץ)
raw = out.decode("latin-1")
check("הערכים אינם בבייטים הגולמיים", "123456782" not in raw and "4580458045804580" not in raw)

# השוואה: ריבוע שחור בלבד משאיר את הטקסט
d2 = redactor.open_doc(src)
for a in areas:
    for r in a["rects"]:
        d2[a["page"]].draw_rect(fitz.Rect(r), color=(0, 0, 0), fill=(0, 0, 0))
naive = d2.tobytes()
d2.close()
naive_txt = "".join(p.get_text() for p in fitz.open(stream=naive, filetype="pdf"))
check("ריבוע שחור בלבד אכן מדליף (השוואה)", "123456782" in naive_txt,
      "מוכיח שההסרה שלנו נחוצה")

# ---------- 3ב. אזור ריק מול מלבן שחור ----------
print("\n[3ב] סגנון ההסרה: אזור ריק (ברירת מחדל) מול מלבן שחור")
_d = redactor.open_doc(src)
_info = redactor.analyze(_d)
_id_rect = fitz.Rect([c for c in _info["candidates"] if c["kind"] == "id_number"][0]["rects"][0])
_d.close()

for _style, _black, _expected in [("ריק", False, (255, 255, 255)), ("שחור", True, (0, 0, 0))]:
    _doc = redactor.open_doc(src)
    _out = redactor.apply_redactions(
        _doc, [{"page": 0, "rects": [list(_id_rect)]}], black_boxes=_black)
    _doc.close()
    _p = fitz.open(stream=_out, filetype="pdf")
    _pix = _p[0].get_pixmap(dpi=100, clip=_id_rect)
    _colors = {_pix.pixel(x, y) for x in range(_pix.width) for y in range(_pix.height)}
    check(f"סגנון '{_style}': הטקסט נמחק מהקובץ",
          "123456782" not in _p[0].get_text())
    check(f"סגנון '{_style}': האזור בצבע {_expected}",
          _colors == {_expected}, str(sorted(_colors)[:3]))
    _p.close()

# ---------- 3ג. האימות מדויק: בלי אזעקות שווא, ובלי לפספס ----------
print("\n[3ג] דיוק דוח האימות")
_d = redactor.open_doc(src)
_info = redactor.analyze(_d)
_cands = _info["candidates"]
_id_area = {"page": 0, "rects": [c["rects"][0] for c in _cands if c["kind"] == "id_number"]}
_bank_area = {"page": 0, "rects": [c["rects"][0] for c in _cands if c["kind"] == "bank_account"]}
_d.close()

# תרחיש שיצר בעבר אזעקת שווא: מסירים את חשבון הבנק (12345678) אך
# משאירים במכוון את הת"ז (123456782) — הראשון הוא תת-מחרוזת ספרות של השני.
_doc = redactor.open_doc(src)
_out = redactor.apply_redactions(_doc, [_bank_area])
_doc.close()
_rep = redactor.verify(_out, [_bank_area])
check("אין אזעקת שווא כשערך אחר מכיל את אותן ספרות",
      _rep["clean"], f"dirty={_rep['dirty_regions']}")
_txt = "".join(p.get_text() for p in fitz.open(stream=_out, filetype="pdf"))
check("הת\"ז שלא סומנה אכן נשארה במסמך", "123456782" in _txt)
check("חשבון הבנק שסומן הוסר", "Account: 12345678" not in _txt)

# תרחיש נגדי: האימות חייב לתפוס אזור שבו באמת נשאר טקסט.
# מדמים זאת ע"י אימות מול אזור שכלל לא הוסר.
_rep_bad = redactor.verify(_out, [_id_area])
check("האימות תופס אזור שלא נוקה", not _rep_bad["clean"])
check("מדווח על האזור המלוכלך", len(_rep_bad["dirty_regions"]) == 1,
      str(_rep_bad["dirty_regions"]))
check("הדוח כולל את הטקסט שנותר",
      "123456782" in _rep_bad["dirty_regions"][0]["text"])

# מונחי חיפוש נבדקים כמחרוזת מדויקת, לא כספרות
_doc2 = redactor.open_doc(src)
_res2 = redactor.search_text(_doc2, "Israel Israeli")
_out2 = redactor.apply_redactions(_doc2, _res2)
_doc2.close()
_rep2 = redactor.verify(_out2, _res2, search_terms=["Israel Israeli"])
check("מונח חיפוש שהוסר מדווח כנקי", _rep2["clean"], str(_rep2["term_leaks"]))
_rep3 = redactor.verify(_out2, _res2, search_terms=["Payslip"])
check("מונח שעדיין במסמך מדווח כדליפה", _rep3["term_leaks"] == ["Payslip"])

# ---------- 4. rasterize ----------
print("\n[4] מצב ביטחון מקסימלי (rasterize)")
d3 = redactor.open_doc(src)
rast = redactor.apply_redactions(d3, areas, rasterize=True)
d3.close()
rtxt = "".join(p.get_text() for p in fitz.open(stream=rast, filetype="pdf")).strip()
check("אין שכבת טקסט כלל אחרי rasterize", rtxt == "", f"len={len(rtxt)}")
check("הקובץ עדיין תקין", fitz.open(stream=rast, filetype="pdf").page_count == 1)

# ---------- 5. חיפוש טקסט ----------
print("\n[5] חיפוש טקסט והסרת כל המופעים")
d4 = redactor.open_doc(src)
res = redactor.search_text(d4, "Israel Israeli")
check("החיפוש מצא מופעים", len(res) > 0, f"{len(res)} מופעים")
out2 = redactor.apply_redactions(d4, res)
d4.close()
check("השם הוסר מהקובץ", redactor.verify(out2, res, search_terms=["Israel Israeli"])["clean"])

# ---------- 6. PDF סרוק ----------
print("\n[6] מסמך סרוק (ללא שכבת טקסט)")
scan = fitz.open()
sp = scan.new_page()
tmp_doc = fitz.open(stream=src, filetype="pdf")
pix = tmp_doc[0].get_pixmap(dpi=120)
sp.insert_image(sp.rect, stream=pix.tobytes("png"))
scan_bytes = scan.tobytes()
scan.close()
sdoc = redactor.open_doc(scan_bytes)
sinfo = redactor.analyze(sdoc)
check("המסמך מסומן כסרוק", not sinfo["pages"][0]["has_text"])
check("אין מועמדים אוטומטיים בסרוק", len(sinfo["candidates"]) == 0)
# הסרה ידנית על סרוק חייבת למחוק פיקסלים
manual = [{"page": 0, "rects": [[50, 60, 400, 300]]}]
sout = redactor.apply_redactions(sdoc, manual)
sdoc.close()
before = fitz.open(stream=scan_bytes, filetype="pdf")[0].get_pixmap(dpi=72)
after = fitz.open(stream=sout, filetype="pdf")[0].get_pixmap(dpi=72)
check("פיקסלים באזור המסומן השתנו", before.samples != after.samples)

# ---------- 7. אין קריאות רשת ב-frontend ----------
print("\n[7] בידוד רשת")
import re
net_hits = []
for f in ["static/index.html", "static/app.js", "static/style.css"]:
    txt = (ROOT / f).read_text(encoding="utf-8")
    for m in re.findall(r"https?://[^\s\"'<>)]+", txt):
        if "127.0.0.1" not in m and "localhost" not in m:
            net_hits.append(f"{f}: {m}")
check("אין URL חיצוני ב-frontend", not net_hits, "; ".join(net_hits))
app_src = (ROOT / "app.py").read_text(encoding="utf-8")
check("השרת מאזין רק ל-127.0.0.1", 'host="127.0.0.1"' in app_src)
check("קיימת הגנת Host מפני DNS rebinding", "ALLOWED_HOSTS" in app_src)
check("קיימת מדיניות CSP", "Content-Security-Policy" in app_src)

# בדיקה חיה מול השרת אם הוא פועל
try:
    import urllib.error
    import urllib.request
    req = urllib.request.Request("http://127.0.0.1:8477/", headers={"Host": "evil.example.com"})
    urllib.request.urlopen(req, timeout=3)
    check("Host זר נחסם", False, "התקבלה תשובה 200")
except urllib.error.HTTPError as e:
    check("Host זר נחסם", e.code == 403, f"status={e.code}")
except Exception as e:
    print(f"  SKIP  בדיקה חיה — השרת אינו פועל ({type(e).__name__})")

# ---------- 7ב. מיזוג כמה מסמכים למסמך אחד ----------
print("\n[7ב] מיזוג מסמכים")
pay_bytes = (TMP / "sample_payslip.pdf").read_bytes()
stmt_bytes = (TMP / "sample_statement.pdf").read_bytes()

merged = redactor.open_doc(pay_bytes)
n0 = merged.page_count
res_m = redactor.merge_into(merged, stmt_bytes)
added = res_m["added"]
check("מספר העמודים שנוספו נכון", added == 2, f"added={added}")
check("שום עמוד לא דולג במיזוג של מסמכים שונים", res_m["skipped"] == [], str(res_m["skipped"]))
check("הוחזרה חתימה לכל עמוד שנוסף", len(res_m["sigs"]) == 2, str(len(res_m["sigs"])))
check("סך העמודים אחרי מיזוג", merged.page_count == n0 + 2, f"{merged.page_count}")

# ניתוח מצטבר: רק העמודים החדשים, עם היסט נכון
info_a = redactor.analyze(merged, start_page=0)
info_b = redactor.analyze(merged, start_page=n0)
check("ניתוח חלקי מחזיר רק עמודים חדשים",
      [p["index"] for p in info_b["pages"]] == list(range(n0, n0 + 2)))
check("אין מועמדים עם עמוד מחוץ לטווח",
      all(0 <= c["page"] < merged.page_count for c in info_a["candidates"]))
pages_b = {c["page"] for c in info_b["candidates"]}
check("מועמדים מהמסמך השני ממופים לעמודים הנכונים",
      pages_b and all(p >= n0 for p in pages_b), str(sorted(pages_b)))

# תוכן שני המסמכים קיים במסמך הממוזג
merged_text = "".join(merged[i].get_text() for i in range(merged.page_count))
check("תוכן המסמך הראשון קיים", "Payslip" in merged_text)
check("תוכן המסמך השני קיים", "Dana Cohen" in merged_text and "Transactions" in merged_text)

# הסרה על המסמך הממוזג מנקה את שני המקורות
all_areas = [{"page": c["page"], "rects": c["rects"]} for c in info_a["candidates"]]
merged_out = redactor.apply_redactions(merged, all_areas)
merged.close()
rep_m = redactor.verify(merged_out, all_areas)
check("ההסרה במסמך הממוזג נקייה", rep_m["clean"], f"dirty={rep_m['dirty_regions']}")
out_text = "".join(p.get_text() for p in fitz.open(stream=merged_out, filetype="pdf"))
check("ת\"ז מהמסמך הראשון הוסרה", "123456782" not in out_text)
check("ת\"ז מהמסמך השני הוסרה", "987654321" not in out_text)
check("אשראי מהמסמך השני הוסר", "5500005555555559" not in out_text)
check("טקסט שאינו רגיש נשמר", "Payslip" in out_text and "Transactions" in out_text)

# ---------- 7ג. מיזוג ללא סימונים כלל ----------
print("\n[7ג] הפקת קובץ בלי לסמן דבר להסרה")
_m = redactor.open_doc(pay_bytes)
redactor.merge_into(_m, stmt_bytes)
_orig_meta = fitz.open(stream=pay_bytes, filetype="pdf").metadata.get("author")
_out_only = redactor.apply_redactions(_m, [])   # רשימת אזורים ריקה
_m.close()
_p = fitz.open(stream=_out_only, filetype="pdf")
_t = "".join(pg.get_text() for pg in _p)
check("הקובץ תקין ומכיל את כל העמודים", _p.page_count == 3, str(_p.page_count))
check("כל התוכן נשמר", "Payslip" in _t and "Dana Cohen" in _t and "Transactions" in _t)
check("מידע רגיש נשאר — לא התבקשה הסרה",
      "123456782" in _t and "987654321" in _t)
check("(במקור הייתה מטאדאטה)", bool(_orig_meta), str(_orig_meta))
check("המטאדאטה נוקתה גם בלי הסרות",
      not {k: v for k, v in _p.metadata.items() if v and k not in ("format", "encryption")})
_rep_empty = redactor.verify(_out_only, [])
check("דוח האימות נקי עם אפס אזורים",
      _rep_empty["clean"] and _rep_empty["regions_checked"] == 0)
_p.close()

# ---------- 7ד. מניעת כפילות עמודים במיזוג ----------
print(chr(10) + "[7ד] מיזוג לא משכפל את אותו עמוד")


def _sigset(doc):
    return {sg for sg in redactor.page_signatures(doc) if sg}


# אותו קובץ בדיוק, פעמיים
_d = redactor.open_doc(pay_bytes)
_r = redactor.merge_into(_d, pay_bytes, existing_sigs=_sigset(_d))
check("מיזוג אותו קובץ שוב אינו מוסיף עמודים", _r["added"] == 0, str(_r))
check("העמוד הכפול מדווח כמדולג", _r["skipped"] == [0], str(_r["skipped"]))
check("מספר העמודים לא השתנה", _d.page_count == 1, str(_d.page_count))

# אותו תוכן, בייטים שונים (הקובץ נשמר מחדש) — hash על הקובץ היה מפספס
_resaved = fitz.open(stream=pay_bytes, filetype="pdf").tobytes(garbage=4, deflate=True)
check("(הבייטים אכן שונים)", _resaved != pay_bytes)
_r = redactor.merge_into(_d, _resaved, existing_sigs=_sigset(_d))
check("עותק שנשמר מחדש מזוהה ככפילות", _r["added"] == 0, str(_r))
_d.close()

# מסמכים שונים ממוזגים כרגיל
_d = redactor.open_doc(pay_bytes)
_r = redactor.merge_into(_d, stmt_bytes, existing_sigs=_sigset(_d))
check("מסמך אחר אינו מדולג", _r["added"] == 2 and _r["skipped"] == [], str(_r))

# חפיפה חלקית: רק העמוד שכבר קיים מדולג
_r = redactor.merge_into(_d, pay_bytes + b"", existing_sigs=_sigset(_d))
check("חפיפה חלקית — הכפול בלבד מדולג", _r["added"] == 0 and _r["skipped"] == [0], str(_r))
_mixed = fitz.open()
_mixed.insert_pdf(fitz.open(stream=pay_bytes, filetype="pdf"))     # כבר קיים
_mixed.new_page(width=300, height=300)                              # ריק
_mixed[1].insert_text((50, 50), "brand new unique content 42")
_mixed_bytes = _mixed.tobytes()
_mixed.close()
_before = _d.page_count
_r = redactor.merge_into(_d, _mixed_bytes, existing_sigs=_sigset(_d))
check("מקובץ מעורב נוסף רק העמוד החדש", _r["added"] == 1 and _r["skipped"] == [0], str(_r))
check("סך העמודים גדל באחד בלבד", _d.page_count == _before + 1, str(_d.page_count))
_txt = "".join(_d[i].get_text() for i in range(_d.page_count))
check("תוכן העמוד החדש נכנס", "brand new unique content 42" in _txt)
check("תוכן העמוד הקיים לא הוכפל", _txt.count("Israel Israeli") == 1,
      str(_txt.count("Israel Israeli")))
_d.close()

# עמוד ריק לגמרי אינו נחשב כפילות — אחרת דף מפריד היה נעלם
_blank = fitz.open()
_blank.new_page(width=595, height=842)
_blank.new_page(width=595, height=842)
_bb = _blank.tobytes()
check("עמוד ריק אינו מקבל חתימה", redactor.page_signature(_blank, 0) is None)
_blank.close()
_d = redactor.open_doc(_bb)
_r = redactor.merge_into(_d, _bb, existing_sigs=_sigset(_d))
check("עמודים ריקים אינם מדולגים", _r["added"] == 2 and _r["skipped"] == [], str(_r))
_d.close()

# עמודים סרוקים (תמונה בלבד, בלי טקסט) — התמונה היא המבדיל היחיד
def _scan_pdf(color):
    d = fitz.open()
    pg = d.new_page(width=200, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
    pix.set_rect(pix.irect, color)
    pg.insert_image(pg.rect, stream=pix.tobytes("png"))
    b = d.tobytes()
    d.close()
    return b


_scan_a, _scan_b = _scan_pdf((10, 20, 30)), _scan_pdf((200, 100, 50))
_da = fitz.open(stream=_scan_a, filetype="pdf")
check("(העמוד הסרוק אכן חסר טקסט)", not _da[0].get_text("text").strip())
check("לעמוד סרוק יש חתימה", redactor.page_signature(_da, 0) is not None)
_r = redactor.merge_into(_da, _scan_a, existing_sigs=_sigset(_da))
check("סריקה זהה מזוהה ככפילות", _r["added"] == 0, str(_r))
_r = redactor.merge_into(_da, _scan_b, existing_sigs=_sigset(_da))
check("סריקה שונה אינה מדולגת", _r["added"] == 1 and _r["skipped"] == [], str(_r))
_da.close()

# ---------- 8. פקיעת סשנים (השרת רץ תמיד ברקע) ----------
print("\n[8] פקיעת סשנים בזיכרון")
import time as _time  # noqa: E402

import app as appmod  # noqa: E402

appmod.SESSIONS.clear()
appmod.SESSIONS["live"] = {"doc": fitz.open(), "bytes": b"", "name": "a.pdf", "touched": _time.time()}
appmod.SESSIONS["stale"] = {"doc": fitz.open(), "bytes": b"", "name": "b.pdf",
                            "touched": _time.time() - appmod.SESSION_TTL_SECONDS - 10}
appmod.purge_expired()
check("סשן שפג נמחק מהזיכרון", "stale" not in appmod.SESSIONS)
check("סשן פעיל נשמר", "live" in appmod.SESSIONS)
appmod.SESSIONS.clear()

# ---------- 9. בידוד מהתוכנות האחרות במחשב ----------
print("\n[9] אי-התנגשות עם MoneySaver / BOTI / WEBTOP")
stop_src = (ROOT / "scripts" / "stop-pdfredactor.ps1").read_text(encoding="utf-8-sig")
check("סקריפט העצירה מסנן לפי נתיב הפרויקט", "$projectDir" in stop_src)
check("קיימת שכבת הגנה על פרויקטים אחרים",
      "MONEY SAVER" in stop_src and "WEBTOP" in stop_src)
check("אין הריגה גורפת לפי שם תהליך",
      "-Name python" not in stop_src and "taskkill /IM" not in stop_src)

# קבצי BAT חייבים להישאר ASCII — cmd.exe שובר עברית בקבצי .bat
for bat in ROOT.glob("*.bat"):
    raw = bat.read_bytes()
    check(f"{bat.name} הוא ASCII בלבד", all(b < 128 for b in raw))

vbs_src = (ROOT / "run-hidden.vbs").read_text(encoding="utf-8")
check("ה-VBS מפעיל את python דרך נתיב ה-venv המלא", "baseDir & \"\\venv\\Scripts\\python.exe\"" in vbs_src)
check("ההפעלה האוטומטית אינה פותחת דפדפן", "--no-browser" in vbs_src)

app_src2 = (ROOT / "app.py").read_text(encoding="utf-8")
check("קיימת הגנה מפני מופע כפול על הפורט", "_port_owner" in app_src2)

print("\n" + "=" * 50)
if FAILS:
    print(f"נכשלו {len(FAILS)} בדיקות: {FAILS}")
    sys.exit(1)
print("כל הבדיקות עברו.")
