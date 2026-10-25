import asyncio
import logging
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import config
from database.db import (
    add_tracked_friend,
    ensure_default_friends,
    get_tracked_friends,
    remove_tracked_friend,
)
from database.models import TrackedFriend
from services.henrik_api import (
    get_account,
    get_last_match,
    get_leaderboard,
    get_matches,
    get_mmr,
    get_stored_mmr_history,
)
from services.scoreboard import render_scoreboard

logger = logging.getLogger(__name__)

ICT = timezone(timedelta(hours=7))  # Indochina Time (Bangkok) UTC+7


def format_thai_match_date(timestamp: int) -> str:
    """Formats UNIX timestamp into a concise Thai datetime string that won't line-wrap."""
    if not timestamp:
        return "ไม่มีข้อมูลเวลา"
    try:
        dt = datetime.fromtimestamp(timestamp, tz=ICT)
    except Exception:
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)

    thai_months = [
        "", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
        "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."
    ]
    month_name = thai_months[dt.month] if 1 <= dt.month <= 12 else str(dt.month)
    return f"{dt.day} {month_name} {dt.year} • {dt.strftime('%H:%M')} น."


def _pad_thai(text: str, target_width: int = 10) -> str:
    """Pads Thai text for monospace alignment taking 0-width combining marks into account."""
    visual_len = sum(0 if unicodedata.category(c) in ("Mn", "Me") else 1 for c in text)
    return text + " " * max(0, target_width - visual_len)


def parse_riot_id(raw_id: str) -> tuple[str, str] | None:
    """Parses 'Name#Tag' into (name, tag)."""
    if "#" not in raw_id:
        return None
    parts = raw_id.split("#", 1)
    name = parts[0].strip()
    tag = parts[1].strip()
    if not name or not tag:
        return None
    return name, tag


def get_rank_color(tier_name: str) -> discord.Color:
    """Returns a representative Discord color based on Valorant rank."""
    name_lower = tier_name.lower()
    if "radiant" in name_lower:
        return discord.Color.from_rgb(255, 255, 170)
    if "immortal" in name_lower:
        return discord.Color.from_rgb(180, 20, 60)
    if "ascendant" in name_lower:
        return discord.Color.from_rgb(30, 160, 100)
    if "diamond" in name_lower:
        return discord.Color.from_rgb(180, 130, 240)
    if "platinum" in name_lower:
        return discord.Color.from_rgb(50, 160, 200)
    if "gold" in name_lower:
        return discord.Color.gold()
    if "silver" in name_lower:
        return discord.Color.light_grey()
    if "bronze" in name_lower:
        return discord.Color.dark_orange()
    if "iron" in name_lower:
        return discord.Color.dark_grey()
    return discord.Color.red()


def get_rank_badge(tier_name: str) -> str:
    """Returns a representative badge/emoji for a Valorant rank tier."""
    name_lower = tier_name.lower()
    if "radiant" in name_lower:
        return "👑"
    if "immortal" in name_lower:
        return "🔴"
    if "ascendant" in name_lower:
        return "🟢"
    if "diamond" in name_lower:
        return "💎"
    if "platinum" in name_lower:
        return "🌐"
    if "gold" in name_lower:
        return "🟡"
    if "silver" in name_lower:
        return "⚪"
    if "bronze" in name_lower:
        return "🟤"
    if "iron" in name_lower:
        return "🔘"
    return "❔"


class MatchSelectView(discord.ui.View):
    """View with a dropdown for users to select a match and inspect its full scoreboard image."""

    def __init__(self, matches: list[dict[str, Any]], target_puuid: str, player_name: str):
        super().__init__(timeout=180)
        self.matches = matches
        self.target_puuid = target_puuid
        self.player_name = player_name

        options = []
        for idx, m in enumerate(matches, start=1):
            metadata = m.get("metadata") or {}
            teams = m.get("teams") or {}
            map_name = str(metadata.get("map") or "Map")
            mode = str(metadata.get("mode") or metadata.get("queue") or "Competitive")

            # Determine outcome for target player
            players = (m.get("players") or {}).get("all_players") or []
            target_p = next((p for p in players if p.get("puuid") == target_puuid), None)

            outcome_str = ""
            score_str = ""
            agent_str = "Agent"
            kda_desc = ""
            if target_p:
                agent_str = str(target_p.get("character") or "Agent")
                stats = target_p.get("stats") or {}
                k = stats.get("kills") or 0
                d = stats.get("deaths") or 0
                a = stats.get("assists") or 0
                kda_desc = f" • KDA: {k}/{d}/{a}"

                team_key = str(target_p.get("team") or "").lower()
                team_data = teams.get(team_key) or {}
                won = team_data.get("has_won")
                rw = team_data.get("rounds_won") or 0
                rl = team_data.get("rounds_lost") or 0
                score_str = f"{rw}-{rl}"
                if won is True:
                    outcome_str = "🟢 ชนะ"
                elif won is False:
                    outcome_str = "🔴 แพ้"
                else:
                    outcome_str = "🟡 เสมอ"

            label = f"แมตช์ที่ {idx}: {map_name} ({score_str}) • {outcome_str}"
            desc = f"โหมด {mode} • เล่นเป็น {agent_str}{kda_desc}"
            options.append(discord.SelectOption(label=label, value=str(idx - 1), description=desc))

        select = discord.ui.Select(
            placeholder="📊 เลือกแมตช์เพื่อดูรูป Scoreboard เต็ม...",
            min_values=1,
            max_values=1,
            options=options,
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_idx = int(interaction.data["values"][0])
        match_data = self.matches[selected_idx]

        metadata = match_data.get("metadata") or {}
        map_name = str(metadata.get("map") or "Map")
        mode = str(metadata.get("mode") or metadata.get("queue") or "Competitive")

        buf = await render_scoreboard(match_data, target_puuid=self.target_puuid)
        file = discord.File(fp=buf, filename=f"scoreboard_match_{selected_idx + 1}.png")

        embed = discord.Embed(
            title=f"📊 สกอร์บอร์ดแมตช์ที่ {selected_idx + 1}: {map_name} ({mode})",
            description=f"ผลการแข่งขันของ **{self.player_name}**",
            color=discord.Color.from_rgb(255, 70, 85),
        )
        embed.set_image(url=f"attachment://scoreboard_match_{selected_idx + 1}.png")
        embed.set_footer(text="Powered by localhostvlr")

        await interaction.followup.send(embed=embed, file=file)


class StatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Register persistent view so /checkrank buttons work permanently across bot restarts
        self.bot.add_view(FriendRankView())

    @app_commands.command(
        name="rank",
        description="ดูแรงค์และคะแนนปัจจุบันของผู้เล่น (จำเป็นต้องระบุ Riot ID เช่น Name#Tag)",
    )
    @app_commands.describe(
        player="ระบุชื่อผู้เล่นพร้อมแท็ก เช่น Name#Tag (จำเป็นต้องใส่ # ตามด้วยแท็ก)",
    )
    async def rank_cmd(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=False)

        parsed = parse_riot_id(player)
        if not parsed:
            await interaction.followup.send(
                f"{interaction.user.mention} รูปแบบ Riot ID ไม่ถูกต้อง! กรุณาระบุในรูปแบบ `ชื่อ#แท็ก` เช่น `TenZ#0001` (ต้องมีเครื่องหมาย `#`)"
            )
            return

        name, tag = parsed
        acc = await get_account(name, tag)
        if not acc:
            await interaction.followup.send(
                f"{interaction.user.mention} ไม่พบข้อมูลผู้เล่น `{player}` ในระบบ Riot Games กรุณาตรวจสอบชื่อและแท็กอีกครั้ง"
            )
            return

        puuid = acc.get("puuid")
        shard = acc.get("region", "ap")
        display_name = f"{acc.get('name', name)}#{acc.get('tag', tag)}"

        try:
            mmr_data = await get_mmr(shard=shard, puuid=puuid)
            if not mmr_data or "current_data" not in mmr_data:
                await interaction.followup.send(
                    f"{interaction.user.mention} ไม่พบข้อมูลแรงค์สำหรับ **{display_name}** "
                    f"(ผู้เล่นอาจยังไม่ได้เล่นโหมด Competitive หรือโปรไฟล์ถูกตั้งเป็นส่วนตัว)"
                )
                return

            current = mmr_data.get("current_data", {})
            highest = mmr_data.get("highest_rank", {})

            rank_name = current.get("currenttierpatched", "Unrated")
            rr = current.get("ranking_in_tier", 0)
            elo = current.get("elo", 0)
            last_change = current.get("mmr_change_to_last_game", 0)
            images = current.get("images", {})
            rank_icon = images.get("large") or images.get("small")

            # Format last game RR change
            if last_change > 0:
                change_str = f"📈 `+{last_change} RR`"
            elif last_change < 0:
                change_str = f"📉 `{last_change} RR`"
            else:
                change_str = "➖ `0 RR`"

            peak_rank = highest.get("patched_tier", "ไม่มีข้อมูล")
            peak_season = highest.get("season", "").upper()
            peak_str = f"🏆 **{peak_rank}** (`{peak_season}`)" if peak_season else f"🏆 **{peak_rank}**"

            embed = discord.Embed(
                title=f"🎖️ ข้อมูลแรงค์ Valorant: {display_name}",
                color=get_rank_color(rank_name),
            )
            if rank_icon:
                embed.set_thumbnail(url=rank_icon)

            embed.add_field(
                name="🏅 แรงค์ปัจจุบัน",
                value=f"**{rank_name}**\n(`{rr} / 100 RR`)",
                inline=True,
            )
            embed.add_field(
                name="📊 แต้ม ELO รวม",
                value=f"`{elo:,}`",
                inline=True,
            )
            embed.add_field(
                name="⚡ แมตช์ล่าสุด",
                value=change_str,
                inline=True,
            )
            embed.add_field(
                name="🌟 แรงค์สูงสุดที่เคยได้ (Peak Rank)",
                value=peak_str,
                inline=False,
            )

            embed.set_footer(text=f"ภูมิภาค: {shard.upper()} • Powered by localhostvlr")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.exception("Error in /rank command: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลแรงค์ กรุณาลองใหม่อีกครั้งในภายหลัง"
            )

    @app_commands.command(
        name="lastmatch",
        description="ดูรูปภาพ Scoreboard การแข่งขันแมตช์ล่าสุดของผู้เล่น (จำเป็นต้องระบุ Riot ID)",
    )
    @app_commands.describe(
        player="ระบุชื่อผู้เล่นพร้อมแท็ก เช่น Name#Tag (จำเป็นต้องใส่ # ตามด้วยแท็ก)",
    )
    async def lastmatch_cmd(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=False)

        parsed = parse_riot_id(player)
        if not parsed:
            await interaction.followup.send(
                f"{interaction.user.mention} รูปแบบ Riot ID ไม่ถูกต้อง! กรุณาระบุในรูปแบบ `ชื่อ#แท็ก` เช่น `TenZ#0001` (ต้องมีเครื่องหมาย `#`)"
            )
            return

        name, tag = parsed
        acc = await get_account(name, tag)
        if not acc:
            await interaction.followup.send(
                f"{interaction.user.mention} ไม่พบข้อมูลผู้เล่น `{player}` ในระบบ Riot Games กรุณาตรวจสอบชื่อและแท็กอีกครั้ง"
            )
            return

        puuid = acc.get("puuid")
        shard = acc.get("region", "ap")
        display_name = f"{acc.get('name', name)}#{acc.get('tag', tag)}"

        try:
            match_data = await get_last_match(shard=shard, puuid=puuid)
            if not match_data or "metadata" not in match_data:
                await interaction.followup.send(
                    f"{interaction.user.mention} ไม่พบประวัติการแข่งขันล่าสุดสำหรับ **{display_name}**"
                )
                return

            metadata = match_data.get("metadata") or {}
            map_name = str(metadata.get("map") or "Map")
            mode = str(metadata.get("mode") or metadata.get("queue") or "Competitive")
            teams = match_data.get("teams") or {}
            players = (match_data.get("players") or {}).get("all_players") or []

            # Target player result
            target_p = next((p for p in players if p.get("puuid") == puuid), None)
            outcome_str = "ผลการแข่งขัน"
            rounds_won = 0
            rounds_lost = 0
            embed_color = discord.Color.red()

            if target_p:
                team_key = str(target_p.get("team") or "").lower()
                t_info = teams.get(team_key) or {}
                rounds_won = t_info.get("rounds_won") or 0
                rl = t_info.get("rounds_lost") or 0
                rounds_lost = rl
                has_won = t_info.get("has_won")
                if has_won is True:
                    outcome_str = "🏆 ชัยชนะ (VICTORY)"
                    embed_color = discord.Color.green()
                elif has_won is False:
                    outcome_str = "💀 พ่ายแพ้ (DEFEAT)"
                    embed_color = discord.Color.red()
                else:
                    outcome_str = "🤝 เสมอ (DRAW)"
                    embed_color = discord.Color.gold()

            # Render Scoreboard image using Pillow
            buf = await render_scoreboard(match_data, target_puuid=puuid)
            file = discord.File(fp=buf, filename="scoreboard.png")

            embed = discord.Embed(
                title=f"{outcome_str} — แผนที่ {map_name} ({rounds_won} - {rounds_lost})",
                description=f"สกอร์บอร์ดแมตช์ล่าสุดของ **{display_name}** ในโหมด **{mode}**",
                color=embed_color,
            )
            embed.set_image(url="attachment://scoreboard.png")
            embed.set_footer(text=f"ภูมิภาค: {shard.upper()} • Powered by localhostvlr")

            await interaction.followup.send(embed=embed, file=file)

        except Exception as e:
            logger.exception("Error in /lastmatch command: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูลแมตช์ล่าสุด กรุณาลองใหม่อีกครั้งในภายหลัง"
            )

    @app_commands.command(
        name="match",
        description="ดูประวัติ 5 แมตช์ล่าสุด พร้อมเมนูเลือกเปิดรูป Scoreboard เต็ม",
    )
    @app_commands.describe(
        player="ระบุชื่อผู้เล่นพร้อมแท็ก เช่น Name#Tag (จำเป็นต้องใส่ # ตามด้วยแท็ก)",
    )
    async def match_cmd(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=False)

        parsed = parse_riot_id(player)
        if not parsed:
            await interaction.followup.send(
                f"{interaction.user.mention} รูปแบบ Riot ID ไม่ถูกต้อง! กรุณาระบุในรูปแบบ `ชื่อ#แท็ก` เช่น `TenZ#0001` (ต้องมีเครื่องหมาย `#`)"
            )
            return

        name, tag = parsed
        acc = await get_account(name, tag)
        if not acc:
            await interaction.followup.send(
                f"{interaction.user.mention} ไม่พบข้อมูลผู้เล่น `{player}` ในระบบ Riot Games กรุณาตรวจสอบชื่อและแท็กอีกครั้ง"
            )
            return

        puuid = acc.get("puuid")
        shard = acc.get("region", "ap")
        display_name = f"{acc.get('name', name)}#{acc.get('tag', tag)}"

        try:
            matches_task = get_matches(shard=shard, puuid=puuid, size=5)
            mmr_hist_task = get_stored_mmr_history(shard=shard, puuid=puuid)
            matches_res, mmr_res = await asyncio.gather(matches_task, mmr_hist_task, return_exceptions=True)

            matches = matches_res if isinstance(matches_res, list) else []
            if not matches:
                await interaction.followup.send(
                    f"{interaction.user.mention} ไม่พบประวัติการแข่งขันสำหรับ **{display_name}**"
                )
                return

            mmr_hist = mmr_res if isinstance(mmr_res, list) else []
            mmr_map = {
                item.get("match_id"): item
                for item in mmr_hist
                if isinstance(item, dict) and item.get("match_id")
            }

            wins = 0
            losses = 0
            draws = 0
            match_cards = []

            for idx, m in enumerate(matches, start=1):
                metadata = m.get("metadata") or {}
                teams = m.get("teams") or {}
                players = (m.get("players") or {}).get("all_players") or []
                rounds_played = metadata.get("rounds_played") or 0

                map_name = str(metadata.get("map") or "Map")
                mode = str(metadata.get("mode") or metadata.get("queue") or "Competitive")
                game_start = metadata.get("game_start") or 0
                match_id = metadata.get("matchid")

                target_p = next((p for p in players if p.get("puuid") == puuid), None)

                agent_name = "Agent"
                tier_name = "Unrated"
                kda_str = "0/0/0"
                kd_ratio = 0.0
                acs_val = 0
                hs_pct = 0
                outcome_str = "จบการแข่งขัน"
                score_str = "0 - 0"

                if target_p:
                    agent_name = str(target_p.get("character") or "Agent")
                    tier_name = str(target_p.get("currenttier_patched") or "Unrated")

                    team_key = str(target_p.get("team") or "").lower()
                    t_info = teams.get(team_key) or {}
                    rw = t_info.get("rounds_won") or 0
                    rl = t_info.get("rounds_lost") or 0
                    score_str = f"{rw} - {rl}"
                    has_won = t_info.get("has_won")

                    if has_won is True:
                        wins += 1
                        outcome_str = f"🟢 ชนะ ({score_str})"
                    elif has_won is False:
                        losses += 1
                        outcome_str = f"🔴 แพ้ ({score_str})"
                    elif has_won is None and (rw > 0 or rl > 0):
                        if rw > rl:
                            wins += 1
                            outcome_str = f"🟢 ชนะ ({score_str})"
                        elif rw < rl:
                            losses += 1
                            outcome_str = f"🔴 แพ้ ({score_str})"
                        else:
                            draws += 1
                            outcome_str = f"🟡 เสมอ ({score_str})"
                    else:
                        draws += 1
                        outcome_str = f"🟡 เสมอ ({score_str})"

                    stats = target_p.get("stats") or {}
                    k = stats.get("kills") or 0
                    d = stats.get("deaths") or 0
                    a = stats.get("assists") or 0
                    kda_str = f"{k}/{d}/{a}"
                    kd_ratio = round(k / max(d, 1), 2)
                    score = stats.get("score") or 0
                    acs_val = round(score / max(rounds_played, 1))

                    hs = stats.get("headshots") or 0
                    bs = stats.get("bodyshots") or 0
                    ls = stats.get("legshots") or 0
                    total_s = hs + bs + ls
                    hs_pct = round((hs / max(total_s, 1)) * 100)

                # Look up MMR History for this match if available
                is_competitive = mode.lower() == "competitive"
                mmr_item = mmr_map.get(match_id)
                if mmr_item:
                    tier_override = mmr_item.get("currenttier_patched")
                    if tier_override:
                        tier_name = str(tier_override)
                    rr_change = mmr_item.get("mmr_change_to_last_game")
                    rr_in_tier = mmr_item.get("ranking_in_tier")
                    if rr_change is not None:
                        if rr_change > 0:
                            rr_str = f"📈 +{rr_change} RR"
                        elif rr_change < 0:
                            rr_str = f"📉 {rr_change} RR"
                        else:
                            rr_str = "➖ 0 RR"
                        if rr_in_tier is not None:
                            rr_str += f" ({rr_in_tier}/100)"
                    else:
                        rr_str = "โหมด Competitive" if is_competitive else f"- (โหมด {mode})"
                else:
                    if is_competitive:
                        rr_str = "โหมด Competitive"
                    else:
                        rr_str = f"- (โหมด {mode})"

                # Format clean, aligned boxed card
                box_lines = [
                    f"{_pad_thai('ผลแข่งขัน')} : {outcome_str}",
                    f"{_pad_thai('ระดับแรงค์')} : {tier_name}",
                    f"{_pad_thai('แต้ม (RR)')} : {rr_str}",
                    f"{_pad_thai('เอเจนต์')} : {agent_name} ({mode})",
                    f"{_pad_thai('สถิติ KDA')} : {kda_str} • ACS {acs_val} • HS {hs_pct}%",
                    f"{_pad_thai('เวลาที่เล่น')} : {format_thai_match_date(game_start)}",
                ]
                box_content = "```\n" + "\n".join(box_lines) + "\n```"
                time_badge = f"🕒 เล่นเมื่อ <t:{game_start}:R>" if game_start else ""
                card_value = f"{box_content}\n{time_badge}" if time_badge else box_content

                header_title = f"`แมตช์ที่ {idx}` • 🗺️ {map_name}"
                match_cards.append((header_title, card_value))

            total_counted = wins + losses + draws
            win_rate = round((wins / max(total_counted, 1)) * 100)
            draw_text = f" / 🟡 `{draws} เสมอ`" if draws > 0 else ""

            embed_desc = (
                f"📊 **ภาพรวม 5 แมตช์ล่าสุด:** 🟢 `{wins} ชนะ` / 🔴 `{losses} แพ้`{draw_text} (Win Rate: `{win_rate}%`)\n"
                f"👇 *เลือกแมตช์จากเมนู Dropdown ด้านล่างเพื่อดูรูปภาพ **Scoreboard เต็ม** ได้ทันที*"
            )

            embed = discord.Embed(
                title=f"📜 ประวัติการแข่งขัน 5 แมตช์ล่าสุด: {display_name}",
                description=embed_desc,
                color=discord.Color.from_rgb(255, 70, 85),
            )

            for header, content in match_cards:
                embed.add_field(name=header, value=content, inline=False)

            embed.set_footer(text=f"ภูมิภาค: {shard.upper()} • Powered by localhostvlr")

            view = MatchSelectView(matches=matches, target_puuid=puuid, player_name=display_name)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception("Error in /match command: %s", e)
            await interaction.followup.send(
                f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงประวัติการแข่งขัน กรุณาลองใหม่อีกครั้งในภายหลัง"
            )

    @app_commands.command(
        name="leaderboard",
        description="ดูตารางจัดอันดับผู้เล่น Radiant ท็อปของเซิร์ฟเวอร์ หรือค้นหาอันดับผู้เล่น",
    )
    @app_commands.describe(
        region="เลือกเซิร์ฟเวอร์/ภูมิภาคที่ต้องการดู (ค่าเริ่มต้น: AP / เอเชีย)",
        player="ค้นหาชื่อผู้เล่นในบอร์ด เช่น TenZ หรือ Day1#1119 (ไม่จำเป็นต้องระบุ)",
    )
    @app_commands.choices(
        region=[
            app_commands.Choice(name="Asia-Pacific (AP / ไทย / สิงคโปร์)", value="ap"),
            app_commands.Choice(name="North America (NA / อเมริกาเหนือ)", value="na"),
            app_commands.Choice(name="Europe (EU / ยุโรป)", value="eu"),
            app_commands.Choice(name="Korea (KR / เกาหลี)", value="kr"),
            app_commands.Choice(name="Brazil (BR / บราซิล)", value="br"),
            app_commands.Choice(name="Latin America (LATAM / ละตินอเมริกา)", value="latam"),
        ]
    )
    async def leaderboard_cmd(
        self,
        interaction: discord.Interaction,
        region: app_commands.Choice[str] | None = None,
        player: str | None = None,
    ):
        await interaction.response.defer(ephemeral=False)
        shard = region.value if region else "ap"
        try:
            if player and "#" in player:
                p_parts = player.split("#", 1)
                p_name, p_tag = p_parts[0].strip(), p_parts[1].strip()
                direct_data = await get_leaderboard(shard, name=p_name, tag=p_tag)
                if direct_data and direct_data.get("players"):
                    matches = direct_data.get("players", [])
                    embed = build_leaderboard_search_embed(direct_data, shard, player, matches)
                    # Fetch top 10 for view switch button
                    top_data = await get_leaderboard(shard) or direct_data
                    view = LeaderboardSearchView(top_data, shard)
                    await interaction.followup.send(embed=embed, view=view)
                    return
                else:
                    fallback_data = await get_leaderboard(shard) or {}
                    embed = build_leaderboard_search_embed(fallback_data, shard, player, [])
                    await interaction.followup.send(embed=embed)
                    return

            data = await get_leaderboard(shard)
            if not data or not data.get("players"):
                await interaction.followup.send(
                    f"{interaction.user.mention} ไม่สามารถดึงข้อมูล Leaderboard ของภูมิภาค `{shard.upper()}` ได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง"
                )
                return

            if player:
                query = player.strip().lower()
                all_players = data.get("players", [])
                matches = [
                    p
                    for p in all_players
                    if not p.get("IsAnonymized", False)
                    and (
                        query in (p.get("gameName") or "").lower()
                        or query in f"{(p.get('gameName') or '').lower()}#{(p.get('tagLine') or '').lower()}"
                    )
                ]
                embed = build_leaderboard_search_embed(data, shard, player, matches)
                view = LeaderboardSearchView(data, shard) if matches else None
                await interaction.followup.send(embed=embed, view=view)
            else:
                max_pg = min(10, (len(data.get("players", [])) + 9) // 10) or 1
                view = LeaderboardView(data, shard, page=1, per_page=10, max_pages=max_pg)
                embed = build_leaderboard_embed(data, shard, page=1, per_page=10, max_pages=max_pg)
                await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            logger.exception("Error in /leaderboard command: %s", e)
            try:
                await interaction.followup.send(
                    f"{interaction.user.mention} เกิดข้อผิดพลาดในการดึงข้อมูล Leaderboard กรุณาลองใหม่อีกครั้งในภายหลัง"
                )
            except Exception:
                pass

    @app_commands.command(
        name="checkrank",
        description="ตรวจสอบแรงค์ของเพื่อนๆ ในเซิร์ฟเวอร์ (แสดงหน้าละ 5 คน พร้อมปุ่มอัพเดทแรงค์)",
    )
    async def checkrank_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        guild_id = interaction.guild_id or 0

        # Seed default friends if this server has none
        friends = await get_tracked_friends(guild_id)
        if not friends:
            friends = await ensure_default_friends(guild_id, config.DEFAULT_FRIENDS)

        if not friends:
            await interaction.followup.send(
                f"{interaction.user.mention} ยังไม่มีรายชื่อเพื่อนในระบบ! ใช้คำสั่ง `/addfriend` เพื่อเพิ่มเพื่อนได้เลย"
            )
            return

        tasks = [fetch_player_rank_summary(f) for f in friends]
        summaries = await asyncio.gather(*tasks)
        summaries = sorted(summaries, key=lambda p: p["elo"], reverse=True)

        view = FriendRankView(guild_id=guild_id, player_summaries=summaries, page=0, per_page=5)
        embed = view.get_embed()
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="addfriend",
        description="เพิ่มเพื่อนเข้าสู่ระบบตรวจสอบแรงค์ (/checkrank)",
    )
    @app_commands.describe(
        player="ชื่อผู้เล่นพร้อมแท็ก เช่น Name#Tag (จำเป็นต้องมี #)",
    )
    async def addfriend_cmd(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=False)
        parsed = parse_riot_id(player)
        if not parsed:
            await interaction.followup.send(
                f"{interaction.user.mention} ❌ รูปแบบ Riot ID ไม่ถูกต้อง! กรุณาระบุในรูปแบบ `ชื่อ#แท็ก` เช่น `TenZ#0001` (ต้องมีเครื่องหมาย `#`)"
            )
            return

        name, tag = parsed
        acc = await get_account(name, tag)
        if not acc:
            await interaction.followup.send(
                f"{interaction.user.mention} ❌ ไม่พบข้อมูลผู้เล่น `{player}` ในระบบ Riot Games กรุณาตรวจสอบชื่อและแท็กอีกครั้ง"
            )
            return

        puuid = acc.get("puuid")
        shard = acc.get("region", "ap")
        real_name = acc.get("name", name)
        real_tag = acc.get("tag", tag)
        display_name = f"{real_name}#{real_tag}"

        guild_id = interaction.guild_id or 0
        await ensure_default_friends(guild_id, config.DEFAULT_FRIENDS)
        await add_tracked_friend(guild_id, real_name, real_tag, puuid, shard)

        mmr_data = await get_mmr(shard=shard, puuid=puuid)
        rank_name = "Unrated"
        rr = 0
        if mmr_data and "current_data" in mmr_data:
            curr = mmr_data.get("current_data", {})
            rank_name = curr.get("currenttierpatched", "Unrated")
            rr = curr.get("ranking_in_tier", 0)

        embed = discord.Embed(
            title="✅ เพิ่มเพื่อนเข้าแก๊งสำเร็จ!",
            description=(
                f"เพิ่ม **{display_name}** เข้าสู่ระบบตรวจสอบแรงค์ `/checkrank` แล้ว\n"
                f"🏅 แรงค์ปัจจุบัน: **{rank_name}** (`{rr} RR`)\n"
                f"🌍 ภูมิภาค: `{shard.upper()}`"
            ),
            color=get_rank_color(rank_name),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="removefriend",
        description="ลบเพื่อนออกจากระบบตรวจสอบแรงค์ (/checkrank)",
    )
    @app_commands.describe(
        player="เลือกหรือระบุชื่อเพื่อนที่ต้องการลบ (เช่น Name#Tag)",
    )
    async def removefriend_cmd(self, interaction: discord.Interaction, player: str):
        await interaction.response.defer(ephemeral=False)
        guild_id = interaction.guild_id or 0
        await ensure_default_friends(guild_id, config.DEFAULT_FRIENDS)

        parsed = parse_riot_id(player)
        if parsed:
            name, tag = parsed
        else:
            clean = player.strip().lower()
            friends = await get_tracked_friends(guild_id)
            match = next((f for f in friends if f.name.lower() == clean or f.riot_id.lower() == clean), None)
            if match:
                name, tag = match.name, match.tag
            else:
                await interaction.followup.send(
                    f"{interaction.user.mention} ❌ ไม่พบผู้เล่น `{player}` ในรายชื่อเพื่อน กรุณาระบุในรูปแบบ `ชื่อ#แท็ก`"
                )
                return

        success = await remove_tracked_friend(guild_id, name, tag)
        if success:
            await interaction.followup.send(
                f"🗑️ ลบ **{name}#{tag}** ออกจากระบบตรวจสอบแรงค์ `/checkrank` เรียบร้อยแล้ว"
            )
        else:
            await interaction.followup.send(
                f"{interaction.user.mention} ⚠️ ไม่พบผู้เล่น `{name}#{tag}` ในรายชื่อเพื่อนของเซิร์ฟเวอร์นี้"
            )

    @removefriend_cmd.autocomplete("player")
    async def removefriend_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild_id = interaction.guild_id or 0
        friends = await get_tracked_friends(guild_id)
        if not friends:
            friends = await ensure_default_friends(guild_id, config.DEFAULT_FRIENDS)

        choices = []
        for f in friends:
            riot_id = f.riot_id
            if current.lower() in riot_id.lower():
                choices.append(app_commands.Choice(name=riot_id, value=riot_id))
        return choices[:25]


REGION_TITLES = {
    "ap": "Asia-Pacific (APAC)",
    "na": "North America",
    "eu": "Europe",
    "kr": "Korea",
    "br": "Brazil",
    "latam": "Latin America",
}


def build_leaderboard_embed(
    data: dict[str, Any],
    shard: str,
    page: int = 1,
    per_page: int = 10,
    max_pages: int = 10,
) -> discord.Embed:
    players = data.get("players", [])
    total_players = data.get("total_players", len(players))
    radiant_threshold = data.get("radiant_threshold", 0)
    last_update = data.get("last_update")

    start_idx = (page - 1) * per_page
    end_idx = min(start_idx + per_page, len(players))
    page_players = players[start_idx:end_idx]

    reg_name = REGION_TITLES.get(shard.lower(), shard.upper())
    time_str = f"<t:{last_update}:R>" if last_update else "เร็วๆ นี้"

    desc_lines = [
        f"👑 **เกณฑ์ขั้นต่ำขึ้น Radiant:** `{radiant_threshold} RR`",
        f"👥 **ผู้เล่นติดบอร์ดทั้งหมด:** `{total_players:,} คน` • 🕒 **อัปเดต:** {time_str}\n",
    ]

    medal_icons = {1: "🥇", 2: "🥈", 3: "🥉"}

    for p in page_players:
        rank = p.get("leaderboardRank", 0)
        medal = medal_icons.get(rank, "🔹")
        is_anon = p.get("IsAnonymized", False)
        if is_anon:
            player_str = "*🕵️ Secret Agent (ซ่อนชื่อ)*"
        else:
            gn = p.get("gameName") or "Player"
            tag = p.get("tagLine") or ""
            player_str = f"**{gn}#{tag}**" if tag else f"**{gn}**"

        rr = p.get("rankedRating", 0)
        wins = p.get("numberOfWins", 0)

        desc_lines.append(
            f"{medal} `#{rank:<2}` • {player_str}\n"
            f"   └ ⭐ `{rr:,} RR` • 🏆 `{wins:,} ชนะ`"
        )

    embed = discord.Embed(
        title=f"🏆 Valorant Radiant Leaderboard — {reg_name}",
        description="\n".join(desc_lines),
        color=discord.Color.from_rgb(255, 235, 120),  # Radiant Gold
    )
    embed.set_thumbnail(
        url="https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/27/largeicon.png"
    )
    embed.set_footer(
        text=f"ภูมิภาค: {shard.upper()} • หน้า {page}/{max_pages} (อันดับ {start_idx + 1} - {end_idx}) • Powered by localhostvlr"
    )
    return embed


def build_leaderboard_search_embed(
    data: dict[str, Any],
    shard: str,
    query: str,
    matches: list[dict[str, Any]],
) -> discord.Embed:
    radiant_threshold = data.get("radiant_threshold", 0)
    reg_name = REGION_TITLES.get(shard.lower(), shard.upper())

    if not matches:
        embed = discord.Embed(
            title=f"🔍 ค้นหาใน Leaderboard — {reg_name}",
            description=(
                f"❌ ไม่พบผู้เล่น **`{query}`** ในตาราง Leaderboard ท็อป 15,000 คนของเซิร์ฟเวอร์ `{shard.upper()}`\n\n"
                f"💡 *ผู้เล่นอาจอยู่อันดับต่ำกว่า Immortal 1 หรือตั้งโปรไฟล์เป็นส่วนตัว*"
            ),
            color=discord.Color.red(),
        )
        return embed

    desc_lines = [
        f"🎯 ผลการค้นหาสำหรับ **`{query}`** ในภูมิภาค `{reg_name}` (พบ {len(matches)} คน):\n",
    ]
    medal_icons = {1: "🥇", 2: "🥈", 3: "🥉"}

    for p in matches[:8]:
        rank = p.get("leaderboardRank", 0)
        medal = medal_icons.get(rank, "🔹")
        gn = p.get("gameName") or "Player"
        tag = p.get("tagLine") or ""
        player_str = f"**{gn}#{tag}**" if tag else f"**{gn}**"
        rr = p.get("rankedRating", 0)
        wins = p.get("numberOfWins", 0)
        tier = "Radiant" if rr >= radiant_threshold else "Immortal"

        desc_lines.append(
            f"{medal} `#{rank}` • {player_str}\n"
            f"   └ ⭐ `{rr:,} RR` ({tier}) • 🏆 `{wins:,} ชนะ`"
        )

    embed = discord.Embed(
        title=f"🔍 ผลการค้นหา Leaderboard — {reg_name}",
        description="\n".join(desc_lines),
        color=discord.Color.from_rgb(255, 235, 120),
    )
    embed.set_thumbnail(
        url="https://media.valorant-api.com/competitivetiers/03621f52-342b-cf4e-4f86-9350a49c6d04/27/largeicon.png"
    )
    embed.set_footer(text=f"เกณฑ์ Radiant: {radiant_threshold} RR • Powered by localhostvlr")
    return embed


class LeaderboardView(discord.ui.View):
    def __init__(
        self,
        data: dict[str, Any],
        shard: str,
        page: int = 1,
        per_page: int = 10,
        max_pages: int = 10,
    ):
        super().__init__(timeout=180)
        self.data = data
        self.shard = shard
        self.page = page
        self.per_page = per_page
        self.max_pages = max_pages
        self.update_components()

    def update_components(self):
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.max_pages
        self.page_indicator.label = f"หน้า {self.page} / {self.max_pages}"

    @discord.ui.button(label="◀️ ก่อนหน้า", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 1:
            self.page -= 1
            self.update_components()
            embed = build_leaderboard_embed(self.data, self.shard, self.page, self.per_page, self.max_pages)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="หน้า 1 / 10", style=discord.ButtonStyle.primary, disabled=True)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="ถัดไป ▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.max_pages:
            self.page += 1
            self.update_components()
            embed = build_leaderboard_embed(self.data, self.shard, self.page, self.per_page, self.max_pages)
            await interaction.response.edit_message(embed=embed, view=self)


class LeaderboardSearchView(discord.ui.View):
    def __init__(self, data: dict[str, Any], shard: str):
        super().__init__(timeout=180)
        self.data = data
        self.shard = shard

    @discord.ui.button(label="📋 ดู Top 10 บอร์ดเต็ม", style=discord.ButtonStyle.primary)
    async def view_top10(self, interaction: discord.Interaction, button: discord.ui.Button):
        new_view = LeaderboardView(self.data, self.shard, page=1)
        embed = build_leaderboard_embed(self.data, self.shard, page=1)
        await interaction.response.edit_message(embed=embed, view=new_view)


async def fetch_player_rank_summary(friend: TrackedFriend, force_refresh: bool = False) -> dict[str, Any]:
    name = friend.name
    tag = friend.tag
    puuid = friend.puuid
    shard = friend.region or "ap"

    try:
        if not puuid:
            acc = await get_account(name, tag)
            if acc:
                puuid = acc.get("puuid")
                shard = acc.get("region", shard)
                asyncio.create_task(add_tracked_friend(friend.guild_id, name, tag, puuid, shard))

        mmr_data = await get_mmr(shard=shard, puuid=puuid, name=name, tag=tag, force_refresh=force_refresh)

        if not mmr_data or "current_data" not in mmr_data:
            return {
                "name": name,
                "tag": tag,
                "display_name": f"{name}#{tag}",
                "rank_name": "Unrated / ไม่พบข้อมูล",
                "rr": 0,
                "elo": -1,
                "last_change": 0,
                "peak_rank": "-",
                "badge": "❔",
                "rank_icon": None,
            }

        current = mmr_data.get("current_data", {})
        highest = mmr_data.get("highest_rank", {})

        rank_name = current.get("currenttierpatched", "Unrated")
        rr = current.get("ranking_in_tier", 0)
        elo = current.get("elo", 0)
        last_change = current.get("mmr_change_to_last_game", 0)
        images = current.get("images", {})
        rank_icon = images.get("large") or images.get("small")

        peak_tier = highest.get("patched_tier", "")
        peak_season = highest.get("season", "").upper()
        peak_str = f"{peak_tier} ({peak_season})" if (peak_tier and peak_season) else (peak_tier or "-")

        return {
            "name": name,
            "tag": tag,
            "display_name": f"{name}#{tag}",
            "rank_name": rank_name,
            "rr": rr,
            "elo": elo if elo is not None else 0,
            "last_change": last_change,
            "peak_rank": peak_str,
            "badge": get_rank_badge(rank_name),
            "rank_icon": rank_icon,
        }
    except Exception as e:
        logger.exception("Error fetching rank summary for %s#%s: %s", name, tag, e)
        return {
            "name": name,
            "tag": tag,
            "display_name": f"{name}#{tag}",
            "rank_name": "Error",
            "rr": 0,
            "elo": -2,
            "last_change": 0,
            "peak_rank": "-",
            "badge": "⚠️",
            "rank_icon": None,
        }


def build_friend_rank_embed(
    player_summaries: list[dict[str, Any]],
    page: int,
    per_page: int = 5,
    total_pages: int = 1,
) -> discord.Embed:
    start_idx = page * per_page
    page_players = player_summaries[start_idx : start_idx + per_page]

    top_rank_name = player_summaries[0]["rank_name"] if player_summaries else "Unrated"
    color = get_rank_color(top_rank_name)

    embed = discord.Embed(
        title="จัดอันดับแรงค์ (Rank Leaderboard)",
        description=f"อันดับพวกบื้อ **{len(player_summaries)}** คน • เรียงตามแต้ม ELO สูงสุด",
        color=color,
    )

    logo_url = getattr(
        config,
        "VALORANT_LOGO_URL",
        "https://cdn.iconscout.com/icon/free/png-256/free-valorant-logo-icon-svg-download-png-2724649.png",
    )
    embed.set_thumbnail(url=logo_url)

    num_emojis = {
        1: "1️⃣",
        2: "2️⃣",
        3: "3️⃣",
        4: "4️⃣",
        5: "5️⃣",
        6: "6️⃣",
        7: "7️⃣",
        8: "8️⃣",
        9: "9️⃣",
        10: "🔟",
    }

    for idx, p in enumerate(page_players, start=start_idx + 1):
        rank_icon = num_emojis.get(idx, f"#{idx}")
        display_name = p.get("display_name", "Unknown")
        rank_name = p.get("rank_name", "Unrated")
        rr = p.get("rr", 0)
        elo = p.get("elo", 0)
        last_change = p.get("last_change", 0)
        peak = p.get("peak_rank", "-")

        if last_change > 0:
            change_str = f"+{last_change} RR"
        elif last_change < 0:
            change_str = f"{last_change} RR"
        else:
            change_str = "0 RR"

        field_name = f"{rank_icon} **{display_name}**"
        field_value = (
            f"**{rank_name}** (`{rr}/100 RR`)\n"
            f"แมตช์ล่าสุด: `{change_str}` • ELO: `{elo:,}`\n"
            f"Peak: **{peak}**"
        )
        embed.add_field(name=field_name, value=field_value, inline=False)

    now_ict = datetime.now(ICT).strftime("%H:%M:%S น.")
    embed.set_footer(
        text=f"หน้า {page + 1}/{total_pages} • อัพเดทล่าสุดเมื่อ {now_ict} (ICT) • กด 🔄 เพื่ออัพเดทแรงค์"
    )
    return embed




_REFRESH_COOLDOWNS: dict[int, float] = {}


def get_current_page_from_embed(embed: discord.Embed) -> int:
    """Parses current page number (0-indexed) from embed footer (e.g. 'หน้า 1/2')."""
    try:
        if embed.footer and embed.footer.text and "หน้า " in embed.footer.text:
            text = embed.footer.text
            page_part = text.split("หน้า ", 1)[1].split("/", 1)[0].strip()
            return max(0, int(page_part) - 1)
    except Exception:
        pass
    return 0


class FriendRankView(discord.ui.View):
    def __init__(
        self,
        guild_id: int = 0,
        player_summaries: list[dict[str, Any]] | None = None,
        page: int = 0,
        per_page: int = 5,
    ):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.player_summaries = player_summaries or []
        self.page = page
        self.per_page = per_page
        self.total_pages = max(1, (len(self.player_summaries) - 1) // per_page + 1) if self.player_summaries else 1
        self.update_buttons()

    def update_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        if self.total_pages > 1:
            self.next_btn.disabled = self.page >= self.total_pages - 1
        else:
            self.next_btn.disabled = True

    def get_embed(self) -> discord.Embed:
        return build_friend_rank_embed(
            self.player_summaries, self.page, self.per_page, self.total_pages
        )

    async def _ensure_data(self, interaction: discord.Interaction, force_refresh: bool = False):
        """Loads or updates player data if empty or requested."""
        guild_id = self.guild_id or interaction.guild_id or 0
        self.guild_id = guild_id

        if not self.player_summaries or force_refresh:
            friends = await get_tracked_friends(guild_id)
            if not friends:
                friends = await ensure_default_friends(guild_id, config.DEFAULT_FRIENDS)

            tasks = [fetch_player_rank_summary(f, force_refresh=force_refresh) for f in friends]
            summaries = await asyncio.gather(*tasks)
            self.player_summaries = sorted(summaries, key=lambda p: p["elo"], reverse=True)
            self.total_pages = max(1, (len(self.player_summaries) - 1) // self.per_page + 1)

    @discord.ui.button(
        label="◀️ ก่อนหน้า",
        style=discord.ButtonStyle.secondary,
        custom_id="checkrank:prev",
        row=0,
    )
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._ensure_data(interaction, force_refresh=False)
        current_page = get_current_page_from_embed(interaction.message.embeds[0]) if interaction.message.embeds else self.page
        if current_page > 0:
            self.page = current_page - 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()

    @discord.ui.button(
        label="🔄 อัพเดทแรงค์",
        style=discord.ButtonStyle.primary,
        custom_id="checkrank:refresh",
        row=0,
    )
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.guild_id or interaction.guild_id or 0
        now = time.time()
        last_refresh = _REFRESH_COOLDOWNS.get(guild_id, 0)
        if now - last_refresh < 10:
            remaining = int(10 - (now - last_refresh))
            await interaction.response.send_message(
                f"⏳ กรุณารอสักครู่ (อีก {remaining} วินาที) ก่อนกดอัพเดทอีกครั้ง",
                ephemeral=True,
            )
            return

        _REFRESH_COOLDOWNS[guild_id] = now
        await interaction.response.defer()

        # Parse current page before refresh so user stays on the same page
        if interaction.message.embeds:
            self.page = get_current_page_from_embed(interaction.message.embeds[0])

        await self._ensure_data(interaction, force_refresh=True)

        if self.page >= self.total_pages:
            self.page = max(0, self.total_pages - 1)

        self.update_buttons()
        await interaction.edit_original_response(embed=self.get_embed(), view=self)

    @discord.ui.button(
        label="ถัดไป ▶️",
        style=discord.ButtonStyle.secondary,
        custom_id="checkrank:next",
        row=0,
    )
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._ensure_data(interaction, force_refresh=False)
        current_page = get_current_page_from_embed(interaction.message.embeds[0]) if interaction.message.embeds else self.page
        if current_page < self.total_pages - 1:
            self.page = current_page + 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(), view=self)
        else:
            await interaction.response.defer()


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCog(bot))


