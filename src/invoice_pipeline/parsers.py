"""
Generic line item parser. Works across all suppliers - no format-specific logic.
Improved to handle table format, pipe-separated columns, Price Per Hundred, and better MPN extraction.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import RawLineItem


def _parse_float(s: str | None) -> Optional[float]:
    if not s:
        return None
    s = re.sub(r"[^\d.\-]", "", str(s))
    try:
        return float(s)
    except ValueError:
        return None


# Common UOMs - check longer ones first to avoid partial matches
UOM_PATTERNS = [
    "DOZEN", "CARTON", "DISPLAY", "PACKAGE", "BOTTLE",
    "EA", "EACH", "BX", "BOX", "CS", "CASE", "CT", "PR", "PAIR",
    "DZ", "DOZ", "PK", "PAC", "DP", "BG", "BAG", "RL", "ROLL", "CTN",
]


def _extract_uom(text: str) -> Optional[str]:
    """Extract UOM from text, preferring word boundaries."""
    text_upper = text.upper()
    for uom in UOM_PATTERNS:
        if re.search(rf"\b{re.escape(uom)}\b", text_upper):
            return uom
    return None


def _looks_like_sku(s: str) -> bool:
    """Heuristic: alphanumeric with optional dashes, typical part number pattern. Exclude UOMs."""
    uom_only = {"EA", "BX", "CS", "CT", "PR", "DZ", "PK", "DP", "BG", "RL", "UN"}
    if not s or len(s) < 3 or len(s) > 40:
        return False
    if s.upper() in uom_only:
        return False
    if re.match(r"^[A-Z0-9][A-Z0-9\-/.]*[A-Z0-9]$", s, re.I) or re.match(r"^[A-Z0-9\-]+$", s, re.I):
        return True
    return False


def _parse_line_item_from_parts(parts: list[str], is_price_per_hundred: bool = False) -> Optional[RawLineItem]:
    """Parse a list of column values into a RawLineItem."""
    # Find numeric columns from the end
    decimals: list[tuple[int, float]] = []
    integers: list[tuple[int, int]] = []
    for i, p in enumerate(parts):
        p_clean = p.replace(",", "").strip()
        try:
            v = float(p_clean)
            if v == int(v) and 1 <= v <= 999999:
                integers.append((i, int(v)))
            if "." in p_clean and 0.001 <= v <= 999999:
                decimals.append((i, v))
        except ValueError:
            pass

    if len(decimals) < 1:
        return None

    # For Price Per Hundred: last decimal = Amount (extended), second-to-last = Price/Hundred
    if is_price_per_hundred and len(decimals) >= 2:
        decimals_by_pos = sorted(decimals, key=lambda x: x[0])
        extended = decimals_by_pos[-1][1]
        price_per_hundred = decimals_by_pos[-2][1]
        unit_price = price_per_hundred / 100.0
    else:
        # Standard: extended = largest decimal (line total), unit_price = other
        decimals_sorted = sorted(decimals, key=lambda x: (-x[1], -x[0]))
        extended = decimals_sorted[0][1]
        unit_price = None
        for _, v in decimals_sorted[1:]:
            if 0.001 <= v <= 99999 and abs(v - extended) > 0.01:
                unit_price = v
                break
        if unit_price is None:
            unit_price = extended

    # Quantity: integer before prices
    qty = 1
    for i, v in integers:
        if i < len(parts) - 3 and v != int(extended) and v != int(unit_price):
            qty = v
            break

    if is_price_per_hundred and len(decimals) < 2:
        unit_price = (unit_price or 0) / 100.0

    # Validate: extended ≈ qty * unit_price (within 5%)
    if qty > 0 and unit_price:
        expected = qty * unit_price
        if extended > 0 and abs(extended - expected) / max(extended, 0.01) > 0.05:
            extended = expected

    desc_parts = []
    mpn = None
    uom = None
    for i, p in enumerate(parts):
        p = p.strip()
        if not p or re.match(r"^\d+\.?\d*$", p.replace(",", "")):
            continue
        if _extract_uom(p) and not uom:
            uom = _extract_uom(p)
        if _looks_like_sku(p) and mpn is None and i < len(parts) - 3:
            mpn = p
        if len(p) > 2 and not re.match(r"^[\d,.\$]+$", p):
            desc_parts.append(p)

    desc = " ".join(desc_parts[:5]) if desc_parts else " ".join(p for p in parts[:4] if p.strip())
    if not uom:
        uom = _extract_uom(desc) or _extract_uom(" ".join(parts))

    return RawLineItem(
        description=desc[:200] if desc else "Item",
        item_number=mpn,
        manufacturer_part=mpn,
        quantity=float(qty),
        original_uom=uom,
        unit_price=round(unit_price, 4) if unit_price else None,
        extended_price=round(extended, 2),
        line_confidence=0.75,
    )


def _parse_line_space_separated(line: str, is_price_per_hundred: bool = False) -> Optional[RawLineItem]:
    """Parse line with space-separated columns."""
    parts = line.split()
    if len(parts) < 4:
        return None
    return _parse_line_item_from_parts(parts, is_price_per_hundred)


def _parse_line_pipe_separated(line: str, is_price_per_hundred: bool = False) -> Optional[RawLineItem]:
    """Parse line with pipe-separated columns (from table extraction)."""
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    return _parse_line_item_from_parts(parts, is_price_per_hundred)


def extract_line_items(text: str) -> list[RawLineItem]:
    """Extract line items using generic table-based parser."""
    items: list[RawLineItem] = []
    seen: set[tuple[float, float]] = set()  # (qty, extended) dedup

    skip_keywords = [
        "invoice", "page", "remit", "sold to", "ship to", "sub-total", "subtotal",
        "total", "amount due", "please pay", "thank you", "customer order",
    ]
    is_price_per_hundred = (
        "price per hundred" in text.lower()
        or "price/hundred" in text.lower()
        or "price / hundred" in text.lower()
        or "per hundred" in text.lower()
    )

    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or len(line) < 8:
            continue
        # Skip header-like lines (all caps short words, no meaningful numbers)
        if any(kw in line.lower() for kw in skip_keywords):
            if not re.search(r"\d+\.\d{2}", line):
                continue
        # Skip lines that look like column headers only
        words = line.split()
        if len(words) <= 3 and not any(re.search(r"\d+\.\d{2}", w) for w in words):
            continue

        parsed = None
        if "|" in line and line.count("|") >= 2:
            parsed = _parse_line_pipe_separated(line, is_price_per_hundred)
        else:
            parsed = _parse_line_space_separated(line, is_price_per_hundred)

        # Fallback: original regex-based parse for lines that didn't match
        if parsed is None:
            nums = re.findall(r"[\d,]+\.?\d*", line)
            decimals = []
            for n in nums:
                v = _parse_float(n)
                if v and 0.01 <= v <= 999999:
                    decimals.append(v)
            if len(decimals) >= 2:
                extended = max(decimals)
                unit_price = next((d for d in decimals if d != extended and 0.001 <= d <= 99999), extended)
                qty = 1
                for n in re.findall(r"\b\d+\b", line):
                    v = int(n)
                    if 1 <= v <= 99999 and v != int(extended):
                        qty = v
                        break
                if is_price_per_hundred:
                    unit_price = unit_price / 100.0
                    extended = qty * unit_price
                desc_parts = [p for p in re.split(r"\s{2,}|\t", line) if p and not re.match(r"^\d+\.?\d*$", p.replace(",", "")) and len(p) > 2]
                desc = " ".join(desc_parts[:4]) if desc_parts else line[:80]
                uom = _extract_uom(line)
                parsed = RawLineItem(
                    description=desc[:200],
                    item_number=None,
                    manufacturer_part=None,
                    quantity=float(qty),
                    original_uom=uom,
                    unit_price=round(unit_price, 4),
                    extended_price=round(extended, 2),
                    line_confidence=0.65,
                )

        if parsed and parsed.extended_price and parsed.extended_price > 0:
            key = (parsed.quantity, parsed.extended_price)
            if key not in seen:
                seen.add(key)
                items.append(parsed)

    return items
