import logging
from typing import Any
import httpx
from services.valorant_api import get_client_version

logger = logging.getLogger(__name__)

# Base64 encoded platform header string used by Riot client
PLATFORM_HEADER = (
    "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjogIldpbmRvd3MiLA0K"
    "CSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5MDQyLjEuMjU2LjY0Yml0IiwNCgkicGxh"
    "dGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
)


async def get_storefront(
    shard: str,
    puuid: str,
    entitlement: str,
    access_token: str,
    client_version: str | None = None,
) -> dict[str, Any] | None:
    """
    Fetches the storefront for the specified player from Riot's private store endpoint.
    """
    if not client_version:
        client_version = await get_client_version()

    url = f"https://pd.{shard}.a.pvp.net/store/v3/storefront/{puuid}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "X-Riot-Entitlements-Jwt": entitlement,
        "X-Riot-ClientPlatform": PLATFORM_HEADER,
        "X-Riot-ClientVersion": client_version,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, headers=headers, json={})
            if response.status_code == 200:
                return response.json()
            logger.error("Storefront request failed. Status: %s - %s", response.status_code, response.text)
        except Exception as e:
            logger.exception("Error in get_storefront: %s", e)
    return None
