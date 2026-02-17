"""
Pydantic models for structured invoice output.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class RawLineItem(BaseModel):
    """Internal representation of a parsed line item before normalization."""
    description: Optional[str] = None
    item_number: Optional[str] = None
    manufacturer_part: Optional[str] = None
    quantity: float = 1.0
    original_uom: Optional[str] = None
    unit_price: Optional[float] = None
    extended_price: Optional[float] = None
    line_confidence: float = 1.0


class LineItemOutput(BaseModel):
    """Structured output per line item as required by spec."""
    supplier_name: str = Field(description="Extracted and normalized supplier name")
    item_description: str = Field(description="Cleaned item description")
    manufacturer_part_number: Optional[str] = Field(
        default=None,
        description="MPN if extractable, otherwise null"
    )
    original_uom: Optional[str] = Field(
        default=None,
        description="Original UOM as present on invoice"
    )
    detected_pack_quantity: Optional[int] = Field(
        default=None,
        description="Pack quantity if applicable (e.g. 25 per case)"
    )
    canonical_base_uom: str = Field(
        default="EA",
        description="Normalized to EA (each) as base unit"
    )
    price_per_base_unit: Optional[float] = Field(
        default=None,
        description="Price per single base unit (EA). Null when UOM not convertible (e.g. LB, GAL)"
    )
    confidence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in extraction and normalization"
    )
    escalation_flag: bool = Field(
        default=False,
        description="True if human review recommended (ambiguous UOM, low confidence)"
    )


class InvoiceResult(BaseModel):
    """Result for a single processed invoice."""
    source_file: str
    supplier_name: str
    line_items: list[LineItemOutput]
    raw_metadata: dict = Field(default_factory=dict)
