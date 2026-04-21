"""
Shared httpx.AsyncClient — reused across all outbound HTTP calls (OAuth, webhooks, etc.).

Creating a new client per request does a full TCP+TLS handshake and skips connection
pooling. Reusing one client amortizes those costs. Lifecycle is tied to the FastAPI app.
"""

import httpx

_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Return the process-wide AsyncClient. Creates on first use."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            follow_redirects=False,
        )
    return _client


async def close_http_client() -> None:
    """Close the shared client on app shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
