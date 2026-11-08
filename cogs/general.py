import logging
import os
import platform
import sys
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
import psutil
from cogs.shop import is_bot_owner

logger = logging.getLogger(__name__)


def make_progress_bar(percent: float, length: int = 10) -> str:
    """Generates a text progress bar like '███████░░░'."""
    filled = round((percent / 100.0) * length)
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


def format_duration(seconds: float) -> str:
    """Formats seconds into human-readable Thai duration (วัน ชม. นาที วินาที)."""
    sec = int(seconds)
    days = sec // 86400
    hours = (sec % 86400) // 3600
    minutes = (sec % 3600) // 60
    s = sec % 60
    parts = []
    if days > 0:
        parts.append(f"{days} วัน")
    if hours > 0:
        parts.append(f"{hours} ชม.")
    if minutes > 0:
        parts.append(f"{minutes} นาที")
    parts.append(f"{s} วินาที")
    return " ".join(parts)


class GeneralCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.update_status_loop.start()

    def cog_unload(self):
        self.update_status_loop.cancel()

    @tasks.loop(seconds=20)
    async def update_status_loop(self):
        """Periodically updates the bot's Discord Presence to display PC CPU & RAM usage vertically."""
        try:
            cpu_pct = psutil.cpu_percent()
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024**3)
            total_gb = vm.total / (1024**3)

            # แสดงผลแบบแนวตั้ง 2 บรรทัด (CPU ด้านบน และ RAM ด้านล่าง)
            status_text = f"CPU: {cpu_pct:.0f}%\nRAM: {used_gb:.1f}/{total_gb:.1f}GB ({vm.percent:.0f}%)"
            activity = discord.CustomActivity(name=status_text)

            await self.bot.change_presence(activity=activity)
        except Exception as e:
            logger.debug("Failed to update status presence: %s", e)

    @update_status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="status",
        description="ดูสถานะการทำงานของบอท, Uptime, Ping และทรัพยากรระบบ",
    )
    async def status_command(self, interaction: discord.Interaction):
        is_owner = is_bot_owner(interaction.user.id)

        # Bot Process RAM Usage
        process = psutil.Process()
        proc_mem_mb = process.memory_info().rss / (1024 * 1024)

        # Bot Uptime calculation
        start_time = getattr(self.bot, "start_time", datetime.now(timezone.utc))
        uptime_sec = (datetime.now(timezone.utc) - start_time).total_seconds()
        uptime_str = format_duration(uptime_sec)
        uptime_timestamp = int(start_time.timestamp())

        # Latency / Ping
        ping_ms = round(self.bot.latency * 1000)
        python_ver = platform.python_version()
        discord_ver = discord.__version__

        embed = discord.Embed(
            title="สถานะการทำงานของบอท (Bot Status)",
            color=discord.Color.from_rgb(0, 212, 255),  # Cyan / Tech Blue
        )

        embed.add_field(
            name="ข้อมูลการทำงานของบอท (Bot Runtime)",
            value=(
                f"• **ระยะเวลาทำงาน (Uptime):** `{uptime_str}` (<t:{uptime_timestamp}:R>)\n"
                f"• **ความเร็วการตอบสนอง (Ping):** `{ping_ms} ms`\n"
                f"• **หน่วยความจำที่บอทใช้ (RAM):** `{proc_mem_mb:.1f} MB`\n"
                f"• **เซิร์ฟเวอร์ที่ให้บริการ:** `{len(self.bot.guilds):,} เซิร์ฟเวอร์`"
            ),
            inline=False,
        )

        embed.add_field(
            name="สภาพแวดล้อม (Environment)",
            value=(
                f"• **Python:** `{python_ver}` • **Discord.py:** `{discord_ver}`"
            ),
            inline=False,
        )

        # Detailed host stats only displayed for Bot Owner to avoid information disclosure
        if is_owner:
            cpu_pct = psutil.cpu_percent(interval=0.2)
            logical_cores = psutil.cpu_count(logical=True) or 1
            physical_cores = psutil.cpu_count(logical=False) or logical_cores
            vm = psutil.virtual_memory()
            ram_used_gb = vm.used / (1024**3)
            ram_total_gb = vm.total / (1024**3)
            disk = psutil.disk_usage(os.getcwd())
            disk_used_gb = disk.used / (1024**3)
            disk_total_gb = disk.total / (1024**3)

            os_name = platform.system()
            os_release = platform.release()
            os_arch = platform.machine()
            display_os = f"{os_name} {os_release} ({os_arch})"

            embed.add_field(
                name="Host CPU & RAM (Admin View)",
                value=(
                    f"• CPU: `[{make_progress_bar(cpu_pct)}]` **{cpu_pct:.1f}%** ({logical_cores}T / {physical_cores}C)\n"
                    f"• RAM: `[{make_progress_bar(vm.percent)}]` **{ram_used_gb:.2f} / {ram_total_gb:.2f} GB**\n"
                    f"• Disk: `[{make_progress_bar(disk.percent)}]` **{disk_used_gb:.1f} / {disk_total_gb:.1f} GB**\n"
                    f"• OS: `{display_os}`"
                ),
                inline=False,
            )

        embed.set_footer(
            text="Powered by localhostvlr",
            icon_url="https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/27/largeicon.png",
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="help", description="ดูคำสั่งและคู่มือการใช้งานบอท ValoTracker ทั้งหมด")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="ValoTracker | คู่มือคำสั่ง",
            description=(
                "เชื่อมบัญชีด้วย `/login` ก่อนดูร้านค้าด้วย `/shop`\n"
                "ระบุผู้เล่นด้วย `Name#Tag`"
            ),
            color=discord.Color.from_rgb(255, 70, 85),  # Valorant signature red
        )

        embed.add_field(
            name="เริ่มต้นใช้งาน",
            value=(
                "1. ใช้ `/login` แล้วเข้าสู่ระบบผ่านลิงก์ Riot\n"
                "2. วาง Redirect URL ในขั้นตอนที่ 2 จากนั้นใช้ `/shop`"
            ),
            inline=False,
        )

        embed.add_field(
            name="ร้านค้าและแข่งขันสด",
            value=(
                "`/shop [account]` — ดูร้านค้าประจำวันของบัญชีที่เลือก\n"
                "`/live [player]` — ดูสถานะห้องแข่งสดและผู้เล่น"
            ),
            inline=False,
        )

        embed.add_field(
            name="สถิติและแมตช์",
            value=(
                "`/rank <player>` — ดูแรงค์และคะแนน RR/ELO\n"
                "`/leaderboard [region] [player]` — ดูหรือค้นหาอันดับผู้เล่น\n"
                "`/lastmatch <player>` — ดู scoreboard แมตช์ล่าสุด\n"
                "`/match <player>` — ดู 5 แมตช์ล่าสุดและ scoreboard"
            ),
            inline=False,
        )

        embed.add_field(
            name="แรงค์เพื่อนในเซิร์ฟเวอร์",
            value=(
                "`/checkrank` — ดูอันดับแรงค์เพื่อน\n"
                "`/addfriend <player>` — เพิ่มเพื่อนในตารางแรงค์\n"
                "`/removefriend <player>` — ลบเพื่อนจากตารางแรงค์"
            ),
            inline=False,
        )

        embed.add_field(
            name="บัญชีและระบบ",
            value=(
                "`/login [name]` — เชื่อมบัญชี Riot\n"
                "`/view-accounts` — ดูบัญชีที่เชื่อมและสถานะ\n"
                "`/select-account <id>` — เลือกบัญชีหลัก\n"
                "`/delete-account <id>` — ลบบัญชีที่เชื่อม\n"
                "`/account-list` *(Owner only)* — ดูบัญชีทั้งหมดในระบบ\n"
                "`/status` — ดูสถานะบอทและเครื่องโฮสต์\n"
                "`/help` — เปิดคู่มือคำสั่ง"
            ),
            inline=False,
        )

        embed.set_footer(
            text="Powered by localhostvlr",
            icon_url="https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/27/largeicon.png",
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
