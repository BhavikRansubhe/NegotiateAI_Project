"""
PDF text extraction with OCR fallback for scanned documents.
Uses pdfplumber for native text + tables; falls back to pytesseract if text is sparse.
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber


def _has_sufficient_text(text: str | None, min_chars: int = 100) -> bool:
    """Heuristic: native extraction likely sufficient if we got enough text."""
    if not text or not text.strip():
        return False
    cleaned = re.sub(r"(.)\1{2,}", r"\1", text)
    return len(cleaned.strip()) >= min_chars


def _tables_to_text(tables: list) -> str:
    """Convert extracted tables to readable text lines for LLM consumption."""
    lines: list[str] = []
    for table in tables:
        if not table:
            continue
        for row in table:
            if row and any(cell is not None and str(cell).strip() for cell in row):
                row_str = " | ".join(str(cell or "").strip() for cell in row)
                if row_str.strip():
                    lines.append(row_str)
    return "\n".join(lines) if lines else ""


def _extract_with_pdfplumber(path: Path) -> str:
    """Extract ALL text from PDF: page text + table contents for line items."""
    text_parts: list[str] = []
    table_parts: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
                tables = page.extract_tables()
                if tables:
                    table_parts.append(_tables_to_text(tables))
    except Exception:
        pass
    # Combine: full page text first (headers, vendor), then table data
    all_text = "\n\n".join(text_parts) if text_parts else ""
    table_text = "\n\n".join(table_parts) if table_parts else ""
    if table_text and table_text not in all_text:
        all_text = all_text + "\n\n--- LINE ITEM TABLE ---\n\n" + table_text
    return all_text or ""


def _extract_with_ocr(path: Path) -> str:
    """Fallback: OCR via pdf2image + pytesseract."""
    try:
        from pdf2image import convert_from_path
        import pytesseract

        images = convert_from_path(path, dpi=200)
        texts = [pytesseract.image_to_string(img) for img in images]
        return "\n\n".join(t.strip() for t in texts if t and t.strip())
    except ImportError:
        return ""
    except Exception:
        return ""


def extract_text_from_pdf(path: str | Path) -> str:
    """
    Extract text from a PDF. Uses native text first; falls back to OCR if sparse.
    Returns raw text with minimal preprocessing (handles OCR noise downstream).
    """
    path = Path(path)
    if not path.exists() or path.suffix.lower() != ".pdf":
        return ""

    text = _extract_with_pdfplumber(path)
    if not _has_sufficient_text(text):
        ocr_text = _extract_with_ocr(path)
        if ocr_text:
            text = ocr_text

    return text or ""
