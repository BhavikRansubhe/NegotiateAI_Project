"""
End-to-end pipeline: PDF -> extract -> detect supplier -> parse lines -> normalize UOM -> output JSON.
When deterministic parsers return no items, falls back to generic parser then to LLM.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .extract import extract_text_from_pdf
from .supplier_detection import detect_supplier
from .parsers import extract_line_items
from .uom import (
    normalize_uom,
    parse_pack_from_text,
    price_per_base_unit,
    is_measurable_uom,
    UOM_PACK_CONTAINER,
)
from .lookup_agent import (
    should_trigger_lookup,
    resolve_uom_agent,
    resolve_uom_agent_batch,
    parse_pack_from_description,
)
from .models import LineItemOutput, InvoiceResult
from .llm_extract import extract_all_via_llm, extract_line_items_via_llm


def process_invoice_pdf(
    pdf_path: str | Path,
    use_lookup_agent: bool = True,
    use_llm_fallback: bool = True,
    use_llm_primary: bool = True,
) -> InvoiceResult:
    """
    Process a single invoice PDF and return structured result.
    When use_llm_primary is True, uses LLM first for intelligent extraction (supplier, MPN, clean descriptions).
    When use_lookup_agent is True, lines with missing/ambiguous UOM trigger agentic lookup.
    When use_llm_fallback is True and parsers return 0 items, uses LLM as fallback.
    """
    path = Path(pdf_path)
    text = extract_text_from_pdf(path)
    hint_supplier = detect_supplier(text)

    raw_items: list = []
    supplier_name = hint_supplier
    parser_used = "llm_primary"

    # Primary: LLM extraction for high-quality supplier name, item description, MPN
    if use_llm_primary:
        supplier_name, raw_items = extract_all_via_llm(text, hint_supplier)

    # Fallback: generic parser if LLM returned nothing
    if len(raw_items) == 0:
        raw_items = extract_line_items(text)
        parser_used = "generic"
        supplier_name = hint_supplier

        if len(raw_items) == 0 and use_llm_fallback:
            raw_items, supplier_name = extract_line_items_via_llm(text, supplier_name)
            if raw_items:
                parser_used = "llm_fallback"

    # Batch UOM lookups: collect lines needing lookup, resolve in one LLM call
    lookup_results = resolve_uom_agent_batch(raw_items, supplier_name, use_lookup_agent)

    line_outputs: list[LineItemOutput] = []
    for i, raw in enumerate(raw_items):
        desc = raw.description or ""
        original_uom = raw.original_uom
        pack_from_desc = parse_pack_from_text(desc) or parse_pack_from_description(desc)

        # Measurable UOMs (LB, GAL, FT, etc.) - not convertible, escalate
        if is_measurable_uom(original_uom):
            canonical_uom = "EA"
            pack_qty = None
            confidence = 0.3
            escalate = True
            price_per_ea = None
        elif i in lookup_results:
            lookup = lookup_results[i]
            canonical_uom = lookup.canonical_uom
            pack_qty = lookup.detected_pack_quantity
            confidence = lookup.confidence * raw.line_confidence
            escalate = lookup.escalation
            price_per_ea, conversion_unsafe = price_per_base_unit(
                raw.extended_price, raw.quantity, original_uom, pack_qty, convertible=True
            )
            if conversion_unsafe:
                escalate = True
        else:
            canonical_uom, pack_qty, uom_conf, convertible = normalize_uom(original_uom, desc)
            if pack_qty is None and pack_from_desc is not None:
                pack_qty = pack_from_desc
            confidence = uom_conf * raw.line_confidence
            escalate = False
            raw_uom = (original_uom or "").strip().upper()
            if canonical_uom == "EA" and pack_qty is None and raw_uom in UOM_PACK_CONTAINER:
                escalate = True

            price_per_ea, conversion_unsafe = price_per_base_unit(
                raw.extended_price,
                raw.quantity,
                original_uom,
                pack_qty,
                convertible=convertible,
            )
            if conversion_unsafe:
                escalate = True

        if confidence < 0.6:
            escalate = True

        line_outputs.append(
            LineItemOutput(
                supplier_name=supplier_name,
                item_description=desc,
                manufacturer_part_number=raw.manufacturer_part or raw.item_number,
                original_uom=original_uom,
                detected_pack_quantity=pack_qty,
                canonical_base_uom=canonical_uom or "EA",
                price_per_base_unit=round(price_per_ea, 4) if price_per_ea is not None else None,
                confidence_score=round(min(1.0, confidence), 2),
                escalation_flag=escalate,
            )
        )

    return InvoiceResult(
        source_file=path.name,
        supplier_name=supplier_name,
        line_items=line_outputs,
        raw_metadata={"parser": parser_used},
    )


def _process_one(
    pdf_path: Path,
    output_path: Path,
    use_lookup_agent: bool,
    use_llm_fallback: bool,
    use_llm_primary: bool,
) -> InvoiceResult:
    """Process a single PDF. Used by parallel executor."""
    try:
        result = process_invoice_pdf(
            pdf_path,
            use_lookup_agent=use_lookup_agent,
            use_llm_fallback=use_llm_fallback,
            use_llm_primary=use_llm_primary,
        )
        out_file = output_path / f"{pdf_path.stem}_structured.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(), f, indent=2)
        return result
    except Exception as e:
        result = InvoiceResult(
            source_file=pdf_path.name,
            supplier_name="Error",
            line_items=[],
            raw_metadata={"error": str(e)},
        )
        out_file = output_path / f"{pdf_path.stem}_structured.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "source_file": pdf_path.name,
                    "supplier_name": "Error",
                    "line_items": [],
                    "raw_metadata": {"error": str(e)},
                },
                f,
                indent=2,
            )
        return result


def run_on_folder(
    input_dir: str | Path,
    output_dir: str | Path,
    use_lookup_agent: bool = True,
    use_llm_fallback: bool = True,
    use_llm_primary: bool = True,
    max_workers: int = 1,
) -> list[InvoiceResult]:
    """Process all PDFs in input_dir and write JSON per invoice to output_dir.
    When max_workers > 1, processes PDFs in parallel."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        input_path.mkdir(parents=True, exist_ok=True)
        return []

    pdfs = sorted(input_path.glob("*.pdf"))
    if not pdfs:
        return []

    if max_workers <= 1:
        return [
            _process_one(p, output_path, use_lookup_agent, use_llm_fallback, use_llm_primary)
            for p in pdfs
        ]

    results: list[InvoiceResult] = [None] * len(pdfs)  # type: ignore
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(
                _process_one, p, output_path, use_lookup_agent, use_llm_fallback, use_llm_primary
            ): i
            for i, p in enumerate(pdfs)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = InvoiceResult(
                    source_file=pdfs[idx].name,
                    supplier_name="Error",
                    line_items=[],
                    raw_metadata={"error": str(e)},
                )
    return results
