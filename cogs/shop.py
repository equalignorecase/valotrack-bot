import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from database.db import get_selected_user, update_account_shard, update_account_tokens
from database.models import UserAccount
from services.crypto import decrypt_token, encrypt_token
from services.riot_auth import get_entitlement, get_player_affinity, refresh_tokens
from services.riot_store import get_storefront
from services.shop_renderer import render_daily_shop
from services.valorant_api import get_weapon_info

logger = logging.getLogger(__name__)


async def get_valid_account(discord_user_id: int) -> UserAccount | None:
    """
    Retrieves the user's selected account, refreshing the access token if needed.
    """
    account = await get_selected_user(discord_user_id)
    if not account:
        return None

    # Check if token is still valid (with 1-minute safety window)
    now_utc = datetime.now(timezone.utc)
    if account.token_expires > (now_utc + timedelta(minutes=1)):
        return account

    # Token is expired or expiring soon, refresh it
    try:
        r_token = decrypt_token(account.encrypted_token, account.nonce, account.tag)
        token_data = await refresh_tokens(r_token)

        if not token_data or "access_token" not in token_data:
            logger.error("Failed to refresh token with Riot API for user %s", discord_user_id)
            return None

        new_access_token = token_data["access_token"]
        new_refresh_token = token_data.get("refresh_token", r_token)
        expires_in = token_data.get("expires_in", 3600)

        new_ct, new_nonce, new_tag = encrypt_token(new_refresh_token)
        new_expires = now_utc + timedelta(seconds=expires_in)

        await update_account_tokens(
            user_id=account.id,
            access_token=new_access_token,
            token_expires=new_expires,
            encrypted_token=new_ct,
            nonce=new_nonce,
            tag=new_tag,
        )

        account.access_token = new_access_token
        account.token_expires = new_expires
        account.encrypted_token = new_ct
        account.nonce = new_nonce
        account.tag = new_tag

        # Check and update real live shard if id_token is returned
        id_token = token_data.get("id_token")
        if id_token:
            live_shard = await get_player_affinity(new_access_token, id_token)
            if live_shard and live_shard != account.shard:
                await update_account_shard(account.id, live_shard)
                account.shard = live_shard

        logger.info("Successfully refreshed token for user account ID %s (Shard: %s)", account.id, account.shard)
        return account
    except Exception as e:
        logger.exception("Error in get_valid_account while refreshing token: %s", e)
        return None


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
                    f"{interaction.user.mention} ไม่พบบัญชีที่กำลังเลือกใช้งาน กรุณาเข้าสู่ระบบด้วยคำสั่ง `/login` ก่อน หรือตรวจสอบที่ `/view-accounts`",
                )
                return

            entitlement = await get_entitlement(account.access_token)
            if not entitlement:
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

            if not storefront or "SkinsPanelLayout" not in storefront:
                await interaction.followup.send(
                    f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลร้านค้า ไม่พบข้อมูลสินค้าจาก Riot",
                )
                return

            panel = storefront["SkinsPanelLayout"]
            single_item_offers = panel.get("SingleItemOffers", [])
            store_offers = panel.get("SingleItemStoreOffers", [])
            remaining_seconds = panel.get("SingleItemOffersRemainingDurationInSeconds", 0)

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

            # Render 1x4 Panorama Shop Image
            buf = await render_daily_shop(
                skins=skins_data,
                player_name=account.name or "Valorant Player",
                shard=account.shard,
            )
            file = discord.File(fp=buf, filename="daily_shop.png")

            # Calculate time breakdown
            hours = remaining_seconds // 3600
            minutes = (remaining_seconds % 3600) // 60
            seconds = remaining_seconds % 60
            reset_timestamp = int(datetime.now(timezone.utc).timestamp()) + remaining_seconds

            summary_text = "\n".join(skin_summary_lines)
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
