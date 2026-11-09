import asyncio
import io
import logging
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from database.db import (
    get_account_by_id,
    get_all_users,
    get_selected_user,
    get_users_by_discord_id,
)
from database.models import UserAccount
import config
from services.crypto import decrypt_token
from services.riot_auth import get_entitlement, refresh_account_session
from services.riot_store import get_storefront
from services.shop_renderer import render_daily_shop
from services.valorant_api import get_weapon_info

logger = logging.getLogger(__name__)

# Dynamic Bot Owner ID loaded from config / environment (.env BOT_OWNER_ID)
DEFAULT_OWNER_ID = 578167102582685698
OWNER_DISCORD_ID = config.BOT_OWNER_ID or DEFAULT_OWNER_ID


def is_bot_owner(user_id: int | str) -> bool:
    """Returns True if user_id matches configured BOT_OWNER_ID or fallback owner."""
    try:
        uid = int(user_id)
        owner_id = int(config.BOT_OWNER_ID or DEFAULT_OWNER_ID)
        return uid == owner_id
    except (ValueError, TypeError):
        return False

# In-memory Daily Shop Cache: {(puuid, shard, player_name): {"cache_until": float, "reset_ts": int, "total_vp": int, "summary_text": str, "png_bytes": bytes}}
_SHOP_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
SHOP_CACHE_TTL_SECONDS = 600  # 10 minutes (or until daily shop resets)


def _has_refresh_token(acc: UserAccount) -> bool:
    try:
        r_token = decrypt_token(acc.encrypted_token, acc.nonce, acc.tag)
        return bool(r_token and r_token.strip())
    except Exception:
        return False


async def get_valid_account(discord_user_id: int, force_refresh: bool = False) -> UserAccount | None:
    """
    Retrieves the user's selected account and ensures a valid Riot access token.
    Delegates to refresh_account_session() which serializes refreshes per-PUUID.
    """
    account = await get_selected_user(discord_user_id)
    if not account:
        return None

    refreshed = await refresh_account_session(account, force=force_refresh)
    if not refreshed:
        logger.error("Failed to refresh token with Riot API for user %s", discord_user_id)
        return None
    return refreshed


async def resolve_target_account(
    caller_discord_id: int,
    account_query: str | None,
) -> tuple[UserAccount | None, str | None]:
    """
    Resolves which UserAccount to inspect for /shop:
    - If account_query is None: caller's currently selected account.
    - If caller is OWNER_DISCORD_ID: can select any account in ValoTracker.db by ID or name.
    - Otherwise: can only select from caller's own linked accounts.
    Returns (account, error_message).
    """
    if not account_query or not account_query.strip():
        acc = await get_selected_user(caller_discord_id)
        if not acc:
            return (
                None,
                "ไม่พบบัญชีที่กำลังเลือกใช้งาน กรุณาเข้าสู่ระบบด้วยคำสั่ง `/login` ก่อน หรือตรวจสอบที่ `/view-accounts`",
            )
        return acc, None

    q = account_query.strip()
    is_owner = is_bot_owner(caller_discord_id)

    if is_owner:
        # Try direct database ID lookup if numeric (or "#12")
        clean_id = q.lstrip("#")
        if clean_id.isdigit():
            acc_by_id = await get_account_by_id(int(clean_id))
            if acc_by_id:
                return acc_by_id, None

        all_accs = await get_all_users()
        q_lower = q.lower()
        match = next(
            (
                a
                for a in all_accs
                if (a.name and a.name.lower() == q_lower)
                or a.puuid.lower() == q_lower
                or (a.name and q_lower in a.name.lower())
            ),
            None,
        )
        if match:
            return match, None
        return None, f"❌ ไม่พบบัญชี `{q}` ในฐานข้อมูล"

    # Non-owner: only allow selecting among their own linked accounts
    own_accs = await get_users_by_discord_id(caller_discord_id)
    if not own_accs:
        return None, "คุณยังไม่ได้เชื่อมต่อบัญชีใดๆ กรุณาใช้คำสั่ง `/login` ก่อน"

    clean_id = q.lstrip("#")
    if clean_id.isdigit():
        num = int(clean_id)
        # Match by DB ID first, then by 1-based list index from /view-accounts
        by_db_id = next((a for a in own_accs if a.id == num), None)
        if by_db_id:
            return by_db_id, None
        if 1 <= num <= len(own_accs):
            return own_accs[num - 1], None

    q_lower = q.lower()
    own_match = next(
        (
            a
            for a in own_accs
            if (a.name and a.name.lower() == q_lower)
            or (a.name and q_lower in a.name.lower())
        ),
        None,
    )
    if own_match:
        return own_match, None

    return (
        None,
        "❌ คุณสามารถเลือกดูร้านค้าได้เฉพาะบัญชีของตัวเองที่ผูกไว้เท่านั้น (ตรวจสอบรายชื่อได้ที่ `/view-accounts`)",
    )


async def build_shop_embed_and_file(
    target_account: UserAccount,
    caller_mention: str,
) -> tuple[discord.Embed | None, discord.File | None, str | None]:
    """
    Refreshes the session if needed, fetches or serves cached storefront, renders the 1x4
    panorama shop image, and returns (embed, file, error_message).
    """
    account = await refresh_account_session(target_account, force=False)
    if not account:
        acc_label = target_account.name or f"ID #{target_account.id}"
        return (
            None,
            None,
            f"{caller_mention} เซสชันของบัญชี **{acc_label}** หมดอายุแล้ว เจ้าของบัญชี (<@{target_account.discord_user_id}>) ต้องเข้าสู่ระบบใหม่ด้วย `/login`",
        )

    player_display_name = account.name or "Valorant Player"
    cache_key = (account.puuid, account.shard.lower(), player_display_name)
    now_mono = time.monotonic()
    now_unix = int(datetime.now(timezone.utc).timestamp())

    cached_entry = _SHOP_CACHE.get(cache_key)
    if cached_entry and cached_entry["cache_until"] > now_mono and cached_entry["reset_ts"] > now_unix:
        reset_timestamp = cached_entry["reset_ts"]
        remaining_seconds = max(0, reset_timestamp - now_unix)
        total_vp = cached_entry["total_vp"]
        summary_text = cached_entry["summary_text"]
        png_bytes = cached_entry["png_bytes"]
    else:
        retried = False
        entitlement = await get_entitlement(account.access_token)
        if not entitlement:
            retried = True
            account = await refresh_account_session(account, force=True)
            if account:
                entitlement = await get_entitlement(account.access_token)

        if not account or not entitlement:
            acc_label = target_account.name or f"ID #{target_account.id}"
            return (
                None,
                None,
                f"{caller_mention} ขอสิทธิ์เข้าถึง (Entitlement) สำหรับ **{acc_label}** ไม่สำเร็จ เซสชันอาจหมดอายุ ต้องล็อกอินใหม่ด้วย `/login`",
            )

        storefront = await get_storefront(
            shard=account.shard,
            puuid=account.puuid,
            entitlement=entitlement,
            access_token=account.access_token,
        )

        if (not storefront or "SkinsPanelLayout" not in storefront) and not retried:
            account = await refresh_account_session(account, force=True)
            if account:
                entitlement = await get_entitlement(account.access_token)
                if entitlement:
                    storefront = await get_storefront(
                        shard=account.shard,
                        puuid=account.puuid,
                        entitlement=entitlement,
                        access_token=account.access_token,
                    )

        if not storefront or "SkinsPanelLayout" not in storefront:
            return (
                None,
                None,
                f"{caller_mention} เกิดข้อผิดพลาดในการดึงข้อมูลร้านค้า ไม่พบข้อมูลสินค้าจาก Riot",
            )

        panel = storefront["SkinsPanelLayout"]
        single_item_offers = panel.get("SingleItemOffers", [])
        store_offers = panel.get("SingleItemStoreOffers", [])
        remaining_seconds = max(0, int(panel.get("SingleItemOffersRemainingDurationInSeconds", 0)))

        if not single_item_offers:
            return (
                None,
                None,
                f"{caller_mention} ไม่พบรายการสินค้าในร้านค้าประจำวัน",
            )

        # Build OfferID to Cost map
        cost_map = {}
        for offer in store_offers:
            offer_id = offer.get("OfferID")
            cost_dict = offer.get("Cost", {})
            if offer_id and cost_dict:
                cost_map[offer_id] = next(iter(cost_dict.values()), 0)

        # Concurrently fetch weapon details from valorant-api.com
        weapon_tasks = [get_weapon_info(skin_id) for skin_id in single_item_offers]
        weapon_results = await asyncio.gather(*weapon_tasks)

        skins_data = []
        total_vp = 0
        skin_summary_lines = []

        for skin_id, info in zip(single_item_offers, weapon_results):
            name = info.get("name", "Unknown Skin")
            icon_url = info.get("icon", "")
            cost = cost_map.get(skin_id, 0)
            tier_name = info.get("tier_name", "Select Edition")
            tier_color = info.get("tier_color", "#5A9FE2")
            tier_icon = info.get("tier_icon")

            skins_data.append({
                "id": skin_id,
                "name": name,
                "icon": icon_url,
                "cost": cost,
                "tier_name": tier_name,
                "tier_color": tier_color,
                "tier_icon": tier_icon,
            })
            total_vp += cost
            skin_summary_lines.append(f"• **{name}** — `{cost:,} VP`")

        # Render 1x4 Panorama Shop Image (runs Pillow in background thread)
        buf = await render_daily_shop(
            skins=skins_data,
            player_name=player_display_name,
            shard=account.shard,
        )
        png_bytes = buf.getvalue()
        summary_text = "\n".join(skin_summary_lines)
        now_unix = int(datetime.now(timezone.utc).timestamp())
        reset_timestamp = now_unix + remaining_seconds

        if remaining_seconds > 5:
            if len(_SHOP_CACHE) >= 100:
                _SHOP_CACHE.pop(next(iter(_SHOP_CACHE)))
            _SHOP_CACHE[cache_key] = {
                "cache_until": time.monotonic() + min(SHOP_CACHE_TTL_SECONDS, remaining_seconds),
                "reset_ts": reset_timestamp,
                "total_vp": total_vp,
                "summary_text": summary_text,
                "png_bytes": png_bytes,
            }

    file = discord.File(fp=io.BytesIO(png_bytes), filename="daily_shop.png")

    hours = remaining_seconds // 3600
    minutes = (remaining_seconds % 3600) // 60
    seconds = remaining_seconds % 60

    embed_desc = (
        f"**เวลาร้านค้ารีเซ็ต:** `{hours} ชม. {minutes} นาที {seconds} วินาที` (<t:{reset_timestamp}:R>)\n"
        f"**ภูมิภาค:** `{account.shard.upper()}` • **ราคารวมทั้งหมด:** `{total_vp:,} VP`\n\n"
        f"**รายการสกินประจำวันนี้:**\n"
        f"{summary_text}"
    )

    embed = discord.Embed(
        title=f"ร้านค้าประจำวัน (Daily Shop): {account.name or 'บัญชี Valorant'}",
        description=embed_desc,
        color=discord.Color.from_rgb(255, 70, 85),
    )
    embed.set_image(url="attachment://daily_shop.png")
    embed.set_footer(text="Powered by localhostvlr")

    return embed, file, None


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="shop", description="ดูร้านค้าสกินประจำวัน (Daily Shop) ของเกม Valorant")
    @app_commands.describe(
        account="เลือกบัญชีที่ต้องการดูร้านค้า (เว้นว่างเพื่อดูบัญชีหลักที่เลือกไว้ • Owner ดูได้ทุกบัญชีใน DB)",
    )
    async def shop(self, interaction: discord.Interaction, account: str | None = None):
        # Defer reply (public in chat for /shop command)
        await interaction.response.defer(ephemeral=False)

        try:
            target_account, err_msg = await resolve_target_account(interaction.user.id, account)
            if not target_account:
                await interaction.followup.send(f"{interaction.user.mention} {err_msg}")
                return

            embed, file, shop_err = await build_shop_embed_and_file(target_account, interaction.user.mention)
            if shop_err or not embed or not file:
                await interaction.followup.send(shop_err or f"{interaction.user.mention} ไม่สามารถดึงข้อมูลร้านค้าได้")
                return

            await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            logger.exception("Error executing /shop command: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลร้านค้า กรุณาลองใหม่อีกครั้งในภายหลัง",
            )

    @shop.autocomplete("account")
    async def shop_account_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            is_owner = is_bot_owner(interaction.user.id)
            if is_owner:
                accounts = await get_all_users()
            else:
                accounts = await get_users_by_discord_id(interaction.user.id)

            q = current.strip().lower()
            choices: list[app_commands.Choice[str]] = []

            for idx, acc in enumerate(accounts, start=1):
                is_alive = _has_refresh_token(acc)
                dot = "🟢" if is_alive else "🔴"
                acc_name = acc.name or f"{acc.puuid[:8]}"
                shard = acc.shard.upper()

                if is_owner:
                    status_lbl = "พร้อมใช้" if is_alive else "ต้อง /login"
                    label = f"{dot} #{acc.id} {acc_name} ({shard} • {status_lbl})"
                    searchable = f"{acc.id} {acc_name} {shard} {acc.discord_user_id}".lower()
                else:
                    sel_star = " ⭐" if acc.selected else ""
                    status_lbl = "พร้อมใช้" if is_alive else "ต้อง /login"
                    label = f"{dot} #{idx} {acc_name} ({shard} • {status_lbl}){sel_star}"
                    searchable = f"{idx} {acc.id} {acc_name} {shard}".lower()

                if not q or q in searchable:
                    choices.append(app_commands.Choice(name=label[:100], value=str(acc.id)))
                    if len(choices) >= 25:
                        break

            return choices
        except Exception as e:
            logger.debug("Autocomplete error in /shop: %s", e)
            return []


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
