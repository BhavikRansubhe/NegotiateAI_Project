"""
Agentic UOM lookup: when UOM or pack is missing/ambiguous, attempt resolution.
- Clear decision logic for when to trigger
- Structured output, confidence scoring, escalation
- No hallucinated MPN or pack sizes (return null / escalate)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class LookupResult:
    """Structured result from agentic UOM lookup."""
    canonical_uom: str
    detected_pack_quantity: Optional[int]
    confidence: float
    escalation: bool


def parse_pack_from_description(description: str | None) -> Optional[int]:
    """Extract pack quantity from item description. Delegates to uom module for consistency."""
    from .uom import parse_pack_from_text
    return parse_pack_from_text(description)


def should_trigger_lookup(
    original_uom: Optional[str],
    pack_from_parsing: Optional[int],
    description: str | None,
) -> bool:
    """
    Decision logic: trigger agentic lookup when:
    - UOM is missing or ambiguous (container type without pack)
    - Pack quantity cannot be determined from invoice text
    - Description suggests pack info but we couldn't parse it
    Never trigger for measurable UOMs (LB, GAL, FT, etc.) - those escalate directly.
    """
    from .uom import is_measurable_uom, UOM_PACK_CONTAINER, UOM_COUNT

    if is_measurable_uom(original_uom):
        return False  # Escalate, don't lookup
    raw = (original_uom or "").strip().upper()
    desc = (description or "").strip()

    # Have clear UOM and pack -> no lookup
    if pack_from_parsing is not None and pack_from_parsing > 0:
        return False
    from .uom import UOM_EA_SAFE
    if raw in UOM_EA_SAFE and not any(c in desc.upper() for c in ["/", "PK", "PER"]):
        return False
    if raw in ("PR", "PAIR") and pack_from_parsing is None and "/" not in desc:
        return False
    if raw in ("DZ", "DOZEN"):
        return False

    # Trigger: missing UOM
    if not raw:
        return True
    # Trigger: container UOM (BX, CS, etc.) or count (CT/CNT) without pack
    if raw in UOM_PACK_CONTAINER or raw in UOM_COUNT:
        return pack_from_parsing is None
    # Trigger: ambiguous - numbers or / in description suggest pack
    if re.search(r"\d+\s*/\s*", desc) or re.search(r"PK\s*\d+|\d+\s*PR", desc, re.I):
        return pack_from_parsing is None

    return False


def _call_llm_for_uom(
    description: str,
    item_number: Optional[str],
    supplier_name: Optional[str],
) -> LookupResult:
    """
    Call LLM to infer UOM and pack from product description.
    Uses constrained prompt: no hallucination of MPN, only infer from description.
    """
    from .api_client import get_openai_client
    client = get_openai_client()
    if not client:
        return LookupResult(
            canonical_uom="EA",
            detected_pack_quantity=None,
            confidence=0.3,
            escalation=True,
        )

    try:

        system = """You are a UOM inference assistant. Given an invoice line item description, infer ONLY the unit of measure and pack quantity if clearly indicated in the description.

RULES:
- Output ONLY valid JSON: {"canonical_uom": "EA", "detected_pack_quantity": <int or null>, "confidence": <0.0-1.0>, "escalation": <bool>}
- canonical_uom: always "EA" (each) - we normalize everything to base units
- detected_pack_quantity: ONLY if explicitly in the description (e.g. "100/DP" -> 100, "25/CS" -> 25, "PK10" -> 10). If uncertain, use null.
- NEVER invent or guess MPN, SKU, or pack sizes not in the description.
- confidence: 0.9+ only if pack/UOM is explicit in text; 0.5-0.7 if inferred from product type; 0.3 if very uncertain.
- escalation: true if confidence < 0.6 or if you had to guess."""

        desc_snippet = (description or "")[:500]
        item_info = f"Item/SKU: {item_number}" if item_number else ""
        supplier_info = f"Supplier: {supplier_name}" if supplier_name else ""

        user = f"""Infer UOM and pack for this invoice line item. Do NOT invent data.

Description: {desc_snippet}
{item_info}
{supplier_info}

Return ONLY the JSON object, no other text."""

        resp = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        content = (resp.choices[0].message.content or "").strip()
        # Extract JSON from response
        if "```" in content:
            content = re.sub(r"```(?:json)?\s*", "", content).replace("```", "").strip()
        data = json.loads(content)

        return LookupResult(
            canonical_uom=data.get("canonical_uom", "EA"),
            detected_pack_quantity=data.get("detected_pack_quantity"),
            confidence=float(data.get("confidence", 0.5)),
            escalation=bool(data.get("escalation", True)),
        )
    except Exception:
        return LookupResult(
            canonical_uom="EA",
            detected_pack_quantity=None,
            confidence=0.3,
            escalation=True,
        )


def resolve_uom_agent(
    original_uom: Optional[str],
    description: str,
    item_number: Optional[str],
    supplier_name: Optional[str],
) -> LookupResult:
    """
    Agentic UOM resolution. Called when should_trigger_lookup is True.
    Uses LLM only; no web scraping to avoid brittleness and hallucination.
    Structured output, confidence, escalation.
    """
    # First try deterministic extraction from description
    pack = parse_pack_from_description(description)
    if pack is not None:
        return LookupResult(
            canonical_uom="EA",
            detected_pack_quantity=pack,
            confidence=0.85,
            escalation=False,
        )

    # LLM fallback
    return _call_llm_for_uom(description, item_number, supplier_name)


def _batch_call_llm_for_uom(
    items: list[tuple[int, str, Optional[str], Optional[str]]],
    supplier_name: Optional[str],
) -> dict[int, LookupResult]:
    """
    Batch LLM call for UOM lookup. items = [(idx, desc, item_number, original_uom), ...]
    Returns dict idx -> LookupResult.
    """
    from .api_client import get_openai_client
    client = get_openai_client()
    if not client or not items:
        return {idx: LookupResult("EA", None, 0.3, True) for idx, *_ in items}

    lines_text = []
    for idx, desc, item_no, _ in items:
        desc_snip = (desc or "")[:400]
        item_info = f" SKU: {item_no}" if item_no else ""
        lines_text.append(f"{idx}: {desc_snip}{item_info}")

    user = f"""Infer UOM and pack quantity for each line item. Supplier: {supplier_name or 'Unknown'}

Items (format "idx: description"):
{chr(10).join(lines_text)}

Return a JSON array with one object per item, in the SAME ORDER as above. Each object:
{{"canonical_uom": "EA", "detected_pack_quantity": <int or null>, "confidence": <0.0-1.0>, "escalation": <bool>}}

RULES:
- detected_pack_quantity: ONLY if explicitly in description (e.g. "100/DP" -> 100, "25/CS" -> 25). Null if uncertain.
- NEVER invent pack sizes. escalation: true if confidence < 0.6.
- Output ONLY the JSON array."""

    system = """You infer UOM and pack from invoice line descriptions. Output a JSON array of objects.
Each: {"canonical_uom": "EA", "detected_pack_quantity": int|null, "confidence": float, "escalation": bool}"""

    try:
        resp = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.1,
            max_tokens=min(2000, 200 + len(items) * 80),
        )
        content = (resp.choices[0].message.content or "").strip()
        if "```" in content:
            content = re.sub(r"```(?:json)?\s*", "", content).replace("```", "").strip()
        data = json.loads(content)
        arr = data if isinstance(data, list) else []
        result: dict[int, LookupResult] = {}
        default = LookupResult("EA", None, 0.3, True)
        for i, (idx, *_) in enumerate(items):
            if i < len(arr):
                o = arr[i]
                pq = o.get("detected_pack_quantity")
                try:
                    pack_val = int(float(pq)) if pq is not None else None
                except (TypeError, ValueError):
                    pack_val = None
                result[idx] = LookupResult(
                    canonical_uom=o.get("canonical_uom", "EA"),
                    detected_pack_quantity=pack_val,
                    confidence=float(o.get("confidence", 0.5)),
                    escalation=bool(o.get("escalation", True)),
                )
            else:
                result[idx] = default
        for idx, *_ in items:
            if idx not in result:
                result[idx] = default
        return result
    except Exception:
        return {idx: LookupResult("EA", None, 0.3, True) for idx, *_ in items}


def resolve_uom_agent_batch(
    raw_items: list,
    supplier_name: str,
    use_lookup_agent: bool,
) -> dict[int, LookupResult]:
    """
    Batch UOM resolution. Collects lines needing lookup, resolves deterministic first,
    then one LLM call for the rest. Returns dict line_index -> LookupResult.
    """
    from .uom import parse_pack_from_text

    need_llm: list[tuple[int, str, Optional[str], Optional[str]]] = []
    results: dict[int, LookupResult] = {}

    for i, raw in enumerate(raw_items):
        desc = raw.description or ""
        original_uom = raw.original_uom
        pack_from_desc = parse_pack_from_text(desc)

        if not use_lookup_agent or not should_trigger_lookup(original_uom, pack_from_desc, desc):
            continue

        pack = parse_pack_from_description(desc)
        if pack is not None:
            results[i] = LookupResult("EA", pack, 0.85, False)
        else:
            need_llm.append((i, desc, raw.item_number or raw.manufacturer_part, original_uom))

    if need_llm:
        batch_results = _batch_call_llm_for_uom(need_llm, supplier_name)
        results.update(batch_results)

    return results
