import asyncio
import base64
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from database.db import get_account_by_id, update_account_shard, update_account_tokens
from database.models import UserAccount
from services.crypto import decrypt_token, encrypt_token
from services.http_client import get_http_client
from services.valorant_api import get_riot_user_agent

logger = logging.getLogger(__name__)

# Token refresh safety window (5 minutes before expiry, matching Primordium & SkinPeek)
TOKEN_REFRESH_BUFFER = timedelta(minutes=5)

# Per-PUUID mutex locks to serialize token refreshes across all commands and background tasks
_puuid_refresh_locks: dict[str, asyncio.Lock] = {}


def get_puuid_lock(puuid: str) -> asyncio.Lock:
    if puuid not in _puuid_refresh_locks:
        _puuid_refresh_locks[puuid] = asyncio.Lock()
    return _puuid_refresh_locks[puuid]


def decode_jwt_exp(token: str) -> datetime | None:
    """Extracts the 'exp' timestamp claim from a Riot JWT access token."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "==="
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if exp:
            return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except Exception:
        pass
    return None


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
    user_agent = await get_riot_user_agent()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": user_agent,
    }

    client = get_http_client()
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
    Returns:
      - dict with 'access_token', 'refresh_token', etc. on 200 OK
      - dict with {'_terminal': True, 'error': ...} when Riot permanently rejects the token (invalid_grant)
      - None on transient network / rate-limit / 5xx errors
    """
    if not refresh_token or not refresh_token.strip():
        return {"_terminal": True, "error": "empty_refresh_token"}

    url = "https://auth.riotgames.com/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": "riot-client",
    }
    user_agent = await get_riot_user_agent()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": user_agent,
    }

    client = get_http_client()
    try:
        response = await client.post(url, data=payload, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("access_token"):
                return data
            return None

        logger.error("Failed to refresh tokens. Status: %s - %s", response.status_code, response.text)
        if response.status_code in (400, 401):
            try:
                err_data = response.json()
                err_code = err_data.get("error") if isinstance(err_data, dict) else None
            except Exception:
                err_code = None
            if err_code in ("invalid_grant", "bad_claims", "invalid_request"):
                return {"_terminal": True, "error": err_code}
    except Exception as e:
        logger.exception("Error in refresh_tokens: %s", e)
    return None


async def refresh_account_session(account: UserAccount, force: bool = False) -> UserAccount | None:
    """
    Thread/coroutine-safe session refresh for a Riot account.
    - Serializes refresh calls per PUUID using an asyncio Lock.
    - Reloads fresh account state from SQLite inside the lock to avoid replaying rotated tokens.
    - Persists rotated tokens across all rows sharing the PUUID immediately before any extra calls.
    - Clears dead refresh tokens on terminal 'invalid_grant' so background tasks never loop 401s.
    """
    if not account or not account.id:
        return None

    prev_access_token = account.access_token
    lock = get_puuid_lock(account.puuid)

    async with lock:
        fresh_acc = await get_account_by_id(account.id)
        if not fresh_acc:
            return None
        account = fresh_acc

        now_utc = datetime.now(timezone.utc)
        # If another coroutine already refreshed this account while we waited for the lock, reuse it!
        if account.token_expires > (now_utc + TOKEN_REFRESH_BUFFER):
            if not force or account.access_token != prev_access_token:
                return account

        try:
            r_token = decrypt_token(account.encrypted_token, account.nonce, account.tag)
        except Exception as e:
            logger.error("Failed to decrypt refresh token for Account ID %s: %s", account.id, e)
            return None

        # If refresh token was already cleared after a previous terminal invalid_grant, skip hitting Riot
        if not r_token or not r_token.strip():
            logger.info(
                "Account ID %s (%s) requires manual /login (session previously expired).",
                account.id,
                account.name,
            )
            return None

        token_data = await refresh_tokens(r_token)

        if not token_data:
            # Transient network / 5xx / 429 error: keep stored refresh token intact
            logger.warning(
                "Transient failure refreshing Account ID %s (%s); keeping stored credentials.",
                account.id,
                account.name,
            )
            return account if account.token_expires > now_utc else None

        if token_data.get("_terminal"):
            # Terminal invalid_grant: clear dead refresh token so we never loop 401s again
            logger.warning(
                "Session for Account ID %s (%s, Discord: %s) was revoked/expired (%s). Marking as requiring /login.",
                account.id,
                account.name,
                account.discord_user_id,
                token_data.get("error"),
            )
            empty_ct, empty_nonce, empty_tag = encrypt_token("")
            expired_time = datetime(1970, 1, 1, tzinfo=timezone.utc)
            await update_account_tokens(
                user_id=account.id,
                access_token="",
                token_expires=expired_time,
                encrypted_token=empty_ct,
                nonce=empty_nonce,
                tag=empty_tag,
                puuid=account.puuid,
            )
            return None

        new_access_token = token_data["access_token"]
        new_refresh_token = token_data.get("refresh_token") or r_token
        expires_in = token_data.get("expires_in", 3600)

        new_ct, new_nonce, new_tag = encrypt_token(new_refresh_token)
        new_expires = decode_jwt_exp(new_access_token) or (now_utc + timedelta(seconds=expires_in))

        # Commit rotated tokens immediately before any further network requests
        await update_account_tokens(
            user_id=account.id,
            access_token=new_access_token,
            token_expires=new_expires,
            encrypted_token=new_ct,
            nonce=new_nonce,
            tag=new_tag,
            puuid=account.puuid,
        )

        account.access_token = new_access_token
        account.token_expires = new_expires
        account.encrypted_token = new_ct
        account.nonce = new_nonce
        account.tag = new_tag

        id_token = token_data.get("id_token")
        if id_token:
            try:
                live_shard = await get_player_affinity(new_access_token, id_token)
                if live_shard and live_shard != account.shard:
                    await update_account_shard(account.id, live_shard)
                    account.shard = live_shard
            except Exception:
                pass

        logger.info("Successfully refreshed token for Account ID %s (%s, Shard: %s)", account.id, account.name, account.shard)
        return account


async def get_player_info(access_token: str) -> dict[str, Any] | None:
    """
    Fetches player user info (PUUID and shard/region affinity) from Riot.
    """
    url = "https://auth.riotgames.com/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}

    client = get_http_client()
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

    client = get_http_client()
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

    client = get_http_client()
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
