import logging
import os
import platform
import sys
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
import psutil

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
        description="ดูสถานะการทำงานของบอท ข้อมูลสเปกเครื่อง PC, CPU, RAM และ Disk Usage แบบเรียลไทม์",
    )
    async def status_command(self, interaction: discord.Interaction):
        # Measure CPU percent over a short 0.2s sample
        cpu_pct = psutil.cpu_percent(interval=0.2)
        logical_cores = psutil.cpu_count(logical=True) or 1
        physical_cores = psutil.cpu_count(logical=False) or logical_cores

        # RAM Usage
        vm = psutil.virtual_memory()
        ram_used_gb = vm.used / (1024**3)
        ram_total_gb = vm.total / (1024**3)
        ram_free_gb = vm.available / (1024**3)

        # Disk Usage of current working directory
        disk = psutil.disk_usage(os.getcwd())
        disk_used_gb = disk.used / (1024**3)
        disk_total_gb = disk.total / (1024**3)

        # Bot Uptime calculation
        start_time = getattr(self.bot, "start_time", datetime.now(timezone.utc))
        uptime_sec = (datetime.now(timezone.utc) - start_time).total_seconds()
        uptime_str = format_duration(uptime_sec)
        uptime_timestamp = int(start_time.timestamp())

        # Latency / Ping
        ping_ms = round(self.bot.latency * 1000)

        # OS and Machine info
        os_name = platform.system()
        os_release = platform.release()
        os_arch = platform.machine()
        if os_name == "Darwin":
            display_os = f"macOS {os_release} ({os_arch})"
        elif os_name == "Windows":
            display_os = f"Windows {platform.win32_edition() if hasattr(platform, 'win32_edition') else ''} {os_release} ({os_arch})"
        else:
            display_os = f"{os_name} {os_release} ({os_arch})"

        python_ver = platform.python_version()
        discord_ver = discord.__version__

        embed = discord.Embed(
            title="🖥️ สถานะระบบและสเปกเครื่อง (System & Bot Status)",
            color=discord.Color.from_rgb(0, 212, 255),  # Cyan / Tech Blue
        )

        embed.add_field(
            name="⚡ การใช้งาน CPU (Processor)",
            value=(
                f"`[{make_progress_bar(cpu_pct)}]` **{cpu_pct:.1f}%**\n"
                f"• คอร์ประมวลผล: `{logical_cores} Threads` (`{physical_cores} Cores`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="🧠 การใช้งาน RAM (Memory)",
            value=(
                f"`[{make_progress_bar(vm.percent)}]` **{vm.percent:.1f}%**\n"
                f"• ใช้ไป: `{ram_used_gb:.2f} GB` / `{ram_total_gb:.2f} GB` (ว่าง `{ram_free_gb:.2f} GB`)"
            ),
            inline=False,
        )

        embed.add_field(
            name="💾 พื้นที่จัดเก็บ Disk (Storage)",
            value=(
                f"`[{make_progress_bar(disk.percent)}]` **{disk.percent:.1f}%**\n"
                f"• ใช้ไป: `{disk_used_gb:.1f} GB` / `{disk_total_gb:.1f} GB`"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚙️ ข้อมูลเครื่องและระบบปฏิบัติการ (Host & OS)",
            value=(
                f"• **ระบบปฏิบัติการ:** `{display_os}`\n"
                f"• **Python:** `{python_ver}` • **Discord.py:** `{discord_ver}`"
            ),
            inline=False,
        )

        embed.add_field(
            name="🤖 ข้อมูลการทำงานของบอท (Bot Runtime)",
            value=(
                f"• **ระยะเวลาทำงาน (Uptime):** `{uptime_str}` (<t:{uptime_timestamp}:R>)\n"
                f"• **ความเร็วการตอบสนอง (Ping):** `{ping_ms} ms`\n"
                f"• **เซิร์ฟเวอร์ที่ให้บริการ:** `{len(self.bot.guilds):,} เซิร์ฟเวอร์`"
            ),
            inline=False,
        )

        embed.set_footer(
            text="Powered by localhostvlr",
            icon_url="https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/27/largeicon.png",
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="ดูคำสั่งและคู่มือการใช้งานบอท ValoTracker ทั้งหมด")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎯 ValoTracker — คู่มือการใช้งาน",
            description=(
                "บอท Discord ครบวงจรสำหรับ Valorant: ตรวจสอบร้านค้าสกินประจำวัน, "
                "ดูสถิติการแข่ง, แรงค์, Leaderboard และส่องห้องแข่งขันสด (Live Match)"
            ),
            color=discord.Color.from_rgb(255, 70, 85),  # Valorant signature red
        )

        embed.add_field(
            name="🚀 วิธีเริ่มต้นใช้งาน (Quick Start)",
            value=(
                "1. พิมพ์คำสั่ง `/login` เพื่อสร้างลิงก์เข้าสู่ระบบ Riot ที่ปลอดภัย\n"
                "2. คลิกปุ่มเพื่อล็อกอินผ่านหน้าเว็บทางการของ Riot Games\n"
                "3. คัดลอก URL หน้าเว็บที่ขึ้น Error (`http://localhost/redirect?code=...`)\n"
                "4. กดปุ่ม **ขั้นตอนที่ 2: กรอก Redirect URL** แล้ววาง URL ที่คัดลอกมา\n"
                "5. พิมพ์คำสั่ง `/shop` เพื่อดูสกินประจำวันของคุณได้ทันที!"
            ),
            inline=False,
        )

        embed.add_field(
            name="🛒 ร้านค้า & แข่งขันสด (Shop & Live)",
            value=(
                "• **`/shop`**\n"
                "  ดูร้านค้าสกินปืนประจำวัน `4 กระบอก` แบบภาพรวมพาโนรามาพร้อมราคา `VP`\n"
                "• **`/live [player]`**\n"
                "  ส่องห้องแข่งขันสด (In-Match) หรือหน้าเลือกตัวละคร (Agent Select) แสดงแรงค์ ปาร์ตี้ และสถิติ"
            ),
            inline=False,
        )

        embed.add_field(
            name="📊 สถิติ & ประวัติการแข่ง (Stats & Matches)",
            value=(
                "• **`/rank <player>`**\n"
                "  ดูอันดับแรงค์, คะแนน `RR`, `ELO` และ `Peak Rank` (ระบุ `Name#Tag`)\n"
                "• **`/leaderboard [region] [player]`**\n"
                "  ดูตารางท็อป Radiant หรือค้นหาอันดับผู้เล่นในบอร์ดเซิร์ฟเวอร์\n"
                "• **`/lastmatch <player>`**\n"
                "  ดูรูปภาพ **Scoreboard เต็ม** ของแมตช์ล่าสุด (`KDA`, `ACS`, `HS%`)\n"
                "• **`/match <player>`**\n"
                "  ดูประวัติ `5 แมตช์ล่าสุด` พร้อมเมนู Dropdown เลือกเปิดดู Scoreboard"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔐 จัดการบัญชี & ระบบ (Accounts & System)",
            value=(
                "• **`/login [name]`** — เชื่อมต่อบัญชี Riot (ตั้งชื่อเล่นไอดีได้)\n"
                "• **`/view-accounts`** — ดูรายชื่อบัญชี Riot ทั้งหมดที่ผูกไว้\n"
                "• **`/select-account <id>`** — สลับบัญชีหลักสำหรับดูร้านค้า\n"
                "• **`/delete-account <id>`** — ลบบัญชีและล้าง Token ออกจากระบบ\n"
                "• **`/account-list`** — [Owner Only] ดูรายชื่อบัญชีทั้งหมดในระบบ DB\n"
                "• **`/status`** — ดูสถานะเครื่องโฮสต์ (CPU %, RAM, Disk, Ping, Uptime)\n"
                "• **`/help`** — แสดงคู่มือการใช้งานและรายการคำสั่งทั้งหมด"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔒 ความปลอดภัยและความเป็นส่วนตัว (Security)",
            value=(
                "• **ไม่เก็บรหัสผ่าน**: บอทจะไม่ทราบและไม่เคยขอรหัสผ่านของคุณ\n"
                "• **เข้ารหัสแน่นหนา**: Token จะถูกเข้ารหัสด้วย **AES-256-GCM** ก่อนบันทึกเสมอ\n"
                "• **ความเป็นส่วนตัว**: คำสั่งจัดการบัญชีจะแสดงผลให้คุณเห็นคนเดียวเท่านั้น (`ephemeral`)"
            ),
            inline=False,
        )

        embed.set_footer(
            text="Powered by localhostvlr",
            icon_url="https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/27/largeicon.png",
        )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCog(bot))
