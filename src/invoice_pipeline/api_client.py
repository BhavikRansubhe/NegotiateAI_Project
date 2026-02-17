"""
Shared OpenAI API client. Lazily initialized and reused across extraction and lookup.
"""
from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_client: Optional["OpenAI"] = None


def get_openai_client() -> Optional["OpenAI"]:
    """Return shared OpenAI client, or None if no API key."""
    global _client
    if _client is not None:
        return _client
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    from openai import OpenAI
    base_url = "https://openrouter.ai/api/v1" if os.getenv("OPENROUTER_API_KEY") else None
    _client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    return _client
