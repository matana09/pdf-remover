"""זיהוי אוטומטי של מידע רגיש ישראלי בטקסט של PDF.

עובד על רשימת מילים עם קואורדינטות מ-page.get_text("words").
כל מועמד מוחזר עם סוג, ערך ממוסך לתצוגה, ו-bounding box.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Candidate:
    kind: str          # id_number / credit_card / bank_account / phone / iban / masked_card
    label: str         # שם לתצוגה בעברית
    masked: str        # ערך ממוסך לתצוגה
    page: int          # אינדקס עמוד (0-based)
    rects: list = field(default_factory=list)  # [(x0,y0,x1,y1), ...] — יכול לכסות כמה מילים

    def to_dict(self):
        return {
            "kind": self.kind,
            "label": self.label,
            "masked": self.masked,
            "page": self.page,
            "rects": [list(r) for r in self.rects],
        }


KIND_LABELS = {
    "id_number": "תעודת זהות",
    "credit_card": "כרטיס אשראי",
    "masked_card": "כרטיס ממוסך",
    "bank_account": "חשבון בנק",
    "phone": "טלפון",
    "iban": "IBAN",
}

# מילות מפתח שמעידות שהמספר הסמוך הוא חשבון בנק (השוואה ב-lowercase)
BANK_KEYWORDS = (
    "חשבון", "חשבון", "מס' חשבון", "מספר חשבון", "סניף", "בנק",
    "לזכות", "העברה", "משכורת ל",
    "account", "acct", "branch", "bank", "iban", "routing",
)


def is_valid_israeli_id(digits: str) -> bool:
    """אימות ספרת ביקורת של ת"ז ישראלית (9 ספרות, משקולות 1-2)."""
    if not digits.isdigit() or len(digits) > 9 or len(digits) < 5:
        return False
    digits = digits.zfill(9)
    total = 0
    for i, ch in enumerate(digits):
        v = int(ch) * (1 if i % 2 == 0 else 2)
        total += v if v < 10 else v - 9
    return total % 10 == 0


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or len(digits) < 12:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        v = int(ch)
        if i % 2 == 1:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return total % 10 == 0


def mask(value: str, keep: int = 2) -> str:
    """ממסך ערך לתצוגה: משאיר רק את הספרות האחרונות."""
    stripped = re.sub(r"\D", "", value)
    if len(stripped) <= keep:
        return "*" * len(stripped)
    return "*" * (len(stripped) - keep) + stripped[-keep:]


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)


# תווי מקף/רווח שונים שמופיעים ב-PDF (soft hyphen, en/em dash, מקף עברי, NBSP...)
DASH_CHARS = "­‐‑‒–—―−־⁃－"
SPACE_CHARS = "              　"
# תווי כיווניות בלתי־נראים שמופיעים בטקסט עברי
BIDI_CHARS = "‎‏‪‫‬‭‮⁦⁧⁨⁩​"


def normalize(text: str) -> str:
    """מנרמל מקפים, רווחים ותווי כיווניות לצורה סטנדרטית לפני זיהוי."""
    out = []
    for ch in text:
        if ch in BIDI_CHARS:
            continue
        if ch in DASH_CHARS:
            out.append("-")
        elif ch in SPACE_CHARS:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def detect_in_words(words: list, page_index: int) -> list[Candidate]:
    """words: רשימת טאפלים (x0, y0, x1, y1, text, block, line, word) מ-PyMuPDF.

    הזיהוי עובד ברמת מילה בודדת וגם על צירופי מילים סמוכות באותה שורה
    (מספרי אשראי/חשבון מפוצלים לעיתים לכמה מילים: "4580 1234 5678 9012").
    """
    candidates: list[Candidate] = []
    seen_rects: set = set()

    def add(kind: str, value: str, rects: list):
        key = (kind, tuple(tuple(round(c, 1) for c in r) for r in rects))
        if key in seen_rects:
            return
        seen_rects.add(key)
        candidates.append(Candidate(
            kind=kind, label=KIND_LABELS[kind], masked=mask(value),
            page=page_index, rects=rects,
        ))

    # --- זיהוי ברמת מילה בודדת ---
    for i, w in enumerate(words):
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], normalize(w[4])
        rect = (x0, y0, x1, y1)
        d = _digits_only(text)

        # כרטיס ממוסך: ****-1234 או XXXX1234
        if re.search(r"[*xX]{2,}[-\s]?\d{2,4}", text):
            add("masked_card", text, [rect])
            continue

        # IBAN ישראלי
        if re.fullmatch(r"IL\d{2}[\d\s-]{15,25}", text.strip(), re.IGNORECASE):
            add("iban", text, [rect])
            continue

        # טלפון: 05X-XXXXXXX או 0X-XXXXXXX (עם/בלי מקף)
        if re.fullmatch(r"0(5\d|[2-4789])[-\s]?\d{7}", text.strip()):
            add("phone", text, [rect])
            continue

        # טלפון שנשמר הפוך (PDF עברי שכתב את הספרות בסדר ויזואלי)
        if re.fullmatch(r"\d{7}[-\s]?(\d5|[2-4789])0", text.strip()):
            add("phone", text, [rect])
            continue

        # כרטיס אשראי במילה אחת (עם/בלי מקפים).
        # נבדק גם בסדר הפוך — יש PDF-ים עבריים ששומרים ספרות בסדר ויזואלי.
        if 12 <= len(d) <= 19 and (luhn_ok(d) or luhn_ok(d[::-1])):
            add("credit_card", d, [rect])
            continue

        # ת"ז: בדיוק 9 ספרות עם ספרת ביקורת תקינה (גם בסדר הפוך)
        if len(d) == 9 and text.strip().rstrip(".,:;").isdigit() \
                and (is_valid_israeli_id(d) or is_valid_israeli_id(d[::-1])):
            add("id_number", d, [rect])
            continue

        # חשבון בנק: מספר 5-9 ספרות בסמיכות למילת מפתח באותה שורה
        if 5 <= len(d) <= 9 and d == text.strip().rstrip(".,:;"):
            context = words[max(0, i - 3):i + 4]
            ctx_text = " ".join(normalize(w2[4]) for w2 in context if w2 is not w).lower()
            if any(kw in ctx_text for kw in BANK_KEYWORDS):
                add("bank_account", d, [rect])
                continue

    # --- צירופי מילים סמוכות באותה שורה (אשראי מפוצל לקבוצות) ---
    for i in range(len(words)):
        group, rects, digits = [], [], ""
        for j in range(i, min(i + 5, len(words))):
            w = words[j]
            t = normalize(w[4]).strip()
            if not re.fullmatch(r"[\d-]{2,6}", t):
                break
            # אותה שורה: block+line זהים
            if group and (w[5], w[6]) != (group[0][5], group[0][6]):
                break
            group.append(w)
            rects.append((w[0], w[1], w[2], w[3]))
            digits += _digits_only(t)
            if len(group) >= 3 and 12 <= len(digits) <= 19 and luhn_ok(digits):
                add("credit_card", digits, list(rects))
                break

    return candidates
