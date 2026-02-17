"""
Generic line item parser. Works across all suppliers - no format-specific logic.
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


def extract_line_items(text: str) -> list[RawLineItem]:
    """Extract line items using generic table-based parser."""
    items: list[RawLineItem] = []
    lines = text.split("\n")

    skip_keywords = ["invoice", "page", "remit", "sold to", "ship to", "sub-total", "total", "amount due"]

    for line in lines:
        line = line.strip()
        if not line or len(line) < 10:
            continue
        if any(kw in line.lower() for kw in skip_keywords) and not re.search(r"\d+\.\d{2}", line):
            continue

        nums = re.findall(r"\d+\.?\d*", line)
        if len(nums) < 2:
            continue

        decimals = [n for n in nums if "." in n]
        if not decimals:
            continue

        extended = None
        for d in decimals:
            try:
                v = float(d)
                if 0.01 <= v <= 999999 and v == round(v, 2):
                    extended = v
                    break
            except ValueError:
                pass

        if extended is None:
            continue

        unit_price = None
        for d in decimals:
            try:
                v = float(d)
                if v != extended and 0.001 <= v <= 99999:
                    unit_price = v
                    break
            except ValueError:
                pass

        qty = 1
        for n in nums:
            try:
                v = float(n)
                if v == int(v) and 1 <= v <= 99999 and v != extended:
                    qty = int(v)
                    break
            except ValueError:
                pass

        parts = re.split(r"\s{2,}|\t", line)
        desc_parts = []
        for p in parts:
            if not re.match(r"^\d+\.?\d*$", p.replace(",", "")) and len(p) > 2:
                desc_parts.append(p)
        desc = " ".join(desc_parts[:3]) if desc_parts else line[:50]

        uom = None
        for u in ["EA", "BX", "CS", "CT", "PR", "DZ", "DP", "RL", "BG", "PK"]:
            if re.search(rf"\b{u}\b", line, re.I):
                uom = u
                break

        items.append(
            RawLineItem(
                description=desc[:200],
                item_number=None,
                manufacturer_part=None,
                quantity=float(qty),
                original_uom=uom,
                unit_price=unit_price or (extended / qty if qty else None),
                extended_price=extended,
                line_confidence=0.7,
            )
        )

    return items
