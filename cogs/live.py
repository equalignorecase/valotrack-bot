import logging
import discord
from discord import app_commands
from discord.ext import commands

from cogs.shop import get_valid_account
from database.db import get_user_by_name
from services.live_renderer import render_live_match
from services.riot_auth import get_entitlement
from services.riot_live import get_live_match_raw, parse_live_lobby

logger = logging.getLogger(__name__)


class LiveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="live",
        description="ส่องห้องแข่งขันสด (Live Match Tracker) หรือหน้าเลือกตัวละคร (Agent Select) แบบเรียลไทม์",
    )
    @app_commands.describe(
        player="ระบุชื่อผู้เล่น เช่น Name#Tag เพื่อโฟกัสสถิติในห้องสด หรือเว้นว่างไว้เพื่อดูห้องของตัวเอง",
    )
    async def live(self, interaction: discord.Interaction, player: str | None = None):
        # Defer reply because fetching 10 players' MMR and rendering Pillow takes a moment
        await interaction.response.defer(ephemeral=False)

        try:
            account = None

            # 1. If player name is specified, check if it's a registered account in our DB
            if player:
                db_acc = await get_user_by_name(player)
                if db_acc:
                    # Try to use that account's session
                    account = await get_valid_account(db_acc.discord_user_id)

            # 2. Otherwise use the caller's currently selected account
            if not account:
                account = await get_valid_account(interaction.user.id)

            if not account:
                await interaction.followup.send(
                    f"{interaction.user.mention} ไม่พบบัญชีที่กำลังเลือกใช้งาน กรุณาเข้าสู่ระบบด้วยคำสั่ง `/login` ก่อน หรือตรวจสอบที่ `/view-accounts`\n"
                    f"*(หมายเหตุ: ระบบ Live จำเป็นต้องใช้ Token ของผู้เล่นในห้องเพื่อดึงข้อมูลจาก Riot API)*",
                )
                return

            # 3. Get entitlement
            entitlement = await get_entitlement(account.access_token)
            if not entitlement:
                await interaction.followup.send(
                    f"{interaction.user.mention} ขอสิทธิ์เข้าถึง (Entitlement) ไม่สำเร็จ เซสชัน Riot อาจหมดอายุ กรุณาล็อกอินใหม่ด้วย `/login`",
                )
                return

            # 4. Check live session from Riot GLZ server
            raw_session = await get_live_match_raw(
                shard=account.shard,
                puuid=account.puuid,
                access_token=account.access_token,
                entitlement=entitlement,
            )

            if not raw_session:
                desc = (
                    f"บัญชี **{account.name or 'ของคุณ'}** ไม่ได้อยู่ในห้องเลือกตัวละคร (Agent Select) หรือกำลังเล่นเกมอยู่ในขณะนี้\n\n"
                    f"💡 **วิธีใช้งาน:** เข้าคิวแข่งขันใน Valorant เมื่อเริ่มเลือกตัวหรือเข้าเกมแล้ว กดพิมพ์ `/live` อีกครั้งเพื่อส่องห้องสดทันที!"
                )
                if player:
                    desc += f"\n*(ระบบสแกนหา `{player}` ในห้องสดของ {account.name} แต่ปัจจุบันไม่ได้อยู่ในเกม)*"

                embed_idle = discord.Embed(
                    title="💤 ไม่พบการแข่งขันสดในขณะนี้",
                    description=desc,
                    color=discord.Color.from_rgb(138, 149, 160),
                )
                embed_idle.set_footer(text="Powered by localhostvlr • Riot Games GLZ API")
                await interaction.followup.send(embed=embed_idle)
                return

            # 5. Parse Lobby Data (Players, Names, Ranks, Parties, Map, Mode)
            lobby = await parse_live_lobby(
                shard=account.shard,
                raw_session=raw_session,
                user_puuid=account.puuid,
                access_token=account.access_token,
                entitlement=entitlement,
                focus_player=player,
            )

            # 6. Render High-Res Graphic Scoreboard Image with Pillow
            buf = await render_live_match(lobby)
            file = discord.File(fp=buf, filename="live_match.png")

            # 7. Construct Discord Embed message
            state_thai = "🟡 หน้าเลือกตัวละคร (Agent Select)" if lobby["is_pregame"] else "🔴 กำลังแข่งขันในเกม (In-Match)"
            map_name = lobby["map_name"]
            mode_name = lobby["mode_name"]
            total_players = len(lobby["all_players"])

            desc_lines = [
                f"**สถานะ:** `{state_thai}`",
                f"🗺️ **แผนที่:** `{map_name}` • 🎯 **โหมด:** `{mode_name}` • 🌐 **เซิร์ฟเวอร์:** `{lobby['shard']}`",
            ]

            if player:
                target_p = next(
                    (p for p in lobby["all_players"] if player.lower() in p["display"].lower()),
                    None,
                )
                if target_p:
                    desc_lines.append(
                        f"🎯 **โฟกัสผู้เล่น:** **{target_p['display']}** (เล่นเป็น `{target_p['agent_name']}` • {target_p['rank_name']})",
                    )
                else:
                    desc_lines.append(
                        f"ℹ️ *ไม่พบ `{player}` ในห้องนี้ (แสดงห้องของ {account.name or 'คุณ'} แทน)*",
                    )

            if lobby["is_pregame"]:
                desc_lines.append("\n🔒 *ทีมศัตรูจะถูกซ่อนไว้ในโหมดแข่งขันจนกว่าจะโหลดเข้าสู่แมตช์เต็มตัว*")

            embed = discord.Embed(
                title=f"🎮 ส่องห้องแข่งขันสด (Live Match): {map_name} ({mode_name})",
                description="\n".join(desc_lines),
                color=discord.Color.from_rgb(255, 70, 85) if not lobby["is_pregame"] else discord.Color.gold(),
            )
            embed.set_image(url="attachment://live_match.png")
            embed.set_footer(text=f"ตรวจพบผู้เล่น {total_players} คน • Powered by localhostvlr & Riot Games API")

            await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            logger.exception("Error executing /live command: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลห้องสด กรุณาลองใหม่อีกครั้งในภายหลัง",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LiveCog(bot))
