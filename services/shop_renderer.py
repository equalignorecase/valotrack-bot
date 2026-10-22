import asyncio
import io
import logging
import os
from typing import Any
import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Cache for downloaded skin weapon images and tier icons: {url: PIL.Image}
_IMAGE_CACHE: dict[str, Image.Image] = {}


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Converts a hex color string like '#f5955b' to an (R, G, B) tuple."""
    h = hex_str.lstrip("#")
    if len(h) >= 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    return (90, 159, 226)  # Default fallback blue


def resolve_font_path(bold: bool = False) -> str | None:
    """Finds available system fonts across Windows, macOS, and Linux."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    windows_candidates = [
        os.path.join(windir, "Fonts", "arialbd.ttf" if bold else "arial.ttf"),
        os.path.join(windir, "Fonts", "segoeuib.ttf" if bold else "segoeui.ttf"),
        os.path.join(windir, "Fonts", "tahomabd.ttf" if bold else "tahoma.ttf"),
    ]
    macos_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
    ]
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

    for name in ["arialbd.ttf" if bold else "arial.ttf", "segoeuib.ttf" if bold else "segoeui.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass

    return ImageFont.load_default()


async def fetch_image(url: str) -> Image.Image | None:
    """Fetches an image with caching."""
    if not url:
        return None
    if url in _IMAGE_CACHE:
        return _IMAGE_CACHE[url]

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
                _IMAGE_CACHE[url] = img
                return img
        except Exception as e:
            logger.debug("Failed to fetch image %s: %s", url, e)
    return None


def truncate_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Truncates text with ellipsis if it exceeds the max pixel width."""
    bbox = draw.textbbox((0, 0), text, font=font)
    if (bbox[2] - bbox[0]) <= max_width:
        return text

    while len(text) > 3:
        text = text[:-1]
        bbox = draw.textbbox((0, 0), text + "...", font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return text + "..."
    return text


async def render_daily_shop(
    skins: list[dict[str, Any]],
    player_name: str = "Valorant Player",
    shard: str = "AP",
) -> io.BytesIO:
    """
    Renders a 1x4 horizontal panorama card displaying the daily rotating shop skins.
    Returns: BytesIO buffer of the rendered PNG image.
    """
    canvas_w = 1600
    canvas_h = 620

    canvas = Image.new("RGBA", (canvas_w, canvas_h), (15, 25, 35, 255))
    draw = ImageDraw.Draw(canvas)

    # Fonts
    font_badge = get_font(18, bold=True)
    font_header_title = get_font(24, bold=True)
    font_header_info = get_font(16, bold=False)
    font_tier = get_font(13, bold=True)
    font_skin_name = get_font(19, bold=True)
    font_vp_badge = get_font(14, bold=True)
    font_cost = get_font(22, bold=True)

    # Top Header Section
    # Red VALORANT badge
    draw.rounded_rectangle([40, 25, 170, 65], radius=6, fill=(255, 70, 85, 255))
    draw.text((58, 33), "VALORANT", font=font_badge, fill=(255, 255, 255, 255))

    # Header Title
    draw.text((185, 30), "DAILY STORE", font=font_header_title, fill=(236, 232, 225, 255))

    # Player Info Pill on Top Right
    info_text = f"ACCOUNT: {player_name.upper()}  •  REGION: {shard.upper()}"
    bbox_info = draw.textbbox((0, 0), info_text, font=font_header_info)
    info_w = bbox_info[2] - bbox_info[0]
    pill_right_x = 1560
    pill_left_x = pill_right_x - info_w - 40
    draw.rounded_rectangle(
        [pill_left_x, 26, pill_right_x, 64],
        radius=8,
        fill=(22, 33, 46, 255),
        outline=(45, 60, 75, 255),
        width=1,
    )
    draw.text((pill_left_x + 20, 35), info_text, font=font_header_info, fill=(180, 192, 204, 255))

    # Concurrently fetch weapon artwork images
    fetch_tasks = [fetch_image(s.get("icon", "")) for s in skins]
    tier_icon_tasks = [fetch_image(s.get("tier_icon", "")) for s in skins]
    artwork_images = await asyncio.gather(*fetch_tasks)
    tier_images = await asyncio.gather(*tier_icon_tasks)

    # 4 Cards Layout Geometry (1x4 Horizontal)
    card_w = 362
    card_h = 490
    gap = 24
    start_x = 40
    start_y = 95

    for i in range(min(4, len(skins))):
        skin = skins[i]
        c_x = start_x + i * (card_w + gap)
        c_y = start_y

        tier_hex = skin.get("tier_color") or "#5A9FE2"
        tier_rgb = hex_to_rgb(tier_hex)
        tier_name = skin.get("tier_name", "Select Edition").upper()
        skin_name = skin.get("name", "Unknown Weapon")
        cost = skin.get("cost", 0)

        # 1. Main Card Container
        draw.rounded_rectangle(
            [c_x, c_y, c_x + card_w, c_y + card_h],
            radius=14,
            fill=(22, 33, 46, 255),
            outline=(38, 52, 69, 255),
            width=2,
        )

        # 2. Top Color Accent Line
        draw.rounded_rectangle(
            [c_x + 12, c_y + 4, c_x + card_w - 12, c_y + 8],
            radius=2,
            fill=(*tier_rgb, 255),
        )

        # 3. Tier Header Area
        # Tier icon or colored indicator dot
        t_icon = tier_images[i]
        if t_icon:
            t_icon_resized = t_icon.resize((20, 20), Image.Resampling.LANCZOS)
            canvas.paste(t_icon_resized, (c_x + 22, c_y + 18), t_icon_resized)
            draw.text((c_x + 48, c_y + 20), tier_name, font=font_tier, fill=(*tier_rgb, 255))
        else:
            draw.ellipse([c_x + 22, c_y + 24, c_x + 32, c_y + 34], fill=(*tier_rgb, 255))
            draw.text((c_x + 40, c_y + 20), tier_name, font=font_tier, fill=(*tier_rgb, 255))

        # 4. Weapon Preview Box with subtle backdrop highlight
        box_x = c_x + 16
        box_y = c_y + 50
        box_w = card_w - 32  # 330px
        box_h = 280

        # Background subtle ambient glow
        glow_layer = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        glow_draw.ellipse(
            [box_w * 0.15, box_h * 0.2, box_w * 0.85, box_h * 0.8],
            fill=(*tier_rgb, 28),
        )
        canvas.paste(glow_layer, (box_x, box_y), glow_layer)

        # Weapon Artwork
        art = artwork_images[i]
        if art:
            art_copy = art.copy()
            art_copy.thumbnail((310, 220), Image.Resampling.LANCZOS)
            aw_w, aw_h = art_copy.size
            art_x = box_x + (box_w - aw_w) // 2
            art_y = box_y + (box_h - aw_h) // 2
            canvas.paste(art_copy, (art_x, art_y), art_copy)

        # 5. Divider Line
        div_y = c_y + 345
        draw.line([(c_x + 20, div_y), (c_x + card_w - 20, div_y)], fill=(36, 49, 64, 255), width=1)

        # 6. Weapon Skin Name
        name_y = c_y + 360
        clean_name = truncate_text(draw, skin_name, font_skin_name, max_width=card_w - 40)
        # Center skin name
        bbox_name = draw.textbbox((0, 0), clean_name, font=font_skin_name)
        text_w = bbox_name[2] - bbox_name[0]
        text_x = c_x + (card_w - text_w) // 2
        draw.text((text_x, name_y), clean_name, font=font_skin_name, fill=(236, 232, 225, 255))

        # 7. Price Pill Container
        pill_y = c_y + 416
        pill_h = 50
        pill_x1 = c_x + 20
        pill_x2 = c_x + card_w - 20
        draw.rounded_rectangle(
            [pill_x1, pill_y, pill_x2, pill_y + pill_h],
            radius=10,
            fill=(13, 21, 29, 255),
            outline=(38, 52, 69, 255),
            width=1,
        )

        # Red VP Icon Badge
        badge_x = pill_x1 + 14
        badge_y = pill_y + 10
        badge_w = 42
        badge_h = 30
        draw.rounded_rectangle(
            [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
            radius=6,
            fill=(255, 70, 85, 255),
        )
        draw.text((badge_x + 9, badge_y + 6), "VP", font=font_vp_badge, fill=(255, 255, 255, 255))

        # Cost Text in bright Gold
        cost_str = f"{cost:,}" if cost > 0 else "FREE"
        draw.text((badge_x + badge_w + 14, pill_y + 11), cost_str, font=font_cost, fill=(255, 215, 0, 255))

    # Save to buffer
    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
