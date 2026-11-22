import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.shop import account_choices, build_shop_embed_and_file, resolve_target_account
from database.db import (
    delete_auto_shop_subscription,
    get_account_by_id,
    get_auto_shop_subscription,
    get_enabled_auto_shop_subscriptions,
    update_auto_shop_last_result,
    upsert_auto_shop_subscription,
)
from database.models import AutoShopSubscription

logger = logging.getLogger(__name__)

ICT = timezone(timedelta(hours=7))  # Indochina Time (Bangkok) UTC+7, no DST
DAILY_SEND_TIME = time(hour=7, minute=5, tzinfo=ICT)  # 5 min after the 00:00 UTC shop reset


def next_send_at(now: datetime | None = None) -> datetime:
    """Returns the next 07:05 ICT occurrence."""
    current = now or datetime.now(ICT)
    target = current.replace(hour=7, minute=5, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target


class AutoShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_shop_task.start()

    def cog_unload(self):
        self.daily_shop_task.cancel()

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------
    async def _resolve_target(self, sub: AutoShopSubscription):
        """Resolves the DM user or channel configured for this subscription."""
        if sub.target_type == "channel" and sub.channel_id:
            channel = self.bot.get_channel(sub.channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(sub.channel_id)
                except discord.HTTPException:
                    return None
            return channel

        user = self.bot.get_user(sub.discord_user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(sub.discord_user_id)
            except discord.HTTPException:
                return None
        return user

    async def _deliver(self, sub: AutoShopSubscription) -> tuple[bool, str]:
        """Builds and sends the daily shop. Returns (success, reason)."""
        account = await get_account_by_id(sub.account_id)
        if not account:
            return False, "ไม่พบบัญชีที่ผูกไว้ (อาจถูกลบไปแล้ว)"

        embed, file, err = await build_shop_embed_and_file(
            account, f"<@{sub.discord_user_id}>"
        )
        if err or not embed or not file:
            return False, err or "ดึงข้อมูลร้านค้าไม่สำเร็จ"

        target = await self._resolve_target(sub)
        if target is None:
            return False, "ไม่พบปลายทาง (ช่องอาจถูกลบ หรือผู้ใช้ไม่พร้อมรับ DM)"

        try:
            await target.send(embed=embed, file=file)
        except discord.Forbidden:
            return False, "บอทไม่มีสิทธิ์ส่งข้อความ/แนบไฟล์ในปลายทางนี้"
        except discord.HTTPException as e:
            return False, f"Discord API error: {e.status}"

        return True, "ok"

    async def _notify_failure(self, sub: AutoShopSubscription, reason: str) -> None:
        """Best-effort short notice so the owner knows the auto-send failed."""
        target = await self._resolve_target(sub)
        if target is None:
            return
        try:
            await target.send(
                f"⚠️ <@{sub.discord_user_id}> ส่งร้านค้าอัตโนมัติไม่สำเร็จ: {reason}"
            )
        except discord.HTTPException:
            logger.warning(
                "[Auto-Shop] Could not notify user %s about failure: %s",
                sub.discord_user_id,
                reason,
            )

    @tasks.loop(time=DAILY_SEND_TIME)
    async def daily_shop_task(self):
        """Sends the daily shop to every enabled subscription at 07:05 ICT."""
        logger.info("[Auto-Shop] Starting daily shop broadcast...")
        try:
            subs = await get_enabled_auto_shop_subscriptions()
            if not subs:
                logger.info("[Auto-Shop] No subscriptions enabled.")
                return

            ok_count = 0
            fail_count = 0
            for sub in subs:
                try:
                    ok, reason = await self._deliver(sub)
                    await update_auto_shop_last_result(
                        sub.discord_user_id, "ok" if ok else reason
                    )
                    if ok:
                        ok_count += 1
                        logger.info(
                            "[Auto-Shop] Sent shop to user %s (account %s)",
                            sub.discord_user_id,
                            sub.account_id,
                        )
                    else:
                        fail_count += 1
                        logger.warning(
                            "[Auto-Shop] Failed for user %s (account %s): %s",
                            sub.discord_user_id,
                            sub.account_id,
                            reason,
                        )
                        await self._notify_failure(sub, reason)
                except Exception as e:
                    fail_count += 1
                    logger.exception(
                        "[Auto-Shop] Unexpected error for user %s: %s",
                        sub.discord_user_id,
                        e,
                    )

                # 2-second pause between deliveries to stay friendly to rate limits
                await asyncio.sleep(2)

            logger.info(
                "[Auto-Shop] Completed: %d sent, %d failed out of %d subscriptions.",
                ok_count,
                fail_count,
                len(subs),
            )
        except Exception as e:
            logger.exception("[Auto-Shop] Unexpected error in daily task: %s", e)

    @daily_shop_task.before_loop
    async def before_daily_shop_task(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    autoshop = app_commands.Group(
        name="autoshop",
        description="ตั้งค่าส่งร้านค้าประจำวันอัตโนมัติทุกวัน 07:05 น. (เวลาไทย)",
    )

    @autoshop.command(name="on", description="เปิดส่งร้านค้าอัตโนมัติทุกวัน 07:05 น.")
    @app_commands.describe(
        channel="ช่องที่จะส่ง (เว้นว่าง = ส่ง DM ส่วนตัว)",
        account="บัญชีที่ต้องการ (เว้นว่าง = บัญชีหลักที่เลือกอยู่)",
    )
    async def autoshop_on(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
        account: str | None = None,
    ):
        if channel is not None:
            if interaction.guild is None or channel.guild.id != interaction.guild.id:
                await interaction.response.send_message(
                    "❌ เลือกได้เฉพาะช่องในเซิร์ฟเวอร์นี้เท่านั้น", ephemeral=True
                )
                return

            user_perms = interaction.user.guild_permissions
            if not (user_perms.manage_channels or user_perms.administrator):
                await interaction.response.send_message(
                    "❌ การส่งไปยังช่องในเซิร์ฟเวอร์ต้องมีสิทธิ์ **Manage Channels** "
                    "(หรือเลือกเว้นว่างเพื่อส่ง DM แทน)",
                    ephemeral=True,
                )
                return

            bot_perms = channel.permissions_for(interaction.guild.me)
            if not (bot_perms.send_messages and bot_perms.attach_files and bot_perms.embed_links):
                await interaction.response.send_message(
                    f"❌ บอทไม่มีสิทธิ์ส่งข้อความ/แนบไฟล์/ฝังลิงก์ใน {channel.mention}",
                    ephemeral=True,
                )
                return

        target_account, err_msg = await resolve_target_account(interaction.user.id, account)
        if not target_account:
            await interaction.response.send_message(
                f"❌ {err_msg}", ephemeral=True
            )
            return

        sub = await upsert_auto_shop_subscription(
            interaction.user.id,
            target_account.id,
            "channel" if channel else "dm",
            channel.id if channel else None,
        )

        acc_label = target_account.name or f"ID #{target_account.id}"
        destination = channel.mention if channel else "DM ส่วนตัว"
        embed = discord.Embed(
            title="✅ เปิดส่งร้านค้าอัตโนมัติแล้ว",
            description=(
                f"**บัญชี:** `{acc_label}` ({target_account.shard.upper()})\n"
                f"**ปลายทาง:** {destination}\n"
                f"**เวลาส่ง:** ทุกวัน 07:05 น. (เวลาไทย)\n"
                f"**รอบถัดไป:** <t:{int(next_send_at().timestamp())}:R>"
            ),
            color=discord.Color.from_rgb(255, 70, 85),
        )
        embed.set_footer(text="ปิดได้ด้วย /autoshop off • ทดสอบได้ด้วย /autoshop test")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autoshop_on.autocomplete("account")
    async def autoshop_on_account_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await account_choices(interaction, current)

    @autoshop.command(name="off", description="ปิดส่งร้านค้าอัตโนมัติ")
    async def autoshop_off(self, interaction: discord.Interaction):
        removed = await delete_auto_shop_subscription(interaction.user.id)
        if removed:
            await interaction.response.send_message(
                "🛑 ปิดส่งร้านค้าอัตโนมัติเรียบร้อยแล้ว", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "ℹ️ คุณยังไม่ได้เปิดส่งร้านค้าอัตโนมัติ", ephemeral=True
            )

    @autoshop.command(name="status", description="ดูสถานะการส่งร้านค้าอัตโนมัติ")
    async def autoshop_status(self, interaction: discord.Interaction):
        sub = await get_auto_shop_subscription(interaction.user.id)
        if not sub or not sub.enabled:
            await interaction.response.send_message(
                "ℹ️ คุณยังไม่ได้เปิดส่งร้านค้าอัตโนมัติ (เปิดได้ด้วย `/autoshop on`)",
                ephemeral=True,
            )
            return

        account = await get_account_by_id(sub.account_id)
        acc_label = (account.name if account and account.name else f"ID #{sub.account_id}")
        shard = f" ({account.shard.upper()})" if account else ""

        if sub.target_type == "channel" and sub.channel_id:
            destination = f"<#{sub.channel_id}>"
        else:
            destination = "DM ส่วนตัว"

        if sub.last_sent_at:
            status_icon = "✅" if sub.last_status == "ok" else "⚠️"
            status_text = sub.last_status or "ไม่ทราบสาเหตุ"
            last_line = (
                f"{status_icon} <t:{int(sub.last_sent_at.timestamp())}:f> — {status_text}"
            )
        else:
            last_line = "ยังไม่เคยส่ง"

        embed = discord.Embed(
            title="⚙️ สถานะส่งร้านค้าอัตโนมัติ",
            color=discord.Color.from_rgb(255, 70, 85),
            description=(
                f"**สถานะ:** เปิดใช้งาน\n"
                f"**บัญชี:** `{acc_label}`{shard}\n"
                f"**ปลายทาง:** {destination}\n"
                f"**เวลาส่ง:** ทุกวัน 07:05 น. (เวลาไทย)\n"
                f"**รอบถัดไป:** <t:{int(next_send_at().timestamp())}:R>\n"
                f"**ผลส่งครั้งล่าสุด:** {last_line}"
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @autoshop.command(name="test", description="ทดลองส่งร้านค้าไปยังปลายทางที่ตั้งไว้ทันที")
    async def autoshop_test(self, interaction: discord.Interaction):
        sub = await get_auto_shop_subscription(interaction.user.id)
        if not sub or not sub.enabled:
            await interaction.response.send_message(
                "ℹ️ คุณยังไม่ได้เปิดส่งร้านค้าอัตโนมัติ (เปิดได้ด้วย `/autoshop on`)",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        ok, reason = await self._deliver(sub)
        await update_auto_shop_last_result(
            interaction.user.id, "ok" if ok else reason
        )

        if ok:
            await interaction.followup.send(
                "✅ ส่งร้านค้าไปยังปลายทางที่ตั้งไว้สำเร็จ", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"❌ ส่งไม่สำเร็จ: {reason}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoShopCog(bot))
