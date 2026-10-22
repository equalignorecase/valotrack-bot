import logging
from typing import Any
import httpx

logger = logging.getLogger(__name__)

# In-memory cache for all weapon skins with content tiers
_SKIN_MAP: dict[str, dict[str, Any]] = {}


async def get_client_version() -> str:
    """
    Fetches the current Riot client version from valorant-api.com.
    """
    url = "https://valorant-api.com/v1/version"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("riotClientVersion", "")
            logger.error("Failed to fetch client version. Status: %s", response.status_code)
        except Exception as e:
            logger.exception("Error in get_client_version: %s", e)
    return ""


async def _init_skin_map():
    global _SKIN_MAP
    if _SKIN_MAP:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # 1. Fetch content tiers (rarities & colors)
            tier_resp = await client.get("https://valorant-api.com/v1/contenttiers")
            tier_data = tier_resp.json().get("data", []) if tier_resp.status_code == 200 else []
            tier_map = {}
            for t in tier_data:
                uuid = t.get("uuid")
                raw_color = t.get("highlightColor") or "5A9FE233"
                hex_color = "#" + raw_color[:6]
                tier_map[uuid] = {
                    "name": t.get("displayName", "Select Edition"),
                    "color": hex_color,
                    "icon": t.get("displayIcon"),
                }

            # 2. Fetch all weapon skins with levels
            skins_resp = await client.get("https://valorant-api.com/v1/weapons/skins")
            skins_data = skins_resp.json().get("data", []) if skins_resp.status_code == 200 else []
            for s in skins_data:
                tier_info = tier_map.get(
                    s.get("contentTierUuid"),
                    {"name": "Select Edition", "color": "#5A9FE2", "icon": None},
                )
                for lvl in s.get("levels", []):
                    lvl_id = lvl.get("uuid")
                    if lvl_id:
                        _SKIN_MAP[lvl_id] = {
                            "name": s.get("displayName", "Unknown Skin"),
                            "icon": lvl.get("displayIcon") or s.get("displayIcon") or "",
                            "tier_name": tier_info["name"],
                            "tier_color": tier_info["color"],
                            "tier_icon": tier_info["icon"],
                        }
            logger.info("Loaded %d weapon skins into memory cache", len(_SKIN_MAP))
        except Exception as e:
            logger.exception("Failed to load weapon skins map: %s", e)


async def get_weapon_info(skin_level_id: str) -> dict[str, Any]:
    """
    Fetches weapon details including name, render icon, tier name, and tier color.
    """
    if not _SKIN_MAP:
        await _init_skin_map()

    if skin_level_id in _SKIN_MAP:
        return _SKIN_MAP[skin_level_id]

    # Fallback to direct skinlevel endpoint if not in map
    url = f"https://valorant-api.com/v1/weapons/skinlevels/{skin_level_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json().get("data", {})
                res = {
                    "name": data.get("displayName", "Unknown Skin"),
                    "icon": data.get("displayIcon", "") or "",
                    "tier_name": "Select Edition",
                    "tier_color": "#5A9FE2",
                    "tier_icon": None,
                }
                _SKIN_MAP[skin_level_id] = res
                return res
        except Exception as e:
            logger.exception("Error fetching weapon info for %s: %s", skin_level_id, e)

    return {
        "name": "Unknown Skin",
        "icon": "",
        "tier_name": "Select Edition",
        "tier_color": "#5A9FE2",
        "tier_icon": None,
    }


# In-memory caches for agents, maps, and competitive tiers
_AGENT_MAP: dict[str, dict[str, Any]] = {}
_MAP_MAP: dict[str, dict[str, Any]] = {}
_TIER_MAP: dict[int, dict[str, Any]] = {}


async def _init_agent_map() -> None:
    global _AGENT_MAP
    if _AGENT_MAP:
        return
    url = "https://valorant-api.com/v1/agents?isPlayableCharacter=true"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                for agent in resp.json().get("data", []):
                    uuid = agent.get("uuid", "").lower()
                    if uuid:
                        role = agent.get("role", {}) or {}
                        _AGENT_MAP[uuid] = {
                            "name": agent.get("displayName", "Agent"),
                            "icon": agent.get("displayIcon", "") or "",
                            "bust": agent.get("bustPortrait", "") or "",
                            "role": role.get("displayName", ""),
                        }
                logger.info("Loaded %d agents into memory cache", len(_AGENT_MAP))
        except Exception as e:
            logger.exception("Failed to load agent map: %s", e)


async def get_agent_info(agent_uuid: str | None) -> dict[str, Any]:
    """Fetches agent details (name, icon URL, role) by agent UUID."""
    if not agent_uuid:
        return {"name": "Selecting...", "icon": "", "bust": "", "role": ""}
    if not _AGENT_MAP:
        await _init_agent_map()
    return _AGENT_MAP.get(
        agent_uuid.lower(),
        {"name": "Agent", "icon": "", "bust": "", "role": ""},
    )


async def _init_map_map() -> None:
    global _MAP_MAP
    if _MAP_MAP:
        return
    url = "https://valorant-api.com/v1/maps"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                for m in resp.json().get("data", []):
                    name = m.get("displayName", "Unknown Map")
                    map_url = (m.get("mapUrl") or "").lower()
                    splash = m.get("splash") or ""
                    info = {"name": name, "splash": splash}
                    if map_url:
                        _MAP_MAP[map_url] = info
                    _MAP_MAP[name.lower()] = info
                logger.info("Loaded %d maps into memory cache", len(_MAP_MAP))
        except Exception as e:
            logger.exception("Failed to load map map: %s", e)


async def get_map_info(map_path_or_name: str | None) -> dict[str, Any]:
    """Fetches map details (display name, splash image URL) by map path or name."""
    if not map_path_or_name:
        return {"name": "UNKNOWN MAP", "splash": ""}
    if not _MAP_MAP:
        await _init_map_map()
    key = map_path_or_name.lower()
    if key in _MAP_MAP:
        return _MAP_MAP[key]
    # Check if key is substring of any mapUrl
    for m_url, m_info in _MAP_MAP.items():
        if key in m_url or m_url in key:
            return m_info
    # Clean up standard game map path like "/Game/Maps/Ascent/Ascent" -> "Ascent"
    cleaned = map_path_or_name.split("/")[-1].title()
    return {"name": cleaned, "splash": ""}


async def _init_tier_map() -> None:
    global _TIER_MAP
    if _TIER_MAP:
        return
    url = "https://valorant-api.com/v1/competitivetiers"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    latest = data[-1].get("tiers", [])
                    for t in latest:
                        tier_id = t.get("tier", 0)
                        raw_color = t.get("color") or "8a95a0ff"
                        hex_color = "#" + raw_color[:6]
                        _TIER_MAP[tier_id] = {
                            "name": t.get("tierName", "Unrated"),
                            "icon": t.get("largeIcon") or t.get("smallIcon") or "",
                            "color": hex_color,
                        }
                logger.info("Loaded %d competitive tiers into memory cache", len(_TIER_MAP))
        except Exception as e:
            logger.exception("Failed to load competitive tier map: %s", e)


async def get_tier_info(tier_id: int) -> dict[str, Any]:
    """Fetches tier name, icon URL, and color by tier ID."""
    if not _TIER_MAP:
        await _init_tier_map()
    return _TIER_MAP.get(
        tier_id,
        {"name": "Unrated", "icon": "", "color": "#8a95a0"},
    )

