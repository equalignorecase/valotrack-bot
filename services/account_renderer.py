import io
import logging
from datetime import datetime, timezone
from PIL import Image, ImageDraw

from database.models import UserAccount
from services.scoreboard import get_font

logger = logging.getLogger(__name__)


def render_accounts_dashboard(accounts: list[UserAccount]) -> io.BytesIO:
    """
    Renders a high-resolution dark dashboard image representing all accounts in the database.
    Dynamically adjusts height based on the total number of accounts.
    """
    width = 1100
    row_h = 54
    hdr_h = 150
    ftr_h = 45
    total_h = max(340, hdr_h + len(accounts) * row_h + ftr_h)

    img = Image.new("RGBA", (width, total_h), (14, 20, 28, 255))
    draw = ImageDraw.Draw(img)

    now_utc = datetime.now(timezone.utc)
    total = len(accounts)
    valid_cnt = sum(1 for a in accounts if a.token_expires > now_utc)
    expired_cnt = total - valid_cnt

    # Fonts
    font_title = get_font(22, bold=True)
    font_sub = get_font(11, bold=False)
    font_badge = get_font(11, bold=True)
    font_col = get_font(12, bold=True)
    font_bold = get_font(13, bold=True)
    font_text = get_font(12, bold=False)
    font_footer = get_font(10, bold=False)

    # 1. Header Card
    draw.rounded_rectangle([(25, 18), (width - 25, 120)], radius=8, fill=(20, 28, 38, 240), outline=(38, 50, 65))

    draw.text((45, 34), "VALORANT ACCOUNTS DATABASE", fill=(255, 255, 255), font=font_title)
    draw.text((45, 66), "VALOTRACKER ADMIN PANEL • VALOTRACKER.DB", fill=(138, 149, 160), font=font_sub)

    # Stat Badges (Right side)
    b_y = 44
    # Total
    draw.rounded_rectangle([(650, b_y), (770, b_y + 28)], radius=5, fill=(30, 42, 56), outline=(50, 68, 88))
    draw.text((665, b_y + 6), f"👥 {total} ACCOUNTS", fill=(220, 230, 240), font=font_badge)

    # Valid
    draw.rounded_rectangle([(785, b_y), (895, b_y + 28)], radius=5, fill=(20, 50, 35), outline=(82, 224, 162))
    draw.text((800, b_y + 6), f"🟢 {valid_cnt} VALID", fill=(82, 224, 162), font=font_badge)

    # Expired
    draw.rounded_rectangle([(910, b_y), (1030, b_y + 28)], radius=5, fill=(55, 20, 25), outline=(255, 70, 85))
    draw.text((925, b_y + 6), f"🔴 {expired_cnt} EXPIRED", fill=(255, 70, 85), font=font_badge)

    # 2. Table Header
    th_y = 135
    draw.rounded_rectangle([(25, th_y), (width - 25, th_y + 32)], radius=6, fill=(26, 36, 48), outline=(42, 56, 72))
    draw.text((45, th_y + 8), "#ID", fill=(160, 175, 190), font=font_col)
    draw.text((105, th_y + 8), "RIOT ACCOUNT", fill=(160, 175, 190), font=font_col)
    draw.text((320, th_y + 8), "REGION", fill=(160, 175, 190), font=font_col)
    draw.text((420, th_y + 8), "TOKEN STATUS", fill=(160, 175, 190), font=font_col)
    draw.text((570, th_y + 8), "EXPIRES IN / TIME", fill=(160, 175, 190), font=font_col)
    draw.text((750, th_y + 8), "ACTIVE", fill=(160, 175, 190), font=font_col)
    draw.text((850, th_y + 8), "DISCORD USER ID", fill=(160, 175, 190), font=font_col)

    # 3. Table Rows
    y = th_y + 40
    for idx, acc in enumerate(accounts):
        is_even = (idx % 2 == 0)
        bg = (20, 27, 36, 200) if is_even else (16, 22, 30, 200)
        is_selected = bool(acc.selected)
        border_col = (56, 178, 230) if is_selected else (34, 46, 60)

        draw.rounded_rectangle([(25, y), (width - 25, y + 44)], radius=6, fill=bg, outline=border_col, width=1)
        if is_selected:
            draw.rounded_rectangle([(25, y + 2), (29, y + 42)], radius=2, fill=(56, 178, 230))

        # ID
        draw.text((45, y + 13), f"#{acc.id}", fill=(255, 255, 255), font=font_bold)

        # Riot Account
        riot_name = acc.name if acc.name else f"{acc.puuid[:8]}..."
        draw.text((105, y + 13), str(riot_name)[:16], fill=(255, 255, 255), font=font_bold)

        # Region Pill
        draw.rounded_rectangle([(320, y + 10), (370, y + 32)], radius=4, fill=(24, 45, 68), outline=(56, 120, 180))
        draw.text((330, y + 13), acc.shard.upper()[:4], fill=(130, 190, 250), font=font_badge)

        # Token status Pill
        is_exp = (acc.token_expires <= now_utc)
        if is_exp:
            draw.rounded_rectangle([(420, y + 10), (510, y + 32)], radius=4, fill=(55, 20, 25), outline=(255, 70, 85))
            draw.text((435, y + 13), "EXPIRED", fill=(255, 80, 95), font=font_badge)
            time_label = "Expired"
            time_col = (200, 100, 100)
        else:
            draw.rounded_rectangle([(420, y + 10), (490, y + 32)], radius=4, fill=(20, 50, 35), outline=(82, 224, 162))
            draw.text((435, y + 13), "VALID", fill=(82, 224, 162), font=font_badge)
            diff = acc.token_expires - now_utc
            hrs = int(diff.total_seconds() // 3600)
            mins = int((diff.total_seconds() % 3600) // 60)
            time_label = f"{hrs}h {mins}m remaining"
            time_col = (140, 220, 170)

        # Expires in
        draw.text((570, y + 13), time_label, fill=time_col, font=font_text)

        # Active status
        if is_selected:
            draw.rounded_rectangle([(750, y + 10), (820, y + 32)], radius=4, fill=(15, 45, 60), outline=(56, 178, 230))
            draw.text((760, y + 13), "ACTIVE", fill=(56, 178, 230), font=font_badge)
        else:
            draw.text((770, y + 13), "-", fill=(100, 115, 130), font=font_bold)

        # Discord User ID
        draw.text((850, y + 13), str(acc.discord_user_id), fill=(160, 185, 210), font=font_bold)

        y += row_h

    # 4. Footer
    draw.text(
        (35, total_h - 28),
        "ValoTracker.db • Authorized Admin Access • Powered by localhostvlr",
        fill=(100, 115, 130),
        font=font_footer,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
