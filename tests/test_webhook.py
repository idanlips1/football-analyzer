"""Tests for webhook delivery."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from utils.webhook import deliver_webhook


@pytest.mark.asyncio
async def test_deliver_webhook_success() -> None:
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()

    with patch("utils.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = await deliver_webhook(
            "https://example.com/hook",
            {"job_id": "abc", "status": "completed"},
        )
        assert result is True
        mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_deliver_webhook_failure_retries() -> None:
    with patch("utils.webhook.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.side_effect = Exception("connection refused")
        mock_client_cls.return_value = mock_client

        result = await deliver_webhook(
            "https://example.com/hook",
            {"job_id": "abc", "status": "failed"},
            max_retries=2,
            base_delay=0.01,
        )
        assert result is False
        assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_deliver_webhook_no_url() -> None:
    result = await deliver_webhook(None, {"job_id": "abc"})
    assert result is False
