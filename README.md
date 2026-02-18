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

### Getting a Free API Key from OpenRouter

[OpenRouter](https://openrouter.ai/) provides a unified API to access 300+ models from 60+ providers. You can get started with free credits:

1. **Sign up**: Go to [https://openrouter.ai/](https://openrouter.ai/) and click **Get API Key** or **Sign up**. Sign in with Google or GitHub.

2. **Add credits**: New accounts receive free credits. You can also add credits via **Buy credits** (e.g. $10 minimum) for ongoing use.

3. **Create an API key**: In the dashboard, create an API key. It will look like `sk-or-v1-...`.

4. **Use in this project**: Copy the key and add it to your `.env` file:
   ```
   OPENROUTER_API_KEY=sk-or-v1-your-key-here
   ```

5. **Run the pipeline**: The project uses the standard OpenAI SDK; OpenRouter is fully compatible. No code changes needed.

OpenRouter offers better prices and uptime across providers, and you can switch models (e.g. `anthropic/claude-3-haiku`, `google/gemini-flash`) by changing the model ID in the code if desired.

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
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

See [Getting a Free API Key from OpenRouter](#getting-a-free-api-key-from-openrouter) above for signup and setup. You can also use `OPENAI_API_KEY` for direct OpenAI access.

### Troubleshooting: Output Quality and `parser` Field

Each output JSON includes `raw_metadata.parser` indicating which extraction path was used:

| `parser` value   | Meaning |
|------------------|---------|
| `llm_primary`    | LLM extraction succeeded. Best quality (clean descriptions, MPN). |
| `llm_fallback`   | Generic parser returned 0 items; LLM extracted as fallback. |
| `generic`        | LLM returned nothing; generic table parser was used. Lower quality. |

**If you see `"parser": "generic"` instead of `"parser": "llm_primary"`** — extraction quality will be lower (raw table dumps, no MPN). This usually means:

- **API key is missing or invalid**: Check that `OPENROUTER_API_KEY` (or `OPENAI_API_KEY`) is set correctly in `.env`
- **API key is expired** or out of credits
- **Network issues** or API unavailability

**Fix**: Verify your `.env` file, regenerate the API key at [OpenRouter](https://openrouter.ai/), and ensure you have credits. Re-run the pipeline.

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

# Parallel processing (2 workers) (recommended for faster processing)
python3 run.py -j 2

# Skip agentic UOM lookup (faster, fewer API calls)  (Not recommended)
python3 run.py --no-lookup-agent

# Use generic parser only (no LLM extraction) (Not recommended)
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

## Evaluation Criteria — How This Project Addresses Them

### Does it actually run?

**Yes.** Single entry point: `python3 run.py`. Place PDFs in `./input`, run, get JSON in `./output`. No database or external services beyond the LLM API. Works without API key (falls back to generic parser; see Troubleshooting).

```bash
pip install -r requirements.txt
# Add OPENROUTER_API_KEY to .env
python3 run.py --input ./input --output ./output
```

---

### Can it handle unseen invoices?

**Yes.** LLM-first extraction adapts to new formats without code changes. No supplier-specific parsers. Tested on Magid, ULINE, Fastenal, MSC, Delta Industrial, and synthetic invoices (free-text, Price Per Hundred, OCR noise). Generic parser handles standard table layouts as fallback.

---

### How does it behave when UOM is missing?

1. **Deterministic extraction first**: Pack expressions (25/CS, PK10, 100PR/DP) parsed from description.
2. **Agentic lookup**: Lines with missing/ambiguous UOM trigger a batched LLM lookup (one call per invoice).
3. **Escalation**: If lookup returns low confidence or no pack, `escalation_flag=true` and `price_per_base_unit` may be null for measurable UOMs (LB, GAL, FT).
4. **No guessing**: For container UOMs (BX, CS) without pack, we escalate rather than assume pack=1.

---

### Is the lookup agent safe and structured, or prone to hallucination?

**Safe and structured.**

- **Explicit prompts**: “ONLY if explicitly in the description”, “NEVER invent pack sizes”, “Output ONLY valid JSON”.
- **Structured output**: JSON schema enforced (`canonical_uom`, `detected_pack_quantity`, `confidence`, `escalation`).
- **Deterministic first**: Tries `parse_pack_from_description()` before any LLM call.
- **Low confidence → escalate**: If confidence < 0.6, escalation is set.
- **Batch calling**: One LLM call for all ambiguous lines; reduces inconsistent behavior.

---

### Does it escalate correctly instead of guessing?

**Yes.** Escalation is set when:

- UOM is measurable (LB, GAL, FT, etc.) — no conversion to EA.
- Container UOM (BX, CS, CT) without known pack.
- Confidence score < 0.6.
- Price conversion is marked unsafe (e.g. guessed pack).
- Lookup agent returns `escalation: true`.

`escalation_flag=true` indicates human review; no invented MPNs or pack sizes.

---

### Is the system modular and production-oriented?

**Yes.**

| Aspect | Implementation |
|--------|----------------|
| **Modularity** | Separate modules: `extract`, `parsers`, `llm_extract`, `lookup_agent`, `uom`, `pipeline`. Swappable components. |
| **CLI** | `run.py` with `--input`, `--output`, `--parallel`, `--no-lookup-agent`, etc. |
| **Error handling** | Per-invoice try/except; errors written to output JSON. |
| **Parallelism** | `ThreadPoolExecutor` via `-j N` for batch processing. |
| **API client reuse** | Shared `get_openai_client()` for extraction and lookup. |
| **Output schema** | Pydantic models; consistent JSON structure. |
| **Requirements** | Pinned versions in `requirements.txt`. |

---

## OCR Setup (Optional)

For scanned PDFs, install Tesseract:

- **macOS**: `brew install tesseract`
- **Ubuntu**: `sudo apt install tesseract-ocr`
- **Windows**: Download from [tesseract-ocr.github.io](https://tesseract-ocr.github.io/)
