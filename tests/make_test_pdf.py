"""יוצר PDF בדיקה עם נתונים פיקטיביים (ת"ז תקינה, אשראי עובר Luhn וכו')."""
import sys
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).parent.parent))

OUT = Path(__file__).parent / "sample_payslip.pdf"

# ת"ז פיקטיבית עם ספרת ביקורת תקינה, אשראי פיקטיבי שעובר Luhn
LINES = [
    "Payslip - Salary Statement 07/2026",
    "Employee: Israel Israeli",
    "ID Number: 123456782",
    "Phone: 052-1234567",
    "Bank: Leumi   Branch: 900   Account: 12345678",
    "Credit Card: 4580458045804580",
    "Card split: 4111 1111 1111 1111",
    "Gross: 18,500.00   Net: 13,240.55",
    "IBAN: IL620108000000099999999",
]


def main():
    doc = fitz.open()
    page = doc.new_page()
    y = 80
    for line in LINES:
        page.insert_text((60, y), line, fontsize=12, fontname="helv")
        y += 26
    doc.set_metadata({"author": "Payroll System", "title": "Israel Israeli Payslip"})
    doc.save(OUT)
    doc.close()
    print("created", OUT)


if __name__ == "__main__":
    main()
