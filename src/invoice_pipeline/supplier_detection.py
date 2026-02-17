"""
Supplier detection from invoice text.
Uses deterministic keyword matching; can be extended with LLM if needed.
"""
from __future__ import annotations

import re
from typing import Optional

# Known supplier signatures (regex patterns -> normalized name)
SUPPLIER_SIGNATURES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"magid\s*glove", re.I), "Magid Glove and Safety Manufacturing"),
    (re.compile(r"magidglove\.com", re.I), "Magid Glove and Safety Manufacturing"),
    (re.compile(r"uline\.com", re.I), "ULINE"),
    (re.compile(r"\buline\b", re.I), "ULINE"),
    (re.compile(r"fastenal\s+company", re.I), "Fastenal"),
    (re.compile(r"fastenal\.com", re.I), "Fastenal"),
    (re.compile(r"grainger", re.I), "Grainger"),
    (re.compile(r"mcmaster", re.I), "McMaster-Carr"),
    (re.compile(r"amazon\s*business", re.I), "Amazon Business"),
    (re.compile(r"staples", re.I), "Staples"),
    (re.compile(r"w\.?w\.?grainger", re.I), "Grainger"),
    (re.compile(r"global\s*industrial", re.I), "Global Industrial"),
    (re.compile(r"mscdirect", re.I), "MSC Industrial"),
    (re.compile(r"m\.?s\.?c\.?\s*direct", re.I), "MSC Industrial"),
]


def _normalize_supplier_name(raw: str) -> str:
    """Clean up supplier string: title case, collapse whitespace."""
    s = re.sub(r"\s+", " ", raw.strip())
    return s.title() if s else "Unknown Supplier"


def _ocr_normalize(text: str) -> str:
    """Collapse repeated chars for OCR noise (e.g. MMMaaagggiiiddd -> Magid)."""
    return re.sub(r"(.)\1{2,}", r"\1", text)


def detect_supplier(text: str) -> str:
    """
    Detect supplier from invoice text.
    Returns normalized_supplier_name (used as hint for LLM extraction).
    """
    text_ocr = _ocr_normalize(text).lower()

    for pattern, name in SUPPLIER_SIGNATURES:
        if pattern.search(text) or pattern.search(text_ocr):
            return _normalize_supplier_name(name)

    for line in text.split("\n")[:30]:
        line = line.strip()
        line_ocr = _ocr_normalize(line)
        if re.match(r"^[A-Za-z][a-z]+(\s+[A-Za-z][a-z]+)*\s+(Company|Inc|LLC|Corp|Ltd)\.?$", line_ocr):
            return _normalize_supplier_name(line_ocr)
        if "Remit" in line or "Invoice" in line:
            if "Remit" in line and "P.O." not in line[:20]:
                for part in re.split(r"[\|\-\t:]+", line):
                    p = part.strip()
                    if len(p) > 5 and p[0].isupper() and "P.O." not in p and "BOX" not in p:
                        return _normalize_supplier_name(p)

    return "Unknown Supplier"
