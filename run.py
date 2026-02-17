#!/usr/bin/env python3
"""
Invoice PDF processing pipeline - CLI entry point.

Usage:
  python run.py                     # Process ./input, output to ./output
  python run.py --input Invoices --output ./output
  python run.py --input ./input --no-lookup-agent   # Skip agentic UOM lookup
  python run.py --input ./input --no-llm-fallback   # Skip LLM extraction fallback

Drop PDFs into the input folder and run to generate structured JSON per invoice.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.invoice_pipeline.pipeline import run_on_folder


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Process invoice PDFs and output structured JSON line items."
    )
    parser.add_argument(
        "--input",
        "-i",
        type=str,
        default="./input",
        help="Input directory containing PDF invoices (default: ./input)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="./output",
        help="Output directory for structured JSON files (default: ./output)",
    )
    parser.add_argument(
        "--no-lookup-agent",
        action="store_true",
        help="Disable agentic UOM lookup for missing/ambiguous units",
    )
    parser.add_argument(
        "--no-llm-fallback",
        action="store_true",
        help="Disable LLM fallback when parsers extract 0 line items",
    )
    parser.add_argument(
        "--no-llm-primary",
        action="store_true",
        help="Disable LLM primary extraction (use deterministic parsers only)",
    )
    parser.add_argument(
        "--parallel",
        "-j",
        type=int,
        default=1,
        metavar="N",
        help="Process N PDFs in parallel (default: 1)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        input_path.mkdir(parents=True, exist_ok=True)
        print(f"Created input directory: {input_path.absolute()}. Add PDFs and run again.")
        return

    results = run_on_folder(
        input_path,
        output_path,
        use_lookup_agent=not args.no_lookup_agent,
        use_llm_fallback=not args.no_llm_fallback,
        use_llm_primary=not args.no_llm_primary,
        max_workers=max(1, args.parallel),
    )

    total_items = sum(len(r.line_items) for r in results)
    print(f"Processed {len(results)} invoice(s). Output in: {output_path.absolute()}")
    for r in results:
        print(f"  - {r.source_file}: {len(r.line_items)} line items")
    if results:
        print(f"Total: {total_items} line items in {len(results)} file(s)")


if __name__ == "__main__":
    main()
