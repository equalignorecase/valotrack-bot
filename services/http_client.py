import asyncio
import httpx

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def get_http_client() -> httpx.AsyncClient:
    """
    Returns a shared httpx.AsyncClient bound to the current running event loop.
    Reuses TCP/TLS connections across Riot API, Henrik API, and asset downloads.
    """
    global _client, _client_loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if (
        _client is None
        or _client.is_closed
        or (_client_loop is not None and _client_loop is not current_loop)
    ):
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            ),
        )
        _client_loop = current_loop

    return _client


async def close_http_client() -> None:
    """Closes the shared httpx.AsyncClient on bot shutdown."""
    global _client, _client_loop
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
    _client_loop = None

