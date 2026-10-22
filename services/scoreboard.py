import asyncio
import io
import logging
from typing import Any
import httpx
import os
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Image cache for agent and rank icons: {url: PIL.Image}
_ICON_CACHE: dict[str, Image.Image] = {}

def resolve_font_path(bold: bool = False) -> str | None:
    """Finds available system fonts across Windows, macOS, and Linux."""
    # 1. Windows Fonts (C:\Windows\Fonts)
    windir = os.environ.get("WINDIR", r"C:\Windows")
    windows_candidates = [
        os.path.join(windir, "Fonts", "arialbd.ttf" if bold else "arial.ttf"),
        os.path.join(windir, "Fonts", "segoeuib.ttf" if bold else "segoeui.ttf"),
        os.path.join(windir, "Fonts", "tahomabd.ttf" if bold else "tahoma.ttf"),
    ]

    # 2. macOS Fonts
    macos_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
    ]

    # 3. Linux Fonts
    linux_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]

    for p in windows_candidates + macos_candidates + linux_candidates:
        if os.path.exists(p):
            return p
    return None


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Loads a TTF font with cross-platform fallbacks."""
    path = resolve_font_path(bold=bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass

    # Windows registry named font fallbacks
    for name in ["arialbd.ttf" if bold else "arial.ttf", "segoeuib.ttf" if bold else "segoeui.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass

    return ImageFont.load_default()


def get_tier_color(tier_name: str) -> tuple[int, int, int]:
    t = tier_name.lower()
    if "radiant" in t:
        return (255, 255, 170)
    if "immortal" in t:
        return (180, 20, 60)
    if "ascendant" in t:
        return (82, 224, 162)
    if "diamond" in t:
        return (186, 121, 245)
    if "platinum" in t:
        return (77, 184, 232)
    if "gold" in t:
        return (230, 195, 79)
    if "silver" in t:
        return (181, 193, 204)
    if "bronze" in t:
        return (166, 124, 82)
    if "iron" in t:
        return (109, 119, 128)
    return (138, 149, 160)


async def _fetch_icon(url: str, size: tuple[int, int], circular: bool = False) -> Image.Image | None:
    if not url:
        return None
    cache_key = f"{url}:{size}:{circular}"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                img = img.resize(size, Image.Resampling.LANCZOS)
                if circular:
                    mask = Image.new("L", size, 0)
                    draw_mask = ImageDraw.Draw(mask)
                    draw_mask.ellipse((0, 0, size[0], size[1]), fill=255)
                    output = Image.new("RGBA", size, (0, 0, 0, 0))
                    output.paste(img, (0, 0), mask=mask)
                    img = output
                _ICON_CACHE[cache_key] = img
                return img
    except Exception as e:
        logger.debug("Failed to load icon from %s: %s", url, e)
    return None


async def render_scoreboard(match_data: dict[str, Any], target_puuid: str | None = None) -> io.BytesIO:
    """
    Renders a full match scoreboard matching the modern Valorant UI theme.
    Returns: BytesIO buffer containing the generated PNG image.
    """
    metadata = match_data.get("metadata", {})
    teams = match_data.get("teams", {})
    all_players = match_data.get("players", {}).get("all_players", [])

    map_name = (metadata.get("map") or "UNKNOWN MAP").upper()
    mode_name = (metadata.get("mode") or metadata.get("queue") or "COMPETITIVE").upper()
    rounds_played = metadata.get("rounds_played") or 0
    match_id = (metadata.get("matchid") or "")[:18]

    red_info = teams.get("red", {})
    blue_info = teams.get("blue", {})
    red_score = red_info.get("rounds_won", 0)
    blue_score = blue_info.get("rounds_won", 0)
    red_won = red_info.get("has_won", False)
    blue_won = blue_info.get("has_won", False)

    # Separate and sort players by ACS descending
    red_players = []
    blue_players = []
    for p in all_players:
        stats = p.get("stats", {})
        score = stats.get("score", 0)
        p["_acs"] = round(score / max(rounds_played, 1))

        headshots = stats.get("headshots", 0)
        bodyshots = stats.get("bodyshots", 0)
        legshots = stats.get("legshots", 0)
        total_shots = headshots + bodyshots + legshots
        p["_hs_pct"] = round((headshots / max(total_shots, 1)) * 100)

        team = p.get("team", "").lower()
        if team == "red":
            red_players.append(p)
        else:
            blue_players.append(p)

    red_players.sort(key=lambda x: x["_acs"], reverse=True)
    blue_players.sort(key=lambda x: x["_acs"], reverse=True)

    # Pre-fetch icons
    tasks = []
    for p in all_players:
        agent_url = p.get("assets", {}).get("agent", {}).get("small", "")
        tasks.append(_fetch_icon(agent_url, (38, 38), circular=True))
        tier_id = p.get("currenttier", 0)
        tier_url = f"https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/{tier_id}/smallicon.png"
        tasks.append(_fetch_icon(tier_url, (24, 24), circular=False))

    await asyncio.gather(*tasks)

    # Canvas Setup
    width = 1060
    height = 800
    img = Image.new("RGBA", (width, height), color=(15, 23, 33, 255))  # #0f1923
    draw = ImageDraw.Draw(img)

    # Fonts
    font_title = get_font(30, bold=True)
    font_sub = get_font(13, bold=False)
    font_score_big = get_font(34, bold=True)
    font_score_lbl = get_font(11, bold=True)
    font_banner = get_font(15, bold=True)
    font_col_header = get_font(12, bold=True)
    font_name = get_font(14, bold=True)
    font_tag = get_font(11, bold=False)
    font_text = get_font(14, bold=False)
    font_bold = get_font(14, bold=True)
    font_footer = get_font(11, bold=False)

    # 1. Top Header: Map Name & Subtitle
    draw.text((36, 28), map_name, fill=(255, 255, 255), font=font_title)
    draw.text(
        (36, 68),
        f"MODE: {mode_name} • {rounds_played} ROUNDS PLAYED",
        fill=(138, 149, 160),
        font=font_sub,
    )

    # Top Right: Big Scores
    score_y = 28
    draw.text((880, score_y), str(red_score), fill=(255, 70, 85), font=font_score_big)
    draw.text((930, score_y), ":", fill=(255, 255, 255), font=font_score_big)
    draw.text((955, score_y), str(blue_score), fill=(0, 229, 255), font=font_score_big)

    draw.text((875, 70), "RED TEAM", fill=(255, 70, 85), font=font_score_lbl)
    draw.text((950, 70), "BLUE TEAM", fill=(0, 229, 255), font=font_score_lbl)

    current_y = 100

    def draw_team_section(team_name: str, rounds_won: int, is_winner: bool, color: tuple, players: list):
        nonlocal current_y

        # Banner Background & Accent Bar
        banner_bg = (24, 32, 42, 255)
        draw.rectangle((30, current_y, 1030, current_y + 34), fill=banner_bg)
        draw.rectangle((30, current_y, 36, current_y + 34), fill=color)

        status_text = f" — {rounds_won} ROUNDS" + (" • VICTORY 👑" if is_winner else "")
        draw.text((46, current_y + 8), f"{team_name.upper()} TEAM{status_text}", fill=color, font=font_banner)
        current_y += 42

        # Column Headers
        col_y = current_y
        draw.text((100, col_y), "PLAYER", fill=(138, 149, 160), font=font_col_header)
        draw.text((290, col_y), "AGENT", fill=(138, 149, 160), font=font_col_header)
        draw.text((430, col_y), "RANK TIER", fill=(138, 149, 160), font=font_col_header)
        draw.text((590, col_y), "K / D / A", fill=(138, 149, 160), font=font_col_header)
        draw.text((740, col_y), "ACS", fill=(138, 149, 160), font=font_col_header)
        draw.text((870, col_y), "HS %", fill=(138, 149, 160), font=font_col_header)
        current_y += 22

        # Player Rows
        row_height = 50
        for idx, p in enumerate(players):
            row_y = current_y
            is_target = target_puuid and p.get("puuid") == target_puuid

            # Alternating background or card
            bg_color = (19, 28, 38, 255) if idx % 2 == 0 else (16, 24, 34, 255)
            draw.rectangle((30, row_y, 1030, row_y + row_height - 4), fill=bg_color)

            # Highlight border if target player
            if is_target:
                gold_color = (230, 195, 79, 255)
                draw.rectangle((30, row_y, 1030, row_y + row_height - 4), outline=gold_color, width=2)

            # Circular Agent Icon
            agent_url = p.get("assets", {}).get("agent", {}).get("small", "")
            agent_cache_key = f"{agent_url}:(38, 38):True"
            if agent_cache_key in _ICON_CACHE:
                agent_img = _ICON_CACHE[agent_cache_key]
                img.paste(agent_img, (48, row_y + 4), mask=agent_img)

            # Player Name & Tag
            name = p.get("name", "Unknown")
            tag = p.get("tag", "")
            name_color = (230, 195, 79) if is_target else (255, 255, 255)
            draw.text((98, row_y + 6), name, fill=name_color, font=font_name)
            draw.text((98, row_y + 26), f"#{tag}", fill=(138, 149, 160), font=font_tag)

            # Agent Name
            agent_name = p.get("character", "Agent")
            draw.text((290, row_y + 14), agent_name, fill=(200, 210, 220), font=font_text)

            # Rank Tier Icon & Text
            tier_id = p.get("currenttier", 0)
            tier_name = p.get("currenttier_patched", "Unrated")
            tier_cache_key = f"https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/{tier_id}/smallicon.png:(24, 24):False"
            if tier_cache_key in _ICON_CACHE:
                tier_img = _ICON_CACHE[tier_cache_key]
                img.paste(tier_img, (428, row_y + 12), mask=tier_img)

            tier_col = get_tier_color(tier_name)
            draw.text((460, row_y + 14), tier_name, fill=tier_col, font=font_text)

            # K / D / A
            stats = p.get("stats", {})
            kda_str = f"{stats.get('kills', 0)} / {stats.get('deaths', 0)} / {stats.get('assists', 0)}"
            draw.text((590, row_y + 14), kda_str, fill=(240, 244, 248), font=font_text)

            # ACS
            draw.text((740, row_y + 14), str(p["_acs"]), fill=(255, 255, 255), font=font_bold)

            # HS %
            draw.text((870, row_y + 14), f"{p['_hs_pct']}%", fill=(0, 229, 255), font=font_text)

            current_y += row_height

        current_y += 12

    # Draw Red Team (or winner first)
    draw_team_section("Red", red_score, red_won, (255, 70, 85), red_players)
    draw_team_section("Blue", blue_score, blue_won, (0, 229, 255), blue_players)

    # Footer
    footer_text = f"MATCH ID: {match_id} • VALORANT BOT v2.0 • 🔲 = YOU"
    draw.text((36, height - 26), footer_text, fill=(86, 98, 112), font=font_footer)

    # Export to BytesIO
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
