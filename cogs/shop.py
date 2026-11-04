import asyncio
import io
import logging
import time
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from database.db import get_selected_user
from database.models import UserAccount
from services.riot_auth import get_entitlement, refresh_account_session
from services.riot_store import get_storefront
from services.shop_renderer import render_daily_shop
from services.valorant_api import get_weapon_info

logger = logging.getLogger(__name__)

# In-memory Daily Shop Cache: {(puuid, shard, player_name): {"cache_until": float, "reset_ts": int, "total_vp": int, "summary_text": str, "png_bytes": bytes}}
_SHOP_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
SHOP_CACHE_TTL_SECONDS = 600  # 10 minutes (or until daily shop resets)


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


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="shop", description="ดูร้านค้าสกินประจำวัน (Daily Shop) ของเกม Valorant")
    async def shop(self, interaction: discord.Interaction):
        # Defer reply since fetching multiple Riot & community endpoints takes a moment
        await interaction.response.defer()

        try:
            account = await get_valid_account(interaction.user.id)
            if not account:
                await interaction.followup.send(
                    f"{interaction.user.mention} ไม่พบบัญชีที่กำลังเลือกใช้งาน หรือเซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่ด้วยคำสั่ง `/login` หรือตรวจสอบที่ `/view-accounts`",
                )
                return

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
                    account = await get_valid_account(interaction.user.id, force_refresh=True)
                    if account:
                        entitlement = await get_entitlement(account.access_token)

                if not account or not entitlement:
                    await interaction.followup.send(
                        f"{interaction.user.mention} ขอสิทธิ์เข้าถึง (Entitlement) ไม่สำเร็จ เซสชัน Riot อาจหมดอายุ กรุณาล็อกอินใหม่ด้วย `/login`",
                    )
                    return

                storefront = await get_storefront(
                    shard=account.shard,
                    puuid=account.puuid,
                    entitlement=entitlement,
                    access_token=account.access_token,
                )

                if (not storefront or "SkinsPanelLayout" not in storefront) and not retried:
                    account = await get_valid_account(interaction.user.id, force_refresh=True)
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
                    await interaction.followup.send(
                        f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลร้านค้า ไม่พบข้อมูลสินค้าจาก Riot",
                    )
                    return

                panel = storefront["SkinsPanelLayout"]
                single_item_offers = panel.get("SingleItemOffers", [])
                store_offers = panel.get("SingleItemStoreOffers", [])
                remaining_seconds = max(0, int(panel.get("SingleItemOffersRemainingDurationInSeconds", 0)))

                if not single_item_offers:
                    await interaction.followup.send(
                        f"{interaction.user.mention} ไม่พบรายการสินค้าในร้านค้าประจำวัน",
                    )
                    return

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

            # Calculate time breakdown
            hours = remaining_seconds // 3600
            minutes = (remaining_seconds % 3600) // 60
            seconds = remaining_seconds % 60

            embed_desc = (
                f"⏳ **เวลาร้านค้ารีเซ็ต:** `{hours} ชม. {minutes} นาที {seconds} วินาที` (<t:{reset_timestamp}:R>)\n"
                f"🌐 **ภูมิภาค:** `{account.shard.upper()}` • 💰 **ราคารวมทั้งหมด:** `{total_vp:,} VP`\n\n"
                f"**📋 รายการสกินประจำวันนี้:**\n"
                f"{summary_text}"
            )

            embed = discord.Embed(
                title=f"🛒 ร้านค้าประจำวัน (Daily Shop): {account.name or 'บัญชี Valorant'}",
                description=embed_desc,
                color=discord.Color.from_rgb(255, 70, 85),
            )
            embed.set_image(url="attachment://daily_shop.png")
            embed.set_footer(text="Powered by localhostvlr")

            await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            logger.exception("Error executing /shop command: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลร้านค้า กรุณาลองใหม่อีกครั้งในภายหลัง",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
