"""Webhook delivery with exponential backoff retry."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


async def deliver_webhook(
    url: str | None,
    payload: dict[str, Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> bool:
    """POST payload to webhook URL. Returns True on success, False on failure.

    Retries with exponential backoff. Failures are logged but never raised —
    webhook delivery does not affect job status.
    """
    if not url:
        return False

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                log.info("Webhook delivered to %s (attempt %d)", url, attempt + 1)
                return True
        except Exception:
            delay = base_delay * (4**attempt)  # 1s, 4s, 16s
            log.warning(
                "Webhook delivery to %s failed (attempt %d/%d), retrying in %.1fs",
                url,
                attempt + 1,
                max_retries,
                delay,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)

    log.error("Webhook delivery to %s failed after %d attempts", url, max_retries)
    return False
