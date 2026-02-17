# Invoice Line Item Extraction Pipeline

An end-to-end system that ingests raw invoice PDFs, extracts line items, normalizes units of measure, and outputs structured JSON. Designed for diverse suppliers with inconsistent formats, OCR noise, and mixed or missing UOMs.

## What This Project Does

- **Input**: Raw invoice PDFs (drop into `./input`)
- **Output**: Structured JSON per invoice with normalized line items
- **Per line item**: supplier name, item description, manufacturer part number, original UOM, detected pack quantity, canonical base UOM (EA), price per base unit, confidence score, escalation flag

---

## Approach Chosen

### LLM-First Extraction

We use an **LLM as the primary extractor** rather than rule-based or format-specific parsers. The flow is:

1. **Extract text** from PDF (pdfplumber + OCR fallback for scanned docs)
2. **LLM extraction**: Single LLM call to extract supplier name and all line items with clean descriptions and MPN
3. **Fallback**: If LLM returns nothing, use a generic table parser, then LLM again if still empty
4. **UOM normalization**: Parse pack expressions (25/CS, PK10, 100PR/DP, etc.), normalize to EA
5. **Agentic lookup**: For lines with missing/ambiguous UOM, batch LLM call to infer pack from description
6. **Output**: Structured JSON with confidence scores and escalation flags

### Why This Approach

- **Supplier variety**: Invoices come from many suppliers (Magid, ULINE, Fastenal, MSC, etc.) with different layouts. A single LLM extraction adapts to any format without per-supplier code.
- **Quality**: LLM produces clean item descriptions and MPN instead of raw table dumps.
- **Maintainability**: No need to add or update parsers for each new supplier.
- **Robustness**: Handles OCR noise, free-text layouts, and "Price Per Hundred" style pricing via natural language understanding.

---

## Why Not Separate Parsers for Known Suppliers?

**Trade-off**: Format-specific parsers (Magid, ULINE, Fastenal, etc.) would avoid LLM calls for those suppliers, reducing cost and latency. But:

- **Scale**: In practice, there can be dozens or hundreds of suppliers. Building and maintaining a parser per supplier does not scale.
- **Change**: Invoice formats change over time; parsers require ongoing maintenance.
- **Coverage**: New suppliers would still need LLM or a generic parser.

**When parsers make sense**: If you have a **small, fixed set of suppliers** (e.g. 3–5) with stable formats, a parser-first approach can be more cost-effective. You could add supplier-specific parsers as a fast path and use LLM as fallback. For a general-purpose pipeline that must work with many unknown suppliers, LLM-first is more practical.

---

## Why OpenRouter LLM?

- **Provider flexibility**: OpenRouter is a gateway to multiple LLMs (GPT-4, Claude, etc.). You can switch models without code changes.
- **OpenAI-compatible API**: Drop-in replacement for the OpenAI SDK; minimal integration effort.
- **Unified key**: Single API key instead of managing keys for each provider.
- **Fallback**: Works with `OPENAI_API_KEY` if OpenRouter is not configured.

**Setup**: Add `OPENROUTER_API_KEY` to `.env`. The pipeline uses `openai/gpt-4o-mini` by default for cost-effective extraction.

---

## Assumptions

1. **PDFs are invoice-like**: Content includes line items with descriptions, quantities, and prices. Not designed for non-invoice PDFs.
2. **Text is extractable**: Native text or OCR; heavily image-only or handwritten invoices may fail.
3. **Base unit is EA**: All UOMs are normalized to “each” (EA). Weight/volume UOMs (LB, GAL) are escalated, not converted.
4. **Pack expressions are parseable**: Common patterns (25/CS, PK10, 100PR/DP, etc.) are supported. Unusual patterns may need escalation.
5. **LLM output is structured**: Prompts request JSON; malformed responses may reduce extraction quality.
6. **API availability**: LLM calls require network access and a valid API key.

---

## How to Run

### 1. Setup

```bash
# Clone or navigate to project
cd NegotiateAI_Project

# Create virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Key

Create a `.env` file in the project root:

```
OPENROUTER_API_KEY=your_key_here
```

Or use `OPENAI_API_KEY` for direct OpenAI access.

### 3. Add Invoices

Place PDF invoices in the input folder (default: `./input`):

```bash
cp your_invoices/*.pdf ./input/
```

### 4. Run the Pipeline

```bash
# Default: process ./input, output to ./output
python3 run.py

# Custom paths
python3 run.py --input ./Invoices --output ./output

# Parallel processing (2 workers)
python3 run.py -j 2

# Skip agentic UOM lookup (faster, fewer API calls)
python3 run.py --no-lookup-agent

# Use generic parser only (no LLM extraction)
python3 run.py --no-llm-primary
```

### 5. Output

JSON files are written to the output folder, one per invoice:

```
output/
  invoice_name_structured.json
```

Each file contains `source_file`, `supplier_name`, `line_items`, and `raw_metadata`.

---

## CLI Options

| Option | Description |
|--------|-------------|
| `--input`, `-i` | Input directory (default: `./input`) |
| `--output`, `-o` | Output directory (default: `./output`) |
| `--parallel`, `-j N` | Process N PDFs in parallel |
| `--no-lookup-agent` | Disable UOM lookup for ambiguous lines |
| `--no-llm-fallback` | Disable LLM when generic parser returns 0 items |
| `--no-llm-primary` | Use generic parser only (no LLM extraction) |

---

## Project Structure

```
NegotiateAI_Project/
├── run.py                 # CLI entry point
├── requirements.txt
├── .env                   # OPENROUTER_API_KEY or OPENAI_API_KEY
├── input/                 # Drop PDFs here
├── output/                # Structured JSON output
└── src/
    └── invoice_pipeline/
        ├── api_client.py      # Shared OpenAI client
        ├── extract.py         # PDF text + OCR extraction
        ├── llm_extract.py     # LLM-based line item extraction
        ├── lookup_agent.py    # Batch UOM lookup
        ├── models.py          # Pydantic output models
        ├── parsers.py         # Generic table parser (fallback)
        ├── pipeline.py        # Orchestration
        ├── supplier_detection.py  # Supplier name hint
        └── uom.py             # UOM normalization
```

---

## OCR Setup (Optional)

For scanned PDFs, install Tesseract:

- **macOS**: `brew install tesseract`
- **Ubuntu**: `sudo apt install tesseract-ocr`
- **Windows**: Download from [tesseract-ocr.github.io](https://tesseract-ocr.github.io/)
