import logging
import time
from typing import Any
import httpx
import config

logger = logging.getLogger(__name__)

BASE_URL = "https://api.henrikdev.xyz/valorant"

# In-memory TTL Cache: {cache_key: (timestamp, data)}
_CACHE: dict[str, tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 120  # 2 minutes cache


def _get_from_cache(key: str) -> Any | None:
    if key in _CACHE:
        ts, data = _CACHE[key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            return data
        del _CACHE[key]
    return None


def _set_to_cache(key: str, data: Any) -> None:
    _CACHE[key] = (time.time(), data)


def _get_headers() -> dict[str, str]:
    headers = {"User-Agent": "ValoTracker/2.0"}
    if config.HENRIK_API_KEY:
        headers["Authorization"] = config.HENRIK_API_KEY
    return headers


async def get_account(name: str, tag: str) -> dict[str, Any] | None:
    """
    Fetches account details (PUUID, region, card, level) by Riot Name and Tag.
    """
    cache_key = f"acc:{name.lower()}:{tag.lower()}"
    cached = _get_from_cache(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/v1/account/{name}/{tag}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=_get_headers())
            if resp.status_code == 200:
                data = resp.json().get("data")
                _set_to_cache(cache_key, data)
                return data
            logger.warning("Henrik API get_account failed (%s): %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Error in get_account for %s#%s: %s", name, tag, e)
    return None


async def get_account_by_puuid(puuid: str) -> dict[str, Any] | None:
    """
    Fetches account details by PUUID.
    """
    cache_key = f"acc_puuid:{puuid}"
    cached = _get_from_cache(cache_key)
    if cached is not None:
        return cached

    url = f"{BASE_URL}/v1/by-puuid/account/{puuid}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=_get_headers())
            if resp.status_code == 200:
                data = resp.json().get("data")
                _set_to_cache(cache_key, data)
                return data
            logger.warning("Henrik API get_account_by_puuid failed (%s): %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Error in get_account_by_puuid for %s: %s", puuid, e)
    return None


async def get_mmr(
    shard: str,
    puuid: str | None = None,
    name: str | None = None,
    tag: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    """
    Fetches MMR and rank details using PUUID (preferred) or Name#Tag.
    """
    cache_key = f"mmr:{puuid}" if puuid else f"mmr:{name.lower()}:{tag.lower()}"
    if not force_refresh:
        cached = _get_from_cache(cache_key)
        if cached is not None:
            return cached


    if puuid:
        url = f"{BASE_URL}/v2/by-puuid/mmr/{shard}/{puuid}"
    elif name and tag:
        url = f"{BASE_URL}/v2/mmr/{shard}/{name}/{tag}"
    else:
        return None

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=_get_headers())
            if resp.status_code == 200:
                data = resp.json().get("data")
                _set_to_cache(cache_key, data)
                return data
            logger.warning("Henrik API get_mmr failed (%s): %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Error in get_mmr: %s", e)
    return None


async def get_matches(shard: str, puuid: str | None = None, name: str | None = None, tag: str | None = None, size: int = 5) -> list[dict[str, Any]]:
    """
    Fetches the recent matches for a player (default 5 matches).
    """
    cache_key = f"matches:{puuid}:{size}" if puuid else f"matches:{name.lower()}:{tag.lower()}:{size}"
    cached = _get_from_cache(cache_key)
    if cached is not None:
        return cached

    if puuid:
        url = f"{BASE_URL}/v3/by-puuid/matches/{shard}/{puuid}?size={size}"
    elif name and tag:
        url = f"{BASE_URL}/v3/matches/{shard}/{name}/{tag}?size={size}"
    else:
        return []

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=_get_headers())
            if resp.status_code == 200:
                matches = resp.json().get("data", [])
                _set_to_cache(cache_key, matches)
                return matches
            logger.warning("Henrik API get_matches failed (%s): %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Error in get_matches: %s", e)
    return []


async def get_last_match(shard: str, puuid: str | None = None, name: str | None = None, tag: str | None = None) -> dict[str, Any] | None:
    """
    Fetches the most recent match for a player.
    """
    matches = await get_matches(shard=shard, puuid=puuid, name=name, tag=tag, size=1)
    return matches[0] if matches else None


async def get_stored_mmr_history(
    shard: str, puuid: str | None = None, name: str | None = None, tag: str | None = None
) -> list[dict[str, Any]]:
    """
    Fetches stored MMR history for a player to check RR changes per match.
    """
    cache_key = f"mmr_hist:{puuid}" if puuid else f"mmr_hist:{name.lower()}:{tag.lower()}"
    cached = _get_from_cache(cache_key)
    if cached is not None:
        return cached

    if puuid:
        url = f"{BASE_URL}/v1/by-puuid/stored-mmr-history/{shard}/{puuid}"
    elif name and tag:
        url = f"{BASE_URL}/v1/stored-mmr-history/{shard}/{name}/{tag}"
    else:
        return []

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers=_get_headers())
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                _set_to_cache(cache_key, data)
                return data
            logger.debug("Henrik API get_stored_mmr_history returned %s", resp.status_code)
        except Exception as e:
            logger.debug("Error in get_stored_mmr_history: %s", e)
    return []


# Regional Leaderboard Cache: {shard: (timestamp, data)}
_LEADERBOARD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
LEADERBOARD_CACHE_TTL = 600  # 10 minutes cache


def _normalize_leaderboard_data(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalizes Henrik API v3 leaderboard response to consistent dictionary."""
    d = raw.get("data", {})
    if isinstance(d, dict):
        raw_players = d.get("players", [])
        thresholds = d.get("thresholds", [])
        radiant_threshold = 0
        for th in thresholds:
            tier_info = th.get("tier", {})
            if isinstance(tier_info, dict) and tier_info.get("name") == "Radiant":
                radiant_threshold = th.get("threshold", 0)
            elif th.get("tier") == 27 or th.get("tier_name") == "Radiant":
                radiant_threshold = th.get("threshold", 0)

        total_players = raw.get("results", {}).get("total", len(raw_players))

        normalized_players = []
        for p in raw_players:
            p_norm = dict(p)
            p_norm["leaderboardRank"] = p.get("leaderboard_rank", p.get("leaderboardRank", 0))
            p_norm["IsAnonymized"] = p.get("is_anonymized", p.get("IsAnonymized", False))
            p_norm["gameName"] = p.get("name", p.get("gameName", "Player"))
            p_norm["tagLine"] = p.get("tag", p.get("tagLine", ""))
            p_norm["rankedRating"] = p.get("rr", p.get("rankedRating", 0))
            p_norm["numberOfWins"] = p.get("wins", p.get("numberOfWins", 0))
            normalized_players.append(p_norm)

        return {
            "total_players": total_players,
            "radiant_threshold": radiant_threshold,
            "last_update": int(time.time()),
            "players": normalized_players,
        }
    return raw


async def get_leaderboard(
    shard: str = "ap", name: str | None = None, tag: str | None = None
) -> dict[str, Any] | None:
    """
    Fetches the regional competitive leaderboard from Henrik API v3.
    Supports top players (size 200) or direct player lookup by name and tag.
    Returns normalized dict with 'radiant_threshold', 'total_players', 'players', etc.
    """
    s = shard.lower()
    now = time.time()
    cache_key = f"{s}:{name.lower() if name else ''}:{tag.lower() if tag else ''}"
    if cache_key in _LEADERBOARD_CACHE:
        ts, data = _LEADERBOARD_CACHE[cache_key]
        if now - ts < LEADERBOARD_CACHE_TTL:
            return data

    if name and tag:
        url = f"{BASE_URL}/v3/leaderboard/{s}/pc?name={name}&tag={tag}"
    else:
        url = f"{BASE_URL}/v3/leaderboard/{s}/pc?start_index=1&size=200"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=_get_headers())
            if resp.status_code == 200:
                raw_data = resp.json()
                norm_data = _normalize_leaderboard_data(raw_data)
                _LEADERBOARD_CACHE[cache_key] = (now, norm_data)
                return norm_data
            logger.warning("Henrik API get_leaderboard failed (%s): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.exception("Error in get_leaderboard for shard %s: %s", s, e)
    return None


