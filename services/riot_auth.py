import logging
import uuid
from typing import Any
import httpx

logger = logging.getLogger(__name__)

AUTH_URL = (
    "https://auth.riotgames.com/authorize"
    "?client_id=riot-client"
    "&redirect_uri=http%3A%2F%2Flocalhost%2Fredirect"
    "&response_type=code"
    "&scope=openid+link+ban+lol_region+account+offline_access"
    "&nonce="
)


def generate_login_url() -> str:
    """Generates the OAuth2 Riot authorization URL for logging in."""
    nonce = uuid.uuid4().hex
    return f"{AUTH_URL}{nonce}"


async def get_tokens(code: str) -> dict[str, Any] | None:
    """
    Exchanges the authorization code for an access token and refresh token.
    Returns: dict with 'access_token', 'refresh_token', 'expires_in' or None.
    """
    url = "https://auth.riotgames.com/token"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://localhost/redirect",
        "client_id": "riot-client",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, data=payload, headers=headers)
            if response.status_code == 200:
                return response.json()
            logger.error("Failed to get tokens. Status: %s - %s", response.status_code, response.text)
        except Exception as e:
            logger.exception("Error in get_tokens: %s", e)
    return None


async def refresh_tokens(refresh_token: str) -> dict[str, Any] | None:
    """
    Refreshes an expired access token using the stored refresh token.
    """
    url = "https://auth.riotgames.com/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "riot-client",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, data=payload, headers=headers)
            if response.status_code == 200:
                return response.json()
            logger.error("Failed to refresh tokens. Status: %s - %s", response.status_code, response.text)
        except Exception as e:
            logger.exception("Error in refresh_tokens: %s", e)
    return None


async def get_player_info(access_token: str) -> dict[str, Any] | None:
    """
    Fetches player user info (PUUID and shard/region affinity) from Riot.
    """
    url = "https://auth.riotgames.com/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            logger.error("Failed to get player info. Status: %s - %s", response.status_code, response.text)
        except Exception as e:
            logger.exception("Error in get_player_info: %s", e)
    return None


async def get_entitlement(access_token: str) -> str | None:
    """
    Fetches the Entitlement Token required to query player storefronts.
    """
    url = "https://entitlements.auth.riotgames.com/api/token/v1"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, headers=headers, json={})
            if response.status_code == 200:
                data = response.json()
                return data.get("entitlements_token")
            logger.error("Failed to get entitlement token. Status: %s - %s", response.status_code, response.text)
        except Exception as e:
            logger.exception("Error in get_entitlement: %s", e)
    return None


async def get_player_affinity(access_token: str, id_token: str | None = None) -> str:
    """
    Fetches the real player shard/region affinity ('ap', 'na', 'eu', 'kr', etc.)
    using Riot's Player Affinity Service (PAS).
    """
    if not id_token:
        return "ap"

    url = "https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"id_token": id_token}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.put(url, headers=headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                live_shard = data.get("affinities", {}).get("live")
                if live_shard:
                    logger.info("Player live shard determined from PAS: %s", live_shard)
                    return live_shard.lower()
            logger.warning("PAS request failed (status %s): %s", response.status_code, response.text)
        except Exception as e:
            logger.exception("Error in get_player_affinity: %s", e)

    return "ap"
