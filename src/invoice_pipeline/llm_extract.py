"""
LLM-based invoice extraction: supplier name, line items with clean descriptions and MPN.
Primary extraction path for high-quality structured output.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from dotenv import load_dotenv

from .models import RawLineItem

load_dotenv()


def extract_all_via_llm(
    text: str,
    hint_supplier: Optional[str] = None,
) -> tuple[str, list[RawLineItem]]:
    """
    Use LLM to extract supplier name and all line items from raw invoice text.
    Returns (supplier_name, raw_line_items).
    Produces clean item descriptions and MPN as in the target schema.
    """
    from .api_client import get_openai_client
    client = get_openai_client()
    if not client:
        return hint_supplier or "Unknown Supplier", []

    try:

        system = """You are an expert invoice data extractor. Extract the supplier/vendor name and all line items from the raw invoice text.

RULES:
1. supplier_name: The FULL legal/business name of the company that issued the invoice (e.g. "MSC Industrial Supply Co.", "ULINE", "Magid Glove and Safety Manufacturing", "Fastenal Company"). NOT addresses, NOT "Remit to", NOT P.O. Box. The vendor/supplier company name.

2. For each line item, extract:
   - item_description: CLEAN product description ONLY. Human-readable product name. Remove quantities, prices, UOM, raw table junk. Examples: "LARGE 1/PR MEN'S CTN/PLY STRNGKN GLV", "SAFETY GLASS WIPES", "TOILET BOWL CLEANER 32 OZ BOTTLE". NO "200 200 EA 0.37 74.00" - that is raw data. Extract the actual product name.
   - manufacturer_part_number: The SKU, part number, catalog number, item number, or style code from the invoice (e.g. "35-C410/L", "S-19310", "BC924MSH-BK"). Null if not present.
   - quantity: numeric qty ordered/shipped
   - original_uom: EA, BX, CS, PR, DZ, DP, CT, RL, etc. as shown. Null if unclear.
   - unit_price: price per unit
   - extended_price: line total

3. Do NOT invent MPN or descriptions. Extract only what is on the invoice.
4. Skip header rows, totals, subtotals, tax lines. Only real product line items with prices.
5. Handle OCR noise: ignore repeated characters (e.g. MMMaaagggiiiddd = Magid)."""

        user = f"""Extract supplier and line items from this invoice.
{f"Vendor hint from filename/headers: {hint_supplier}" if hint_supplier else ""}

RAW INVOICE TEXT:
{text[:12000]}

Return a JSON object with this exact structure:
{{
  "supplier_name": "Full Legal Company Name",
  "line_items": [
    {{
      "item_description": "Clean product description only",
      "manufacturer_part_number": "SKU or null",
      "quantity": 1,
      "original_uom": "EA",
      "unit_price": 1.99,
      "extended_price": 1.99
    }}
  ]
}}

Return ONLY the JSON object, no markdown, no explanation."""

        resp = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=8000,
        )
        content = (resp.choices[0].message.content or "").strip()
        if "```" in content:
            content = re.sub(r"```(?:json)?\s*", "", content).replace("```", "").strip()
        data = json.loads(content)

        supplier = str(data.get("supplier_name", hint_supplier or "Unknown Supplier")).strip()
        if not supplier or supplier.lower() in ("unknown", "null", "n/a"):
            supplier = hint_supplier or "Unknown Supplier"

        items: list[RawLineItem] = []
        for o in data.get("line_items") or []:
            try:
                desc = (o.get("item_description") or o.get("description") or "").strip()
                if not desc:
                    continue
                qty = float(o.get("quantity", 1))
                unit = o.get("unit_price")
                ext = o.get("extended_price")
                if ext is None and unit is not None and qty:
                    ext = float(unit) * qty
                elif unit is None and ext is not None and qty:
                    unit = float(ext) / qty
                mpn = o.get("manufacturer_part_number") or o.get("manufacturer_part")
                if mpn is not None and str(mpn).strip().lower() in ("null", "n/a", ""):
                    mpn = None
                items.append(
                    RawLineItem(
                        description=desc,
                        item_number=mpn,
                        manufacturer_part=mpn,
                        quantity=qty,
                        original_uom=o.get("original_uom"),
                        unit_price=float(unit) if unit is not None else None,
                        extended_price=float(ext) if ext is not None else None,
                        line_confidence=0.85,
                    )
                )
            except (TypeError, ValueError):
                continue

        return supplier, items
    except Exception:
        return hint_supplier or "Unknown Supplier", []


def extract_line_items_via_llm(
    text: str,
    supplier_name: Optional[str] = None,
) -> tuple[list[RawLineItem], str]:
    """
    Legacy entry point: use LLM to extract line items.
    Now delegates to extract_all_via_llm for full extraction.
    """
    supplier, items = extract_all_via_llm(text, supplier_name)
    return items, supplier
