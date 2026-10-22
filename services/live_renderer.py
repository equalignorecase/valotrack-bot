import asyncio
import io
import logging
from typing import Any
import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from services.scoreboard import _ICON_CACHE, _fetch_icon, get_font, get_tier_color

logger = logging.getLogger(__name__)

_SPLASH_CACHE: dict[str, Image.Image] = {}


async def _fetch_splash(url: str, size: tuple[int, int]) -> Image.Image | None:
    """Fetches and caches map splash background image."""
    if not url:
        return None
    if url in _SPLASH_CACHE:
        return _SPLASH_CACHE[url]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                img = img.resize(size, Image.Resampling.LANCZOS)
                _SPLASH_CACHE[url] = img
                return img
    except Exception as e:
        logger.debug("Failed to load map splash %s: %s", url, e)
    return None


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) >= 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return (138, 149, 160)


async def render_live_match(lobby_data: dict[str, Any]) -> io.BytesIO:
    """
    Renders a high-resolution live match scoreboard card with Valorant UI styling.
    """
    width = 1200
    height = 760

    map_name = lobby_data.get("map_name", "UNKNOWN MAP").upper()
    map_splash_url = lobby_data.get("map_splash", "")
    mode_name = lobby_data.get("mode_name", "COMPETITIVE").upper()
    shard = lobby_data.get("shard", "AP")
    is_pregame = lobby_data.get("is_pregame", False)
    blue_players = lobby_data.get("blue_players", [])
    red_players = lobby_data.get("red_players", [])

    # Canvas
    canvas = Image.new("RGBA", (width, height), color=(15, 23, 33, 255))

    # 1. Background: Map Splash with Gaussian Blur & Dark Tint
    if map_splash_url:
        splash_img = await _fetch_splash(map_splash_url, (width, height))
        if splash_img:
            blurred_splash = splash_img.filter(ImageFilter.GaussianBlur(14))
            dark_overlay = Image.new("RGBA", (width, height), (10, 16, 24, 215))
            canvas.paste(blurred_splash, (0, 0))
            canvas = Image.alpha_composite(canvas, dark_overlay)

    draw = ImageDraw.Draw(canvas)

    # Fonts
    font_badge = get_font(12, bold=True)
    font_title = get_font(28, bold=True)
    font_sub = get_font(12, bold=False)
    font_team_hdr = get_font(14, bold=True)
    font_name = get_font(13, bold=True)
    font_tag = get_font(10, bold=False)
    font_rank = get_font(11, bold=True)
    font_peak = get_font(10, bold=False)
    font_stats = get_font(10, bold=True)
    font_party = get_font(9, bold=True)
    font_footer = get_font(11, bold=False)

    # Pre-fetch player icons & rank icons
    all_players = lobby_data.get("all_players") or (blue_players + red_players)
    icon_tasks = []
    for p in all_players:
        if p.get("agent_icon"):
            icon_tasks.append(_fetch_icon(p["agent_icon"], (42, 42), circular=True))
        if p.get("tier_icon"):
            icon_tasks.append(_fetch_icon(p["tier_icon"], (24, 24), circular=False))
    if icon_tasks:
        await asyncio.gather(*icon_tasks)

    # 2. Header Bar
    # Status Pill (Live Match vs Agent Select)
    pill_text = "🟡 AGENT SELECT" if is_pregame else "🔴 LIVE MATCH"
    pill_bg = (60, 48, 20, 230) if is_pregame else (65, 20, 26, 230)
    pill_border = (235, 185, 60) if is_pregame else (255, 70, 85)
    pill_w = 125
    pill_h = 24
    draw.rounded_rectangle([(36, 24), (36 + pill_w, 24 + pill_h)], radius=5, fill=pill_bg, outline=pill_border, width=1)
    draw.text((46, 28), pill_text, fill=(255, 255, 255), font=font_badge)

    # Map Title & Mode
    draw.text((36, 54), map_name, fill=(255, 255, 255), font=font_title)
    draw.text(
        (36, 92),
        f"MODE: {mode_name} • REGION: {shard} • 10 PLAYERS",
        fill=(150, 165, 180),
        font=font_sub,
    )

    # Top Right Brand
    draw.text((970, 36), "VALORANT LIVE TRACKER", fill=(138, 149, 160), font=font_badge)
    draw.text((1055, 56), "BY LOCALHOSTVLR", fill=(255, 70, 85), font=font_badge)

    # 3. Render Teams Panels
    col_w = 548
    col_h = 560
    col_y = 125

    # Palette
    color_blue_accent = (56, 178, 230)
    color_red_accent = (255, 70, 85)

    def draw_player_card(p: dict[str, Any], x: int, y: int, w: int, h: int, team_color: tuple[int, int, int]):
        is_high = p.get("is_highlight", False)
        bg_color = (25, 38, 54, 220) if is_high else (18, 26, 36, 190)
        border_color = (255, 215, 0) if is_high else (42, 54, 68)
        border_width = 2 if is_high else 1

        # Card base
        draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=6, fill=bg_color, outline=border_color, width=border_width)

        # Team accent bar on left edge
        draw.rounded_rectangle([(x, y + 2), (x + 4, y + h - 2)], radius=2, fill=team_color)

        # Agent Icon
        agent_url = p.get("agent_icon", "")
        agent_img = _ICON_CACHE.get(f"{agent_url}:(42, 42):True") if agent_url else None
        if agent_img:
            canvas.paste(agent_img, (x + 12, y + 8), agent_img)
        else:
            # Fallback circle placeholder
            draw.ellipse([(x + 12, y + 8), (x + 54, y + 50)], fill=(32, 44, 58), outline=team_color, width=1)

        # Agent Name & Player Name
        name_x = x + 62
        p_name = p.get("name", "Player")
        p_tag = f"#{p.get('tag', '0000')}"
        p_agent = p.get("agent_name", "Agent")
        draw.text((name_x, y + 10), p_name[:14], fill=(255, 255, 255), font=font_name)
        n_bbox = draw.textbbox((name_x, y + 10), p_name[:14], font=font_name)
        draw.text((n_bbox[2] + 4, y + 12), p_tag, fill=(138, 149, 160), font=font_tag)

        # Party Badge (if in party)
        party_lbl = p.get("party_label", "")
        if party_lbl:
            party_x = name_x + 140
            draw.rounded_rectangle([(party_x, y + 11), (party_x + 50, y + 24)], radius=3, fill=(35, 75, 55, 220), outline=(82, 224, 162), width=1)
            draw.text((party_x + 6, y + 13), party_lbl, fill=(82, 224, 162), font=font_party)

        # Rank Icon & Rank Text
        rank_icon_url = p.get("tier_icon", "")
        rank_img = _ICON_CACHE.get(f"{rank_icon_url}:(24, 24):False") if rank_icon_url else None
        if rank_img:
            canvas.paste(rank_img, (x + 285, y + 11), rank_img)

        rank_str = f"{p.get('rank_name', 'Unrated')} ({p.get('rr', 0)} RR)"
        rank_rgb = hex_to_rgb(p.get("rank_color", "#8a95a0"))
        draw.text((x + 315, y + 11), rank_str, fill=rank_rgb, font=font_rank)

        # Peak Rank
        peak_str = f"Peak: {p.get('peak_rank', 'Unrated')}"
        draw.text((x + 315, y + 26), peak_str, fill=(200, 180, 110), font=font_peak)

        # Sub-row: Agent info + KD & WR
        draw.text((name_x, y + 30), f"Playing {p_agent}", fill=(138, 155, 170), font=font_peak)

        # Recent Stats Badge (Far right)
        stats_box_x = x + 445
        draw.rounded_rectangle([(stats_box_x, y + 10), (stats_box_x + 88, y + 42)], radius=4, fill=(14, 20, 28, 200), outline=(40, 52, 66))
        draw.text((stats_box_x + 8, y + 14), p.get("kd", "K/D: -"), fill=(240, 240, 240), font=font_stats)
        draw.text((stats_box_x + 8, y + 27), p.get("winrate", "WR: -"), fill=(82, 224, 162), font=font_stats)

    # 4. Render Left Column (Blue Team / Ally Team)
    left_x = 36
    draw.rounded_rectangle([(left_x, col_y), (left_x + col_w, col_y + col_h)], radius=8, fill=(14, 20, 28, 180), outline=(32, 44, 58))
    # Team header
    draw.rounded_rectangle([(left_x, col_y), (left_x + col_w, col_y + 36)], radius=8, fill=(20, 36, 52, 240))
    draw.text((left_x + 16, col_y + 9), "🔵 TEAM 1 (ALLY / DEFENDERS)" if not is_pregame else "🔵 ALLY TEAM (AGENT SELECT)", fill=color_blue_accent, font=font_team_hdr)

    card_y = col_y + 46
    card_h = 58
    card_spacing = 8

    # Render Blue players
    for p in blue_players[:5]:
        draw_player_card(p, left_x + 10, card_y, col_w - 20, card_h, color_blue_accent)
        card_y += card_h + card_spacing

    # 5. Render Right Column (Red Team / Enemy Team)
    right_x = 616
    draw.rounded_rectangle([(right_x, col_y), (right_x + col_w, col_y + col_h)], radius=8, fill=(14, 20, 28, 180), outline=(32, 44, 58))
    # Team header
    draw.rounded_rectangle([(right_x, col_y), (right_x + col_w, col_y + 36)], radius=8, fill=(45, 20, 28, 240) if not is_pregame else (28, 34, 42, 240))
    hdr_text = "🔴 TEAM 2 (ENEMY / ATTACKERS)" if not is_pregame else "🔒 ENEMY TEAM (HIDDEN)"
    hdr_col = color_red_accent if not is_pregame else (160, 175, 190)
    draw.text((right_x + 16, col_y + 9), hdr_text, fill=hdr_col, font=font_team_hdr)

    if not is_pregame:
        # Coregame: render Red players
        card_y = col_y + 46
        for p in red_players[:5]:
            draw_player_card(p, right_x + 10, card_y, col_w - 20, card_h, color_red_accent)
            card_y += card_h + card_spacing
    else:
        # Pregame: Show informative lock card
        lock_box_y = col_y + 120
        draw.rounded_rectangle([(right_x + 30, lock_box_y), (right_x + col_w - 30, lock_box_y + 180)], radius=8, fill=(18, 24, 32, 220), outline=(48, 60, 75))
        draw.text((right_x + 150, lock_box_y + 30), "🔒 ENEMY TEAM IS HIDDEN", fill=(240, 240, 240), font=font_team_hdr)
        draw.text(
            (right_x + 65, lock_box_y + 65),
            "Riot Games obscures enemy player identities during Agent Select\n"
            "to prevent lobby dodging and target banning.",
            fill=(138, 149, 160),
            font=font_sub,
        )
        draw.text(
            (right_x + 65, lock_box_y + 115),
            "▶ Run /live again once the match loads to inspect all 10 players!",
            fill=(82, 224, 162),
            font=font_badge,
        )

    # 6. Footer
    draw.text((36, 705), "ValoTracker • Powered by localhostvlr & Riot Games API", fill=(110, 122, 134), font=font_footer)
    draw.text((1010, 705), "discord.gg/valorant", fill=(110, 122, 134), font=font_footer)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
