"""
Unit of measure normalization with safe conversion policy.

Policy:
1. EA-equivalent (safe): EA, EACH, UNIT, UN, PC, PCS, PIECE → pack_qty=1
2. Fixed multipliers: DOZ/DZ (×12), GROSS (×144), PR/PAIR (×2)
3. Pack/container: PK, BX, CS, CTN, BG, RL, DP, SET, KIT → convert only if pack_qty known, else escalate
4. Count (CT/CNT): ambiguous, treat as pack-based
5. Measurable (LB, GAL, FT, etc.): NOT convertible → price_per_base_unit=null, escalate
"""
from __future__ import annotations

import re
from typing import Optional

# --- UOM category sets ---

# 1) EA-equivalent: safe, pack_qty=1
UOM_EA_SAFE = frozenset({
    "EA", "EACH", "UNIT", "UN", "PC", "PCS", "PIECE", "ITEM",
})

# 2) Fixed multipliers (always safe conversion)
# Map: UOM -> (multiplier to EA, confidence)
UOM_FIXED_MULT = {
    "PR": (2.0, 0.95),
    "PAIR": (2.0, 0.95),
    "DZ": (12.0, 0.95),
    "DOZ": (12.0, 0.95),
    "DOZEN": (12.0, 0.95),
    "GR": (144.0, 0.95),
    "GROSS": (144.0, 0.95),
}

# 3) Pack/container: need pack_qty from description or lookup
# Aliases: CS/CASE, BX/BOX, PK/PACK, CTN/CT/CARTON, RL/ROL/ROLL, DP/DISP
UOM_PACK_CONTAINER = frozenset({
    "PK", "PACK", "PAC",
    "BX", "BOX",
    "CS", "CASE",
    "CTN", "CT", "CARTON",  # CT ambiguous: carton vs count
    "BG", "BAG",
    "RL", "ROL", "ROLL",
    "DP", "DISP", "DISPLAY",
    "SET", "KIT",
})

# 4) Count UOMs: ambiguous, treat as pack-based
UOM_COUNT = frozenset({"COUNT", "CNT"})

# 5) Measurable: NOT safely convertible to EA (dimension, weight, volume, time)
UOM_MEASURABLE = frozenset({
    "FT", "IN", "M", "CM", "MM", "YD", "METER", "METRE",
    "SF", "SQFT", "SQFT", "M2", "SQ", "SQM",
    "LB", "LBS", "OZ", "KG", "G", "GRAM", "GM",
    "GAL", "GALLON", "QT", "PT", "L", "LITER", "LITRE", "ML",
    "HR", "HRS", "HOUR", "MIN", "MINUTE",
})

# Normalize raw UOM string to canonical key for lookup
UOM_ALIASES = {
    "EA": "EA", "EACH": "EA", "UNIT": "EA", "UN": "EA", "PC": "EA", "PCS": "EA", "PIECE": "EA", "ITEM": "EA",
    "PR": "PR", "PAIR": "PR",
    "DZ": "DZ", "DOZ": "DZ", "DOZEN": "DZ",
    "GR": "GROSS", "GROSS": "GROSS",
    "CS": "CS", "CASE": "CS",
    "BX": "BX", "BOX": "BX",
    "PK": "PK", "PACK": "PK", "PAC": "PK",
    "CTN": "CT", "CT": "CT", "CARTON": "CT",
    "BG": "BG", "BAG": "BG",
    "RL": "RL", "ROL": "RL", "ROLL": "RL",
    "DP": "DP", "DISP": "DP", "DISPLAY": "DP",
}


def _normalize_uom_key(raw: str) -> Optional[str]:
    """Normalize UOM string to canonical key."""
    r = (raw or "").strip().upper()
    if not r:
        return None
    return UOM_ALIASES.get(r, r)


# --- Pack expression patterns ---

PACK_PR_DP = re.compile(r"(\d+)\s*PR\s*/\s*DP", re.IGNORECASE)
PACK_PR_BG = re.compile(r"(\d+)\s*PR\s*/\s*BG", re.IGNORECASE)
PACK_NUM_DENOM = re.compile(
    r"(\d+)\s*/\s*(?:CS|BX|BOX|CT|CASE|PK|PAC|DP|BG|RL|DZ|EA|PR|DISP?\.?|BG\.?)",
    re.IGNORECASE,
)
PACK_PK_NUM = re.compile(r"(?:PK|PAC)\s*(\d+)\b", re.IGNORECASE)
PACK_DENOM_NUM = re.compile(
    r"(?:CS|BX|BOX|CT|CASE|DP|BG|RL)\s*/\s*(\d+)",
    re.IGNORECASE,
)
PACK_NUM_EA = re.compile(r"(\d+)\s*EA(?:\s|/|$|[^\w])", re.IGNORECASE)
PACK_NUM_PR = re.compile(r"(\d+)\s*PR(?:\s+[A-Z]|\s*$|\s*/)", re.IGNORECASE)
PACK_1_PR = re.compile(r"1\s*/\s*PR\b", re.IGNORECASE)
PACK_BX_CS_NUM = re.compile(r"(?:BX|CS|CT|CASE)\s*(\d+)\b", re.IGNORECASE)
PACK_100_DISP = re.compile(r"100\s*/\s*DISP?\.?", re.IGNORECASE)
PACK_100_BG = re.compile(r"100\s*/\s*BG\.?", re.IGNORECASE)


def parse_pack_from_text(text: str | None) -> Optional[int]:
    """
    Extract pack quantity from text. Returns EA-equivalent when applicable.
    Handles: 25/CS, PK10, 100PR/DP, 100/DISP, 100/BG, 1/PR, 100 PR, 100/BX, CS/1000, 1000 EA.
    """
    if not text:
        return None

    for pat in (PACK_PR_DP, PACK_PR_BG):
        m = pat.search(text)
        if m and m.lastindex >= 1:
            return int(m.group(1)) * 2

    if PACK_1_PR.search(text):
        return 2

    m = PACK_NUM_DENOM.search(text)
    if m and m.lastindex >= 1:
        return int(m.group(1))

    m = PACK_PK_NUM.search(text)
    if m:
        return int(m.group(1))

    m = PACK_DENOM_NUM.search(text)
    if m:
        return int(m.group(1))

    m = PACK_NUM_EA.search(text)
    if m:
        return int(m.group(1))

    m = PACK_NUM_PR.search(text)
    if m:
        return int(m.group(1)) * 2

    m = PACK_BX_CS_NUM.search(text)
    if m:
        return int(m.group(1))

    if PACK_100_DISP.search(text) or PACK_100_BG.search(text):
        return 100

    return None


def is_measurable_uom(raw: Optional[str]) -> bool:
    """True if UOM is dimension/weight/volume/time - not convertible to EA."""
    key = _normalize_uom_key(raw or "")
    return key in UOM_MEASURABLE if key else False


def normalize_uom(
    original_uom: Optional[str],
    description: Optional[str],
) -> tuple[str, Optional[int], float, bool]:
    """
    Normalize UOM to canonical EA.
    Returns (canonical_uom, pack_quantity, confidence, convertible).
    convertible=False for measurable UOMs (LB, GAL, FT, etc.) - do not compute price_per_ea.
    """
    raw = (original_uom or "").strip().upper()
    key = _normalize_uom_key(raw)
    desc = (description or "").strip()
    pack_from_desc = parse_pack_from_text(desc) or parse_pack_from_text(original_uom or "")

    # 5) Measurable UOMs - NOT convertible
    if key and key in UOM_MEASURABLE:
        return ("EA", None, 0.0, False)

    # 1) EA-equivalent
    if key in UOM_EA_SAFE or (key == "EA"):
        return ("EA", pack_from_desc or 1, 1.0, True)

    # 2) Fixed multipliers (PR=2, DZ=12, GROSS=144)
    if key in UOM_FIXED_MULT:
        mult, conf = UOM_FIXED_MULT[key]
        pack = pack_from_desc if pack_from_desc is not None else int(mult)
        return ("EA", pack, conf, True)

    # 3) Pack/container
    if key in UOM_PACK_CONTAINER:
        if pack_from_desc is not None:
            return ("EA", pack_from_desc, 0.85, True)
        return ("EA", None, 0.5, True)  # convertible=False effectively via escalate when pack unknown

    # 4) Count - treat as pack-based
    if key in UOM_COUNT:
        if pack_from_desc is not None:
            return ("EA", pack_from_desc, 0.7, True)
        return ("EA", None, 0.4, True)

    # Unknown
    if pack_from_desc is not None:
        return ("EA", pack_from_desc, 0.6, True)
    return ("EA", None, 0.4, True)


def price_per_base_unit(
    extended_price: Optional[float],
    quantity: float,
    original_uom: Optional[str],
    pack_quantity: Optional[int],
    convertible: bool = True,
) -> tuple[Optional[float], bool]:
    """
    Compute price per base unit (EA).
    Returns (price_per_ea, conversion_unsafe).
    price_per_ea is None when UOM is not convertible (measurable) or when inputs invalid.
    """
    if not convertible or extended_price is None or extended_price <= 0 or quantity <= 0:
        return (None, True)

    raw = (original_uom or "").strip().upper()
    key = _normalize_uom_key(raw)
    unsafe = False

    base_units = quantity
    if pack_quantity is not None and pack_quantity > 0:
        base_units = quantity * pack_quantity
    else:
        if key in UOM_FIXED_MULT:
            mult, _ = UOM_FIXED_MULT[key]
            base_units = quantity * mult
        elif key in UOM_PACK_CONTAINER or key in UOM_COUNT:
            unsafe = True
        elif key in UOM_EA_SAFE or key == "EA" or not key:
            base_units = quantity

    if base_units <= 0:
        return (None, True)

    return (extended_price / base_units, unsafe)
