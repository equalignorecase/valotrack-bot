# ValoTracker (Python Version)

A feature-rich Discord bot for Valorant: daily shop rotations, live in-game match inspector with graphical scoreboards, friends rank leaderboard, match history, and regional leaderboards.

![Daily shop example](./docs/shop-example.png)

## Features

### 🛒 Daily Shop & Account Management
- `/login [name]` - Authenticate via official Riot OAuth2 login flow.
- `/shop` - View current daily shop rotation for your active account with rendered skin card images, rarities, and VP prices.
- `/view-accounts` - List all Riot accounts linked to your Discord profile with active status.
- `/select-account <id>` - Switch your active/selected account for shop and live tracking.
- `/delete-account <id>` - Unlink and delete a Riot account and its encrypted tokens from the database.
- `/account-list` - *(Bot Owner Only)* View all registered accounts and token validity across the bot.

### 👥 Friends Rank Leaderboard
- `/checkrank` - Check ranks of tracked friends in the server. Displays 5 players per page sorted by ELO/Rank (Leaderboard style) with interactive pagination (`◀️`, `▶️`) and live `🔄 อัพเดทแรงค์` refresh button with spam cooldown.
- `/addfriend <player>` - Add a friend (`Name#Tag`) to the server rank tracking list. Automatically validates account and resolves PUUID & region.
- `/removefriend <player>` - Remove a friend from the tracking list (with real-time interactive autocomplete).

### 🎮 Live Match & Stats
- `/live [player]` - Inspect live match (In-Game or Agent Select) in real-time with high-res graphical scoreboard, party stack indicators, current rank, peak rank, and player highlight.
- `/rank <player>` - View current rank tier, RR points, last match +/- RR, and peak rank (`Name#Tag`).
- `/lastmatch <player>` - Generate a full high-resolution match Scoreboard image (both teams, KDA, ACS, HS%, rank badges).
- `/match <player>` - View recent matches with an interactive dropdown menu to inspect detailed graphical scoreboards for each game.
- `/leaderboard [region] [player]` - Browse official Radiant leaderboards across regions (AP, NA, EU, KR, BR, LATAM) with pagination or search for any player.

### ⚙️ General
- `/help` - Interactive user guide and full command list.
- `/ping` - Check bot WebSocket latency, API status, and system uptime.

---

## How It Works

Riot does not have a public API for private data like personal daily shops or live pre-game lobbies. ValoTracker integrates with community-discovered Riot Client endpoints alongside the [HenrikDev Valorant API](https://henrikdev.xyz) and [valorant-api.com](https://valorant-api.com):

1. **Authentication Flow**:
   - `/login` generates a secure Riot OAuth2 authorization link.
   - User logs in through Riot's login page, which redirects to `localhost` with authorization tokens in the URL.
   - User pastes the redirect URL into the bot modal.
   - The bot exchanges the auth code for access & refresh tokens at `auth.riotgames.com/token`.
   - Access tokens fetch entitlement info, PUUID, and region before being securely stored.
   - Encrypted refresh tokens automatically renew expired access tokens in the background.

2. **Security**:
   - User passwords are **never** requested or stored.
   - All Riot refresh tokens are encrypted at rest using **AES-256-GCM** (ciphertext, nonce, and auth tag stored separately).
   - Access tokens are short-lived (1 hour).

---

## Setup & Installation

### Requirements
- Python 3.10+
- Discord Bot Application & Token ([Discord Developer Portal](https://discord.com/developers/applications))
- HenrikDev Valorant API Key ([henrikdev.xyz](https://henrikdev.xyz))

### Configuration

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Generate a 32-byte AES encryption key:
   ```bash
   python3 -c "import os, base64; print('AES_KEY=' + base64.b64encode(os.urandom(32)).decode())"
   ```

3. Configure your `.env` variables:
   ```env
   DISCORD_TOKEN=your_bot_token_here
   GUILD_ID=your_test_guild_id_here       # Optional: for instant command sync
   AES_KEY=your_base64_encoded_key_here
   HENRIK_API_KEY=your_henrik_api_key_here
   DB_PATH=ValoTracker.db
   ```

### Running the Bot

**Windows:**
```cmd
install.bat
run.bat
```
*(or manually: `pip install -r requirements.txt` then `python bot.py`)*

**macOS / Linux:**
```bash
pip install -r requirements.txt
python3 bot.py
```

---

## Disclaimer

This is an unofficial fan project and is not affiliated with, endorsed by, or sponsored by Riot Games, Inc. Valorant and Riot Games are trademarks or registered trademarks of Riot Games, Inc.

