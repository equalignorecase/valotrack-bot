import asyncio
import logging
from typing import Any
import httpx

from services.henrik_api import get_account_by_puuid, get_matches, get_mmr
from services.riot_store import PLATFORM_HEADER
from services.valorant_api import get_agent_info, get_client_version, get_map_info, get_tier_info

logger = logging.getLogger(__name__)

QUEUE_NAMES = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "hurm": "Team Deathmatch",
    "ggteam": "Escalation",
    "onefa": "Replication",
    "premier": "Premier",
    "snowball": "Snowball Fight",
}


def get_glz_url(shard: str) -> str:
    """Returns the base GLZ server URL for the given region shard."""
    s = shard.lower()
    if s in ("latam", "br"):
        return f"https://glz-{s}-1.na.a.pvp.net"
    return f"https://glz-{s}-1.{s}.a.pvp.net"


def get_pd_url(shard: str) -> str:
    """Returns the base PD server URL for the given region shard."""
    s = shard.lower()
    return f"https://pd.{s}.a.pvp.net"


async def get_player_names(
    shard: str,
    access_token: str,
    entitlement: str,
    puuids: list[str],
) -> dict[str, dict[str, str]]:
    """
    Resolves a batch of PUUIDs into Riot GameName and TagLine via Riot Name-Service.
    Returns: {puuid: {"name": ..., "tag": ..., "display": "Name#Tag"}}
    """
    if not puuids:
        return {}

    url = f"{get_pd_url(shard)}/name-service/v2/players"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Riot-Entitlements-JWT": entitlement,
        "Content-Type": "application/json",
    }

    results: dict[str, dict[str, str]] = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.put(url, headers=headers, json=puuids)
            if resp.status_code == 200:
                data = resp.json()
                for item in data:
                    sub = item.get("Subject")
                    g_name = item.get("GameName") or "Player"
                    tag = item.get("TagLine") or "0000"
                    if sub:
                        results[sub] = {
                            "name": g_name,
                            "tag": tag,
                            "display": f"{g_name}#{tag}",
                        }
                return results
            logger.warning("Riot Name-Service returned status %s: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.exception("Error in get_player_names: %s", e)

    # Fallback to Henrik API for any unresolved names
    for p in puuids:
        if p not in results:
            try:
                acc = await get_account_by_puuid(p)
                if acc:
                    gn = acc.get("name", "Player")
                    gt = acc.get("tag", "0000")
                    results[p] = {"name": gn, "tag": gt, "display": f"{gn}#{gt}"}
            except Exception:
                pass
            if p not in results:
                results[p] = {"name": "Player", "tag": "0000", "display": "Player"}

    return results


async def get_live_match_raw(
    shard: str,
    puuid: str,
    access_token: str,
    entitlement: str,
    client_version: str | None = None,
) -> dict[str, Any] | None:
    """
    Checks if player is in pre-game (Agent Select) or core-game (In-Match).
    Returns dict with {"type": "pregame"|"coregame", "match_id": ..., "data": ...} or None.
    """
    if not client_version:
        client_version = await get_client_version()

    glz_base = get_glz_url(shard)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Riot-Entitlements-JWT": entitlement,
        "X-Riot-ClientPlatform": PLATFORM_HEADER,
        "X-Riot-ClientVersion": client_version,
    }

    async with httpx.AsyncClient(timeout=12.0) as client:
        # 1. Check Pre-Game (Agent Select)
        try:
            pre_player_url = f"{glz_base}/pre-game/v1/players/{puuid}"
            resp = await client.get(pre_player_url, headers=headers)
            if resp.status_code == 200:
                match_id = resp.json().get("MatchID")
                if match_id:
                    match_url = f"{glz_base}/pre-game/v1/matches/{match_id}"
                    m_resp = await client.get(match_url, headers=headers)
                    if m_resp.status_code == 200:
                        return {
                            "type": "pregame",
                            "match_id": match_id,
                            "data": m_resp.json(),
                        }
        except Exception as e:
            logger.debug("Error checking pregame: %s", e)

        # 2. Check Core-Game (In-Match)
        try:
            core_player_url = f"{glz_base}/core-game/v1/players/{puuid}"
            resp = await client.get(core_player_url, headers=headers)
            if resp.status_code == 200:
                match_id = resp.json().get("MatchID")
                if match_id:
                    match_url = f"{glz_base}/core-game/v1/matches/{match_id}"
                    m_resp = await client.get(match_url, headers=headers)
                    if m_resp.status_code == 200:
                        return {
                            "type": "coregame",
                            "match_id": match_id,
                            "data": m_resp.json(),
                        }
        except Exception as e:
            logger.debug("Error checking coregame: %s", e)

    return None


async def parse_live_lobby(
    shard: str,
    raw_session: dict[str, Any],
    user_puuid: str,
    access_token: str,
    entitlement: str,
    focus_player: str | None = None,
) -> dict[str, Any]:
    """
    Parses live match data into structured match info, players, parties, and stats.
    """
    m_type = raw_session.get("type", "coregame")
    data = raw_session.get("data", {})
    match_id = raw_session.get("match_id", "")

    # Map info
    map_id = data.get("MapID", "")
    map_info = await get_map_info(map_id)

    # Mode info
    queue_id = (
        data.get("MatchmakingData", {}).get("QueueID")
        or data.get("QueueID")
        or data.get("Mode")
        or "competitive"
    ).lower()
    mode_name = QUEUE_NAMES.get(queue_id, queue_id.title())

    # Raw players list extraction
    if m_type == "pregame":
        ally_team = data.get("AllyTeam", {})
        players_raw = ally_team.get("Players", [])
        enemy_size = data.get("EnemyTeamSize", 5)
    else:
        players_raw = data.get("Players", [])
        enemy_size = 0

    puuid_list = [p.get("Subject") for p in players_raw if p.get("Subject")]

    # 1. Concurrently fetch player names from Riot Name-Service
    name_map = await get_player_names(shard, access_token, entitlement, puuid_list)

    # 2. Party grouping
    party_map: dict[str, list[str]] = {}
    for p in players_raw:
        sub = p.get("Subject")
        # In pregame: PartyID is in player dict
        # In coregame: PartyID might be in PlayerIdentity or PartyID
        party_id = (
            p.get("PartyID")
            or p.get("PlayerIdentity", {}).get("PartyID")
            or ""
        )
        if party_id and sub:
            party_map.setdefault(party_id, []).append(sub)

    # Assign party labels to groups with > 1 player
    multi_parties = [p_id for p_id, members in party_map.items() if len(members) > 1]
    party_labels: dict[str, str] = {}
    for idx, p_id in enumerate(multi_parties, start=1):
        party_labels[p_id] = f"Party {idx}"

    # 3. Concurrently fetch Agent info and MMR / Rank for all players
    async def fetch_player_data(p_dict: dict[str, Any]) -> dict[str, Any]:
        sub = p_dict.get("Subject", "")
        team_id = p_dict.get("TeamID") or "Blue"
        char_id = p_dict.get("CharacterID") or ""

        # Party label
        party_id = (
            p_dict.get("PartyID")
            or p_dict.get("PlayerIdentity", {}).get("PartyID")
            or ""
        )
        party_label = party_labels.get(party_id, "")

        # Name
        resolved_name = name_map.get(sub, {"name": "Player", "tag": "0000", "display": "Player"})
        display_name = resolved_name["display"]
        clean_name = resolved_name["name"]
        clean_tag = resolved_name["tag"]

        # Agent info
        agent_info = await get_agent_info(char_id)

        # MMR & Rank info (fetch concurrently)
        mmr_task = get_mmr(shard=shard, puuid=sub)
        matches_task = get_matches(shard=shard, puuid=sub, size=5)
        mmr_res, matches_res = await asyncio.gather(mmr_task, matches_task, return_exceptions=True)

        # Parse MMR
        rank_name = "Unrated"
        rr = 0
        peak_rank = "Unrated"
        tier_icon = ""
        rank_color = "#8a95a0"

        if isinstance(mmr_res, dict) and "current_data" in mmr_res:
            c = mmr_res.get("current_data", {})
            h = mmr_res.get("highest_rank", {})
            rank_name = c.get("currenttierpatched", "Unrated")
            rr = c.get("ranking_in_tier", 0)
            images = c.get("images", {})
            tier_icon = images.get("small") or images.get("large") or ""
            peak_rank = h.get("patched_tier", "Unrated")
            tier_id = c.get("currenttier", 0)
            tier_info = await get_tier_info(tier_id)
            rank_color = tier_info.get("color", "#8a95a0")

        # Parse recent 5-match KD & Winrate
        kd_str = "K/D: -"
        wr_str = "WR: -"
        if isinstance(matches_res, list) and matches_res:
            total_k = 0
            total_d = 0
            wins = 0
            for m in matches_res:
                teams = m.get("teams", {})
                players = (m.get("players") or {}).get("all_players") or []
                tp = next((x for x in players if x.get("puuid") == sub), None)
                if tp:
                    stats = tp.get("stats", {})
                    total_k += stats.get("kills", 0)
                    total_d += stats.get("deaths", 0)
                    t_key = str(tp.get("team", "")).lower()
                    if teams.get(t_key, {}).get("has_won") is True:
                        wins += 1
            if matches_res:
                kd_ratio = total_k / max(total_d, 1)
                wr_pct = round((wins / len(matches_res)) * 100)
                kd_str = f"K/D: {kd_ratio:.2f}"
                wr_str = f"{wr_pct}% WR"

        # Check highlight
        is_highlight = False
        if focus_player:
            f_lower = focus_player.lower().strip()
            if f_lower == display_name.lower() or f_lower == clean_name.lower():
                is_highlight = True
        elif sub == user_puuid:
            is_highlight = True

        return {
            "puuid": sub,
            "name": clean_name,
            "tag": clean_tag,
            "display": display_name,
            "team_id": team_id,
            "character_id": char_id,
            "agent_name": agent_info.get("name", "Agent"),
            "agent_icon": agent_info.get("icon", ""),
            "party_label": party_label,
            "rank_name": rank_name,
            "rr": rr,
            "peak_rank": peak_rank,
            "tier_icon": tier_icon,
            "rank_color": rank_color,
            "kd": kd_str,
            "winrate": wr_str,
            "is_highlight": is_highlight,
        }

    # Gather all player records
    player_tasks = [fetch_player_data(p) for p in players_raw]
    all_parsed_players = await asyncio.gather(*player_tasks)

    # Separate into Blue and Red teams
    blue_players = [p for p in all_parsed_players if p["team_id"].lower() == "blue"]
    red_players = [p for p in all_parsed_players if p["team_id"].lower() == "red"]

    return {
        "state": "PREGAME" if m_type == "pregame" else "COREGAME",
        "match_id": match_id,
        "map_name": map_info.get("name", "UNKNOWN MAP"),
        "map_splash": map_info.get("splash", ""),
        "mode_name": mode_name,
        "shard": shard.upper(),
        "blue_players": blue_players,
        "red_players": red_players,
        "all_players": all_parsed_players,
        "is_pregame": m_type == "pregame",
        "enemy_size": enemy_size,
    }
