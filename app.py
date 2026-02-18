from __future__ import annotations

import json
import os
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path

import streamlit as st

from src.invoice_pipeline.pipeline import process_invoice_pdf


@dataclass(frozen=True)
class _UiInvoice:
    filename: str
    result: dict


def _has_llm_key() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY"))


def _result_metrics(result: dict) -> dict:
    items = result.get("line_items") or []
    escalations = sum(1 for li in items if li.get("escalation_flag"))
    confidences = [li.get("confidence_score") for li in items if isinstance(li.get("confidence_score"), (int, float))]
    avg_conf = round(sum(confidences) / len(confidences), 2) if confidences else None
    return {
        "line_items": len(items),
        "escalations": escalations,
        "avg_confidence": avg_conf,
    }


def _zip_results(invoices: list[_UiInvoice]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for inv in invoices:
            stem = Path(inv.filename).stem
            zf.writestr(
                f"{stem}_structured.json",
                json.dumps(inv.result, indent=2, ensure_ascii=False),
            )
    return buf.getvalue()


st.set_page_config(
    page_title="Invoice Extraction UI",
    page_icon="🧾",
    layout="wide",
)

st.title("Invoice Line Item Extraction")
st.caption("Upload invoice PDFs → extract line items → preview + download structured JSON.")

with st.sidebar:
    st.header("Settings")
    st.write(
        "This UI runs the same pipeline as `run.py`, but without writing to `./output` unless you download results."
    )

    llm_primary = st.toggle("Use LLM primary extraction (best quality)", value=True)
    llm_fallback = st.toggle("Use LLM fallback (if deterministic parsers find 0 items)", value=True)
    lookup_agent = st.toggle("Use agentic UOM lookup for ambiguous lines", value=True)

    st.divider()
    st.subheader("Performance")
    num_workers = st.slider("Parallel workers", min_value=1, max_value=4, value=1, help="Number of invoices to process in parallel (1-4)")

    st.divider()
    st.subheader("LLM key status")
    if _has_llm_key():
        st.success("API key detected in environment.")
    else:
        st.warning(
            "No `OPENROUTER_API_KEY` / `OPENAI_API_KEY` found. LLM steps will return empty results; "
            "disable LLM toggles to rely on deterministic parsers."
        )

st.divider()

uploads = st.file_uploader(
    "Upload one or more invoice PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

col_a, col_b, col_c = st.columns([1, 1, 2])
with col_a:
    run_btn = st.button("Process invoices", type="primary", disabled=not uploads)
with col_b:
    clear_btn = st.button("Clear results")

if clear_btn:
    st.session_state.pop("ui_results", None)
    st.rerun()

if run_btn and uploads:
    ui_results: list[_UiInvoice] = []
    
    def _process_one_pdf(up) -> _UiInvoice:
        """Process a single uploaded PDF."""
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            tmp_pdf = tmp_dir / up.name
            tmp_pdf.write_bytes(up.getvalue())
            r = process_invoice_pdf(
                tmp_pdf,
                use_lookup_agent=lookup_agent,
                use_llm_fallback=llm_fallback,
                use_llm_primary=llm_primary,
            )
            return _UiInvoice(filename=up.name, result=r.model_dump())
    
    with st.spinner(f"Processing {len(uploads)} PDF(s) with {num_workers} worker(s)…"):
        if num_workers == 1:
            # Sequential processing
            for up in uploads:
                ui_results.append(_process_one_pdf(up))
        else:
            # Parallel processing - preserve upload order
            results_dict: dict[int, _UiInvoice] = {}
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                future_to_idx = {executor.submit(_process_one_pdf, up): i for i, up in enumerate(uploads)}
                for future in as_completed(future_to_idx):
                    idx = future_to_idx[future]
                    try:
                        results_dict[idx] = future.result()
                    except Exception as e:
                        up = uploads[idx]
                        error_result = _UiInvoice(
                            filename=up.name,
                            result={
                                "source_file": up.name,
                                "supplier_name": "Error",
                                "line_items": [],
                                "raw_metadata": {"error": str(e)},
                            },
                        )
                        results_dict[idx] = error_result
            # Sort by index to preserve upload order
            ui_results = [results_dict[i] for i in sorted(results_dict.keys())]

    st.session_state["ui_results"] = [asdict(x) for x in ui_results]

raw = st.session_state.get("ui_results") or []
results: list[_UiInvoice] = [_UiInvoice(**x) for x in raw]  # type: ignore[arg-type]

if not results:
    st.info("Upload PDFs and click **Process invoices** to see results.")
    st.stop()

zip_bytes = _zip_results(results)
st.download_button(
    "Download all JSON (zip)",
    data=zip_bytes,
    file_name="invoice_structured_outputs.zip",
    mime="application/zip",
)

tabs = st.tabs([f"{i + 1}. {r.filename}" for i, r in enumerate(results)])
for tab, inv in zip(tabs, results, strict=False):
    with tab:
        meta = inv.result.get("raw_metadata") or {}
        metrics = _result_metrics(inv.result)

        top = st.container()
        with top:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Supplier", inv.result.get("supplier_name") or "—")
            c2.metric("Line items", metrics["line_items"])
            c3.metric("Escalations", metrics["escalations"])
            c4.metric("Avg confidence", metrics["avg_confidence"] if metrics["avg_confidence"] is not None else "—")

            if meta.get("error"):
                st.error(f"Pipeline error: {meta.get('error')}")
            else:
                st.caption(f"Parser: `{meta.get('parser', 'unknown')}`")

        st.subheader("Line items")
        only_escalations = st.checkbox("Show only escalations", value=False, key=f"esc_{inv.filename}")

        items = inv.result.get("line_items") or []
        if only_escalations:
            items = [li for li in items if li.get("escalation_flag")]

        st.dataframe(items, use_container_width=True, hide_index=True)

        st.subheader("Structured JSON")
        st.json(inv.result)

        st.download_button(
            "Download this invoice JSON",
            data=json.dumps(inv.result, indent=2, ensure_ascii=False).encode("utf-8"),
            file_name=f"{Path(inv.filename).stem}_structured.json",
            mime="application/json",
            key=f"dl_{inv.filename}",
        )
