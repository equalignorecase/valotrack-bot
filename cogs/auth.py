import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from database.db import (
    delete_account,
    get_all_users,
    get_user_by_puuid_and_discord_id,
    get_users_by_discord_id,
    save_or_update_user,
    set_selected_account,
    update_account_shard,
    update_account_tokens,
)
from database.models import UserAccount
from services.account_renderer import render_accounts_dashboard
from services.crypto import decrypt_token, encrypt_token
from services.riot_auth import (
    generate_login_url,
    get_player_affinity,
    get_player_info,
    get_tokens,
    refresh_tokens,
)

logger = logging.getLogger(__name__)


def extract_auth_code(redirect_url: str) -> str | None:
    """Extracts the 'code' parameter from the Riot OAuth2 redirect URL or raw code string."""
    cleaned = redirect_url.strip().strip("\"'<>")
    try:
        # If user directly pasted code=xyz
        if cleaned.startswith("code="):
            return cleaned.split("code=", 1)[1].split("&")[0].split("#")[0].strip()

        parsed = urlparse(cleaned)
        # Check query string
        query_params = parse_qs(parsed.query)
        if "code" in query_params and query_params["code"]:
            return query_params["code"][0]
        # Check fragment if format differs
        if parsed.fragment:
            fragment_params = parse_qs(parsed.fragment)
            if "code" in fragment_params and fragment_params["code"]:
                return fragment_params["code"][0]

        # Fallback substring search if scheme was missing
        if "code=" in cleaned:
            parts = cleaned.split("code=", 1)
            code_candidate = parts[1].split("&")[0].split("#")[0].strip()
            if code_candidate:
                return code_candidate
    except Exception as e:
        logger.warning("Error parsing redirect URL: %s", e)
    return None


class LoginModal(discord.ui.Modal, title="กรอก Redirect URL จาก Riot"):
    def __init__(self, account_name: str = ""):
        super().__init__()
        self.account_name = account_name if account_name != "none" else ""

    url_input = discord.ui.TextInput(
        label="Redirect URL",
        placeholder="วาง URL หน้าเว็บที่ขึ้น error (http://localhost/redirect?code=...) ที่นี่...",
        style=discord.TextStyle.paragraph,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        url = self.url_input.value.strip()
        code = extract_auth_code(url)

        if not code:
            await interaction.followup.send(
                f"{interaction.user.mention} ไม่พบรหัสยืนยัน (code) ใน URL กรุณาตรวจสอบว่าคัดลอกมาครบถ้วนถูกต้องหรือไม่?",
                ephemeral=True,
            )
            return

        try:
            tokens = await get_tokens(code)
            if not tokens or "access_token" not in tokens:
                await interaction.followup.send(
                    f"{interaction.user.mention} เข้าสู่ระบบกับ Riot ล้มเหลว รหัสยืนยันอาจหมดอายุแล้ว กรุณาลองใหม่อีกครั้ง",
                    ephemeral=True,
                )
                return

            access_token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token", "")
            id_token = tokens.get("id_token")
            expires_in = tokens.get("expires_in", 3600)

            player_info = await get_player_info(access_token)
            if not player_info or "sub" not in player_info:
                await interaction.followup.send(
                    f"{interaction.user.mention} ดึงข้อมูลผู้เล่นจาก Riot ไม่สำเร็จ",
                    ephemeral=True,
                )
                return

            puuid = player_info["sub"]
            # Detect real live region/shard using Riot PAS endpoint
            shard = await get_player_affinity(access_token, id_token)

            # Determine nickname / Riot ID
            acct = player_info.get("acct") or {}
            game_name = acct.get("game_name")
            tag_line = acct.get("tag_line")
            riot_id = f"{game_name}#{tag_line}" if game_name and tag_line else "บัญชี Valorant"

            custom_name = self.account_name.strip() if self.account_name else ""
            if not custom_name or custom_name.lower() == "none":
                account_display_name = riot_id
            else:
                account_display_name = custom_name

            # Encrypt the refresh token using AES-256-GCM
            ct, nonce, tag = encrypt_token(refresh_token)

            token_expires = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            discord_user_id = interaction.user.id

            existing_user = await get_user_by_puuid_and_discord_id(discord_user_id, puuid)

            user_obj = UserAccount(
                id=existing_user.id if existing_user else None,
                discord_user_id=discord_user_id,
                puuid=puuid,
                encrypted_token=ct,
                nonce=nonce,
                tag=tag,
                shard=shard,
                name=account_display_name,
                selected=True,
                access_token=access_token,
                token_expires=token_expires,
            )

            await save_or_update_user(user_obj)

            if existing_user:
                await interaction.followup.send(
                    f"{interaction.user.mention} บัญชี **{account_display_name}** (`{shard.upper()}`) มีอยู่ในระบบแล้ว ทำการรีเฟรช Token และเลือกใช้งานบัญชีนี้เรียบร้อย!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"{interaction.user.mention} เข้าสู่ระบบ **{account_display_name}** (`{shard.upper()}`) สำเร็จเรียบร้อย! คุณสามารถใช้คำสั่ง `/shop` เพื่อดูร้านค้าประจำวันได้ทันที",
                    ephemeral=True,
                )

        except Exception as e:
            logger.exception("Error during login modal submission: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เข้าสู่ระบบล้มเหลว กรุณาลองใหม่อีกครั้งในภายหลัง",
                ephemeral=True,
            )


class LoginView(discord.ui.View):
    def __init__(self, login_url: str, account_name: str = "none"):
        super().__init__(timeout=300)
        self.account_name = account_name

        # ขั้นตอนที่ 1: ปุ่มลิงก์เปิดหน้าล็อกอิน Riot Games (ปุ่มแรก / ด้านซ้าย)
        btn_step1 = discord.ui.Button(
            label="ขั้นตอนที่ 1: เปิดลิงก์เข้าสู่ระบบ",
            url=login_url,
            style=discord.ButtonStyle.link,
        )
        # ขั้นตอนที่ 2: ปุ่มเปิดหน้าต่างกรอก URL (ปุ่มที่สอง / ด้านขวา)
        btn_step2 = discord.ui.Button(
            label="ขั้นตอนที่ 2: กรอก Redirect URL",
            style=discord.ButtonStyle.primary,
        )
        btn_step2.callback = self.open_modal_btn

        self.clear_items()
        self.add_item(btn_step1)
        self.add_item(btn_step2)

    async def open_modal_btn(self, interaction: discord.Interaction):
        await interaction.response.send_modal(LoginModal(self.account_name))


class AuthCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.auto_refresh_tokens_task.start()

    def cog_unload(self):
        self.auto_refresh_tokens_task.cancel()

    @tasks.loop(hours=24)
    async def auto_refresh_tokens_task(self):
        """
        Background keeper: Automatically refreshes Riot tokens every 24 hours
        for all registered accounts in ValoTracker.db to reset Riot's 30-day idle expiry timer.
        """
        logger.info("[Auto-Refresh Task] Starting daily background token refresh...")
        try:
            accounts = await get_all_users()
            if not accounts:
                logger.info("[Auto-Refresh Task] No accounts found in database to refresh.")
                return

            success_count = 0
            failed_count = 0

            for account in accounts:
                try:
                    r_token = decrypt_token(account.encrypted_token, account.nonce, account.tag)
                    token_data = await refresh_tokens(r_token)

                    if not token_data or "access_token" not in token_data:
                        logger.warning(
                            "[Auto-Refresh Task] Failed to refresh token for Account ID %s (%s, Discord: %s)",
                            account.id,
                            account.name,
                            account.discord_user_id,
                        )
                        failed_count += 1
                        continue

                    new_access_token = token_data["access_token"]
                    new_refresh_token = token_data.get("refresh_token", r_token)
                    expires_in = token_data.get("expires_in", 3600)

                    new_ct, new_nonce, new_tag = encrypt_token(new_refresh_token)
                    now_utc = datetime.now(timezone.utc)
                    new_expires = now_utc + timedelta(seconds=expires_in)

                    await update_account_tokens(
                        user_id=account.id,
                        access_token=new_access_token,
                        token_expires=new_expires,
                        encrypted_token=new_ct,
                        nonce=new_nonce,
                        tag=new_tag,
                    )

                    id_token = token_data.get("id_token")
                    if id_token:
                        live_shard = await get_player_affinity(new_access_token, id_token)
                        if live_shard and live_shard != account.shard:
                            await update_account_shard(account.id, live_shard)

                    success_count += 1
                    logger.info(
                        "[Auto-Refresh Task] Refreshed Account ID %s (%s) successfully.",
                        account.id,
                        account.name,
                    )
                except Exception as e:
                    logger.exception(
                        "[Auto-Refresh Task] Error refreshing Account ID %s (%s): %s",
                        account.id,
                        account.name,
                        e,
                    )
                    failed_count += 1

                # 2-second rate-limit pause between accounts
                await asyncio.sleep(2)

            logger.info(
                "[Auto-Refresh Task] Completed: %d succeeded, %d failed out of %d accounts.",
                success_count,
                failed_count,
                len(accounts),
            )
        except Exception as e:
            logger.exception("[Auto-Refresh Task] Unexpected error in task loop: %s", e)

    @auto_refresh_tokens_task.before_loop
    async def before_auto_refresh_tokens_task(self):
        await self.bot.wait_until_ready()
        # Delay 60s after startup to allow bot to finish loading and syncing
        await asyncio.sleep(60)

    @app_commands.command(name="login", description="เข้าสู่ระบบด้วยบัญชี Riot เพื่อดูร้านค้าและใช้งานคำสั่งต่างๆ")
    @app_commands.describe(name="ตั้งชื่อเล่นให้กับบัญชีนี้ (ไม่บังคับ เช่น ไอดีหลัก, ไอดีรอง)")
    async def login(self, interaction: discord.Interaction, name: str = "none"):
        login_url = generate_login_url()

        embed = discord.Embed(
            title="🔐 เข้าสู่ระบบ Riot Games",
            description=(
                "เข้าสู่ระบบอย่างปลอดภัยผ่านหน้าต่างทางการของ Riot Games\n\n"
                "💡 **คำแนะนำเพื่อความสะดวก & ป้องกันรหัสผิด**:\n"
                "แนะนำให้ **คัดลอกลิงก์ด้านล่างไปเปิดใน Chrome / Edge / Safari** ที่คุณเคยล็อกอิน Riot ไว้อยู่แล้ว "
                "ระบบจะจำเซสชันเดิมและล็อกอินให้อัตโนมัติทันที ไม่ต้องกรอกรหัสผ่านซ้ำ และหลีกเลี่ยงปัญหาของหน้าต่าง Discord"
            ),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="1️⃣ คัดลอกลิงก์เปิดในเบราว์เซอร์ภายนอก",
            value=f"```text\n{login_url}\n```\n*(หรือคลิกปุ่ม **ขั้นตอนที่ 1** ด้านล่างหากต้องการเปิดทันที)*",
            inline=False,
        )
        embed.add_field(
            name="2️⃣ นำ Redirect URL มาใส่",
            value="เมื่อล็อกอินเสร็จ หน้าเว็บจะเปลี่ยนเป็น `http://localhost/redirect?code=...` (ขึ้นหน้า Error เป็นเรื่องปกติ) ให้คัดลอก URL ทั้งหมดด้านบน แล้วกดปุ่ม **ขั้นตอนที่ 2: กรอก Redirect URL**",
            inline=False,
        )
        embed.set_footer(text="บอทจะไม่ทราบรหัสผ่านของคุณ • ปลอดภัยด้วยการเข้ารหัส AES-256-GCM")

        view = LoginView(login_url=login_url, account_name=name)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="view-accounts",
        description="ดูรายการบัญชี Riot ทั้งหมดที่เชื่อมโยงกับ Discord ของคุณ",
    )
    async def view_accounts(self, interaction: discord.Interaction):
        try:
            accounts = await get_users_by_discord_id(interaction.user.id)

            embed = discord.Embed(
                title="👤 บัญชี Riot ของคุณ",
                description="ใช้หมายเลข **ID** ด้านหน้าสำหรับคำสั่ง `/select-account` หรือ `/delete-account`",
                color=discord.Color.blue(),
            )

            if not accounts:
                embed.description = "คุณยังไม่ได้เชื่อมต่อบัญชีใดๆ (ใช้คำสั่ง `/login` เพื่อเริ่มเชื่อมต่อ)"
            else:
                lines = []
                for idx, acc in enumerate(accounts, start=1):
                    selected_badge = "   (**เลือกใช้งานอยู่**)" if acc.selected else ""
                    name = acc.name or "ไม่มีชื่อ"
                    lines.append(f"`{idx:2}` •   **{name}** •   `{acc.shard}`{selected_badge}")
                embed.add_field(name="รายการบัญชี", value="\n".join(lines), inline=False)
            embed.set_footer(text="Powered by localhostvlr")

            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            logger.exception("Error in view_accounts: %s", e)
            await interaction.response.send_message("เกิดข้อผิดพลาดในการโหลดข้อมูลบัญชี กรุณาลองใหม่อีกครั้ง", ephemeral=True)

    @app_commands.command(name="select-account", description="สลับไปใช้งานบัญชีที่ต้องการ")
    @app_commands.describe(account_number="หมายเลข ID ของบัญชีจากคำสั่ง /view-accounts (เช่น 1, 2, ...)")
    async def select_account(self, interaction: discord.Interaction, account_number: int):
        try:
            accounts = await get_users_by_discord_id(interaction.user.id)
            if account_number < 1 or account_number > len(accounts):
                await interaction.response.send_message(
                    f"{interaction.user.mention} ไม่พบบัญชีนี้ กรุณาพิมพ์ `/view-accounts` เพื่อดูหมายเลข ID ที่ถูกต้อง",
                    ephemeral=True,
                )
                return

            target_account = accounts[account_number - 1]
            success = await set_selected_account(interaction.user.id, target_account.id)

            if success:
                acc_name = target_account.name or f"ID {account_number}"
                await interaction.response.send_message(
                    f"{interaction.user.mention} สลับไปใช้งานบัญชี **{acc_name}** เรียบร้อยแล้ว!",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"{interaction.user.mention} ไม่สามารถสลับบัญชีได้ กรุณาลองใหม่อีกครั้ง",
                    ephemeral=True,
                )
        except Exception as e:
            logger.exception("Error in select_account: %s", e)
            await interaction.response.send_message(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการเลือกบัญชี",
                ephemeral=True,
            )

    @app_commands.command(name="delete-account", description="ลบบัญชี Riot ออกจากระบบ")
    @app_commands.describe(account_number="หมายเลข ID ของบัญชีจากคำสั่ง /view-accounts (เช่น 1, 2, ...)")
    async def delete_account_cmd(self, interaction: discord.Interaction, account_number: int):
        try:
            accounts = await get_users_by_discord_id(interaction.user.id)
            if account_number < 1 or account_number > len(accounts):
                await interaction.response.send_message(
                    f"{interaction.user.mention} ไม่พบบัญชีนี้ กรุณาพิมพ์ `/view-accounts` เพื่อดูหมายเลข ID ที่ถูกต้อง",
                    ephemeral=True,
                )
                return

            acc_to_delete = accounts[account_number - 1]
            deleted = await delete_account(interaction.user.id, acc_to_delete.id)

            if deleted:
                name = deleted.name or f"ID {account_number}"
                await interaction.response.send_message(
                    f"{interaction.user.mention} ลบบัญชี **{name}** ออกจากระบบเรียบร้อยแล้ว",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    f"{interaction.user.mention} ไม่สามารถลบบัญชีได้",
                    ephemeral=True,
                )
        except Exception as e:
            logger.exception("Error in delete_account: %s", e)
            await interaction.response.send_message(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการลบบัญชี กรุณาลองใหม่อีกครั้ง",
                ephemeral=True,
            )

    @app_commands.command(
        name="account-list",
        description="[Admin Only] ดูรายชื่อบัญชี Riot ทั้งหมดที่บันทึกไว้ในฐานข้อมูล ValoTracker.db",
    )
    async def account_list(self, interaction: discord.Interaction):
        # ล็อกสิทธิ์เฉพาะ User ID 578167102582685698 เท่านั้น
        allowed_id = 578167102582685698
        if interaction.user.id != allowed_id:
            await interaction.response.send_message(
                f"❌ **คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้!** คำสั่งนี้สงวนสิทธิ์เฉพาะ User ID `{allowed_id}` เท่านั้น",
                ephemeral=True,
            )
            return

        # Defer ephemeral reply
        await interaction.response.defer(ephemeral=True)

        try:
            accounts = await get_all_users()
            if not accounts:
                await interaction.followup.send(
                    "📭 ยังไม่มีบัญชี Riot ใดๆ ถูกบันทึกไว้ในฐานข้อมูล",
                    ephemeral=True,
                )
                return

            # Render High-Resolution Graphic Dashboard with Pillow (Auto-fit height)
            buf = render_accounts_dashboard(accounts)
            file = discord.File(fp=buf, filename="accounts_dashboard.png")

            now_utc = datetime.now(timezone.utc)
            valid_cnt = sum(1 for a in accounts if a.token_expires > now_utc)
            expired_cnt = len(accounts) - valid_cnt

            owner_mentions = []
            for a in accounts:
                is_expired = a.token_expires <= now_utc
                dot = "🔴" if is_expired else "🟢"
                active_str = "🟢 Active" if a.selected else "⚪ Inactive"
                riot_name = a.name if a.name else f"{a.puuid[:8]}..."
                exp_ts = int(a.token_expires.timestamp())
                exp_text = f"หมดอายุ <t:{exp_ts}:R>" if is_expired else f"ใช้ได้ถึง <t:{exp_ts}:R>"
                owner_mentions.append(
                    f"• `#{a.id}` **{riot_name}** (`{a.shard.upper()}`) → <@{a.discord_user_id}> ({active_str} • {dot} {exp_text})"
                )

            mentions_text = "\n".join(owner_mentions)

            embed = discord.Embed(
                title="📋 แดชบอร์ดรายชื่อบัญชี Riot ในระบบ (ValoTracker.db)",
                description=(
                    f"👑 **ผู้ดูแลระบบ:** <@{allowed_id}>\n"
                    f"📊 **สถิติรวม:** 🟢 พร้อมใช้: `{valid_cnt}` • 🔴 หมดอายุ: `{expired_cnt}` • ทั้งหมด: `{len(accounts)}` ไอดี\n\n"
                    f"👥 **แท็กโปรไฟล์เจ้าของบัญชี (Discord):**\n"
                    f"{mentions_text}"
                ),
                color=discord.Color.from_rgb(88, 101, 242),
            )
            embed.set_image(url="attachment://accounts_dashboard.png")
            embed.set_footer(text="ValoTracker.db • Admin Dashboard • Powered by localhostvlr")

            await interaction.followup.send(embed=embed, file=file, ephemeral=True)

        except Exception as e:
            logger.exception("Error in account_list: %s", e)
            await interaction.followup.send(
                "เกิดข้อผิดพลาดในการดึงข้อมูลบัญชีจากฐานข้อมูล กรุณาลองใหม่อีกครั้ง",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AuthCog(bot))


