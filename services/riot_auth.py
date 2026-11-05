import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import logging
from typing import Any
import uuid

from database.db import get_account_by_id, update_account_shard, update_account_tokens
from database.models import UserAccount
from services.crypto import decrypt_token, encrypt_token
from services.http_client import get_http_client
from services.valorant_api import get_riot_user_agent

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. Configuration & Domain Types
# ==============================================================================

# Proactive expiration buffer (300 seconds / 5 minutes)
TOKEN_REFRESH_BUFFER = timedelta(seconds=300)


class SessionStatus(str, Enum):
    """Represents the authentication health state of an account session."""

    ACTIVE = "active"
    NEEDS_LOGIN = "needs_login"
    TRANSIENT_ERROR = "transient_error"


@dataclass(frozen=True)
class TokenBundle:
    """Strongly typed container for OAuth 2.0 token responses."""

    access_token: str
    refresh_token: str
    id_token: str | None
    expires_in: int
    token_expires: datetime


# ==============================================================================
# 2. Concurrency Lock & Single-Flight Deduplication Map
# ==============================================================================

# In-flight Task map (Key: puuid -> running asyncio.Task).
# Implements Promise/Task Deduplication (Single-Flight Pattern):
# If multiple concurrent requests trigger a refresh for the same PUUID within the same
# second, all requests subscribe to and await the exact same in-flight task instead of
# firing duplicate HTTP requests or replaying single-use refresh tokens.
_in_flight_tasks: dict[str, asyncio.Task[UserAccount | None]] = {}

# Per-PUUID mutex locks for serialization safety
_puuid_refresh_locks: dict[str, asyncio.Lock] = {}


def get_puuid_lock(puuid: str) -> asyncio.Lock:
    """Returns or creates an asyncio.Lock tied to a specific Riot PUUID."""
    if puuid not in _puuid_refresh_locks:
        _puuid_refresh_locks[puuid] = asyncio.Lock()
    return _puuid_refresh_locks[puuid]


# ==============================================================================
# 3. JWT & OAuth Utilities
# ==============================================================================


def decode_jwt_exp(token: str) -> datetime | None:
    """
    Extracts the 'exp' timestamp claim from a Riot JWT access token.

    :param token: Raw compact JWT string (header.payload.signature)
    :return: Expiration datetime in UTC timezone, or None if malformed
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "==="
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if exp is not None:
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
    """
    Generates the OAuth2 Riot authorization URL for logging in with offline_access scope.
    """
    nonce = uuid.uuid4().hex
    return f"{AUTH_URL}{nonce}"


# ==============================================================================
# 4. Core OAuth 2.0 Token Exchange & Refresh
# ==============================================================================


async def get_tokens(code: str) -> dict[str, Any] | None:
    """
    Exchanges the authorization code for an initial access token, refresh token, and id_token.

    :param code: OAuth2 authorization code extracted from redirect URI
    :return: JSON dict from Riot token endpoint, or None on failure
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
    Performs OAuth 2.0 Refresh Token Rotation (RTR) against Riot's token endpoint.

    Returns:
      - dict with 'access_token', 'refresh_token', etc. on 200 OK
      - dict with {'_terminal': True, 'error': ...} when Riot permanently rejects the token (invalid_grant)
      - None on transient network / rate-limit / 5xx errors (keeps stored token intact)
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


# ==============================================================================
# 5. Session Orchestrator (Single-Flight + Proactive Expiry + Atomic Rotation)
# ==============================================================================


async def _execute_account_refresh(account: UserAccount, force: bool) -> UserAccount | None:
    """
    Executes the critical section of a token refresh under a per-PUUID lock.
    - Atomically persists the rotated refresh token to SQLite before external calls.
    - Distinguishes terminal session death from transient network hiccups.
    """
    lock = get_puuid_lock(account.puuid)
    async with lock:
        fresh_acc = await get_account_by_id(account.id)
        if not fresh_acc:
            return None
        account = fresh_acc

        now_utc = datetime.now(timezone.utc)
        # Proactive check inside lock: if another task already refreshed it, reuse!
        if not force and account.token_expires > (now_utc + TOKEN_REFRESH_BUFFER):
            return account

        try:
            r_token = decrypt_token(account.encrypted_token, account.nonce, account.tag)
        except Exception as e:
            logger.error("Failed to decrypt refresh token for Account ID %s: %s", account.id, e)
            return None

        if not r_token or not r_token.strip():
            logger.info(
                "Account ID %s (%s) requires manual /login (session previously expired).",
                account.id,
                account.name,
            )
            return None

        token_data = await refresh_tokens(r_token)

        # 1. Transient network / 5xx / 429 error: keep stored credentials intact
        if not token_data:
            logger.warning(
                "Transient failure refreshing Account ID %s (%s); keeping stored credentials.",
                account.id,
                account.name,
            )
            return account if account.token_expires > now_utc else None

        # 2. Terminal error (invalid_grant): clean up dead refresh token to stop retry loops
        if token_data.get("_terminal"):
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

        # 3. Successful rotation: extract tokens & parse exact JWT exp
        new_access_token = token_data["access_token"]
        new_refresh_token = token_data.get("refresh_token") or r_token
        expires_in = token_data.get("expires_in", 3600)

        new_ct, new_nonce, new_tag = encrypt_token(new_refresh_token)
        new_expires = decode_jwt_exp(new_access_token) or (now_utc + timedelta(seconds=expires_in))

        # Atomic SQLite write: sync tokens across all duplicate PUUID links immediately
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

        # Detect real live region/shard via PAS if id_token provided
        id_token = token_data.get("id_token")
        if id_token:
            try:
                live_shard = await get_player_affinity(new_access_token, id_token)
                if live_shard and live_shard != account.shard:
                    await update_account_shard(account.id, live_shard)
                    account.shard = live_shard
            except Exception:
                pass

        logger.info(
            "Successfully rotated session for Account ID %s (%s, Shard: %s)",
            account.id,
            account.name,
            account.shard,
        )
        return account


async def refresh_account_session(account: UserAccount, force: bool = False) -> UserAccount | None:
    """
    High-level entrypoint for acquiring a fresh, authorized session for a Riot account.

    Architecture guarantees:
      1. Proactive Expiry Check: Inspects JWT 'exp' with a 300-second safety buffer.
         If still valid and force=False, returns in-memory account with 0 network calls.
      2. Single-Flight Deduplication: In-flight refresh requests for the same PUUID
         subscribe to the active Task, guaranteeing zero duplicate rotation calls.
      3. Refresh Token Rotation (RTR): Newly issued refresh token is committed atomically.
      4. Resilience: Preserves tokens on network drop, clears dead tokens on invalid_grant.

    :param account: The UserAccount record to validate / refresh
    :param force: Bypass expiry check and force token rotation (e.g. after 401 from API)
    :return: Fresh UserAccount with valid access_token, or None if login required
    """
    if not account or not account.id:
        return None

    now_utc = datetime.now(timezone.utc)
    # Proactive Check: If cached token is still valid within the 5-min buffer, reuse immediately
    if not force and account.access_token and account.token_expires > (now_utc + TOKEN_REFRESH_BUFFER):
        return account

    puuid = account.puuid

    # Single-Flight Deduplication: Join existing running task if one is already in-flight
    existing_task = _in_flight_tasks.get(puuid)
    if existing_task and not existing_task.done():
        logger.debug("[Single-Flight] Deduplicating refresh request for PUUID %s", puuid)
        return await existing_task

    # Create and register the in-flight task
    task = asyncio.create_task(_execute_account_refresh(account, force))
    _in_flight_tasks[puuid] = task

    try:
        return await task
    finally:
        # Deregister task once complete
        if _in_flight_tasks.get(puuid) is task:
            _in_flight_tasks.pop(puuid, None)


# ==============================================================================
# 6. Player Info & Entitlements Service
# ==============================================================================


async def get_player_info(access_token: str) -> dict[str, Any] | None:
    """
    Fetches player user info (PUUID and account details) from Riot's userinfo endpoint.
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
    Fetches the real player live shard/region ('ap', 'na', 'eu', 'kr', etc.)
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
