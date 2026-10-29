"""מנוע ההסרה — PyMuPDF.

הסרה אמיתית: add_redact_annot + apply_redactions מוחקים את התוכן
מה-content stream, לא רק מציירים ריבוע מעליו.
"""
from __future__ import annotations

import hashlib
import io

import fitz  # PyMuPDF

from detectors import detect_in_words

RENDER_DPI = 150


def open_doc(pdf_bytes: bytes) -> fitz.Document:
    return fitz.open(stream=pdf_bytes, filetype="pdf")


def render_page_png(doc: fitz.Document, page_index: int, dpi: int = RENDER_DPI) -> bytes:
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def analyze(doc: fitz.Document, start_page: int = 0) -> dict:
    """מחזיר מידע על המסמך: עמודים, האם יש שכבת טקסט, ומועמדים לזיהוי.

    start_page: ניתוח רק מעמוד זה והלאה. משמש במיזוג — כשמוסיפים מסמך
    לסשן קיים, אין צורך לנתח מחדש את העמודים שכבר נותחו.
    """
    pages = []
    candidates = []
    for i in range(start_page, doc.page_count):
        page = doc[i]
        words = page.get_text("words")
        has_text = len(words) > 0
        pages.append({
            "index": i,
            "width": page.rect.width,
            "height": page.rect.height,
            "has_text": has_text,
        })
        if has_text:
            candidates.extend(c.to_dict() for c in detect_in_words(words, i))
    return {"pages": pages, "candidates": candidates}


def page_signature(doc: fitz.Document, index: int) -> str | None:
    """טביעת אצבע של תוכן העמוד, לזיהוי מיזוג כפול של אותו עמוד.

    נגזרת מהתוכן ולא מבייטי הקובץ, כי PDF שנשמר מחדש (או נוצר מחדש
    מאותו מקור) מקבל בייטים שונים לגמרי לתוכן זהה — hash על הקובץ
    היה מפספס בדיוק את המקרה שאותו צריך לתפוס.

    מרכיבי החתימה:
      • מידות העמוד — מפרידה בין דפים בגדלים שונים.
      • הטקסט המלא — המבדיל העיקרי בעמודים עם שכבת טקסט.
      • ה-hash של זרמי התמונות הגולמיים — המבדיל היחיד בעמודים סרוקים,
        שבהם אין טקסט בכלל. גולמי (raw) בכוונה: בלי לפענח את התמונה.

    מחזיר None לעמוד ריק לחלוטין (בלי טקסט ובלי תמונות). עמוד כזה זהה
    לכל עמוד ריק אחר באותו גודל, ולכן אסור להתייחס אליו ככפילות —
    עמוד מפריד ריק במסמך שני היה נעלם.
    """
    page = doc[index]
    text = page.get_text("text").strip()
    xrefs = sorted({img[0] for img in page.get_images(full=True)})
    if not text and not xrefs:
        return None

    h = hashlib.sha256()
    h.update(f"{round(page.rect.width, 1)}x{round(page.rect.height, 1)}|".encode())
    h.update(text.encode("utf-8", "replace"))
    for xref in xrefs:
        h.update(b"|img|")
        try:
            h.update(hashlib.sha256(doc.xref_stream_raw(xref)).digest())
        except Exception:
            # לא ניתן לקרוא את הזרם: משאירים מזהה ייחודי-למסמך, כך
            # שהעמוד לא ייחשב כפילות בטעות (כיוון בטוח — לא מוחקים).
            h.update(f"xref{xref}".encode())
    return h.hexdigest()


def page_signatures(doc: fitz.Document, start_page: int = 0) -> list[str | None]:
    return [page_signature(doc, i) for i in range(start_page, doc.page_count)]


def merge_into(target: fitz.Document, pdf_bytes: bytes,
               existing_sigs: set[str] | None = None) -> dict:
    """מוסיף את עמודי הקובץ לסוף המסמך, בלי לשכפל עמודים שכבר קיימים.

    existing_sigs: חתימות העמודים שכבר נמצאים במסמך היעד. כל עמוד נכנס
    שחתימתו כבר שם — מדולג. כך מיזוג של אותו קובץ פעמיים (או של שני
    קבצים שחופפים חלקית) לא מייצר את אותו עמוד פעמיים.

    כפילויות *בתוך* הקובץ הנכנס עצמו אינן מסוננות בכוונה: זה המסמך של
    המשתמש כפי שהוא, ולא תפקידנו למחוק ממנו עמודים.

    מחזיר {"added": int, "skipped": [אינדקסים מקוריים בקובץ הנכנס],
            "sigs": [חתימות העמודים שנוספו]}.
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        sigs = page_signatures(src)
        seen = existing_sigs or set()
        keep, keep_sigs, skipped = [], [], []
        for i, sig in enumerate(sigs):
            if sig is not None and sig in seen:
                skipped.append(i)
            else:
                keep.append(i)
                keep_sigs.append(sig)

        if not keep:
            return {"added": 0, "skipped": skipped, "sigs": []}
        if skipped:
            # select משאיר במסמך המקור רק את העמודים שנבחרו, לפי הסדר
            src.select(keep)
        target.insert_pdf(src)
        return {"added": len(keep), "skipped": skipped, "sigs": keep_sigs}
    finally:
        src.close()


def search_text(doc: fitz.Document, query: str) -> list[dict]:
    """חיפוש טקסט בכל העמודים — מחזיר אזורים להסרה."""
    results = []
    for i, page in enumerate(doc):
        for rect in page.search_for(query):
            results.append({"page": i, "rects": [[rect.x0, rect.y0, rect.x1, rect.y1]]})
    return results


def apply_redactions(doc: fitz.Document, areas: list[dict], rasterize: bool = False,
                     black_boxes: bool = False) -> bytes:
    """מבצע הסרה אמיתית.

    areas: [{"page": int, "rects": [[x0,y0,x1,y1], ...]}, ...] בקואורדינטות PDF.
    black_boxes: לצייר מלבן שחור מעל האזור. ברירת המחדל היא אזור ריק.

    המילוי הוא קוסמטי בלבד: apply_redactions מוחק את התוכן מה-content stream
    בשני המקרים. אזור ריק נשאר לבן לחלוטין — הטקסט אינו קיים שם, לא מוסתר.
    """
    fill = (0, 0, 0) if black_boxes else False
    pages_with_redactions = set()
    for area in areas:
        page = doc[area["page"]]
        for r in area["rects"]:
            rect = fitz.Rect(r)
            # הרחבה קלה כדי לא להשאיר שוליים של תווים חתוכים
            rect += (-1, -1, 1, 1)
            page.add_redact_annot(rect, fill=fill)
        pages_with_redactions.add(area["page"])

    for i in pages_with_redactions:
        # מוחק טקסט, תמונות וגרפיקה וקטורית מתחת לאזורים
        doc[i].apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_PIXELS,
            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
        )

    _strip_metadata(doc)

    if rasterize:
        doc = _rasterize(doc)
        _strip_metadata(doc)

    # garbage=4 מסלק אובייקטים יתומים (שרידי תוכן שנמחק)
    return doc.tobytes(garbage=4, deflate=True, clean=True)


def _strip_metadata(doc: fitz.Document):
    doc.set_metadata({})
    doc.del_xml_metadata()
    # הסרת קבצים מוטמעים
    for name in list(doc.embfile_names()):
        doc.embfile_del(name)
    # הסרת annotations שנותרו (הערות, קישורים עם מידע)
    for page in doc:
        for annot in list(page.annots() or []):
            page.delete_annot(annot)


def _rasterize(doc: fitz.Document, dpi: int = 200) -> fitz.Document:
    """ממיר כל עמוד לתמונה — מוחק כל שריד טקסט/וקטור. """
    out = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=pix.tobytes("png"))
    return out


def verify(pdf_bytes: bytes, areas: list[dict], search_terms: list[str] | None = None) -> dict:
    """מאמת את תוצאת ההסרה בקובץ הסופי.

    שתי בדיקות נפרדות:

    1. אזורים — כל אזור שסומן להסרה חייב להיות ריק מטקסט בקובץ הסופי.
       זו הבדיקה המרכזית: היא מוכיחה בדיוק את מה שהובטח, ואינה יכולה
       להתריע לשווא.

    2. מונחי חיפוש — כשהמשתמש ביקש "הסר את כל המופעים של X", מאמתים
       שהמחרוזת אינה מופיעה באף מקום במסמך.

    בכוונה אין כאן השוואת "ספרות בלבד" גורפת: היא יצרה אזעקות שווא —
    למשל 12345678 שנמצא כתת-מחרוזת של ת"ז אחרת שהמשתמש בחר לא להסיר,
    או 072026 (מתוך תאריך 07/2026) שנמצא בעמוד אחר לגמרי.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    dirty_regions = []
    for area in areas:
        page_index = area["page"]
        if page_index >= doc.page_count:
            continue
        page = doc[page_index]
        for r in area["rects"]:
            leftover = page.get_textbox(fitz.Rect(r)).strip()
            if leftover:
                dirty_regions.append({"page": page_index, "text": leftover[:60]})

    full_text = "".join(page.get_text() for page in doc)
    term_leaks = [t for t in (search_terms or []) if t.strip() and t.strip() in full_text]

    # "format"/"encryption" אינם מטאדאטה שהמשתמש הכניס — הם מאפייני הקובץ עצמו
    ignored = {"format", "encryption"}
    meta = {k: val for k, val in doc.metadata.items() if val and k not in ignored}
    doc.close()

    total_rects = sum(len(a["rects"]) for a in areas)
    return {
        "clean": not dirty_regions and not term_leaks,
        "regions_checked": total_rects,
        "regions_clean": total_rects - len(dirty_regions),
        "dirty_regions": dirty_regions,
        "term_leaks": term_leaks,
        "metadata_empty": len(meta) == 0,
        "remaining_metadata": meta,
    }
