import logging
from datetime import datetime, timezone
import aiosqlite
from config import DB_PATH
from database.models import TrackedFriend, UserAccount

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Initializes the database schema compatible with EF Core."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS Users (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                DiscordUserId INTEGER NOT NULL,
                Puuid TEXT NOT NULL,
                EncryptedToken BLOB NOT NULL,
                Nonce BLOB NOT NULL,
                Tag BLOB NOT NULL,
                Shard TEXT NOT NULL,
                Name TEXT,
                Selected INTEGER NOT NULL DEFAULT 0,
                AccessToken TEXT NOT NULL,
                TokenExpires TEXT NOT NULL,
                CreatedAt TEXT NOT NULL,
                UpdatedAt TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS TrackedFriends (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                GuildId INTEGER NOT NULL,
                Name TEXT NOT NULL,
                Tag TEXT NOT NULL,
                Puuid TEXT,
                Region TEXT DEFAULT 'ap',
                CreatedAt TEXT NOT NULL,
                UNIQUE(GuildId, Name, Tag)
            );
            """
        )
        await db.commit()
    logger.info("Database initialized successfully at %s", DB_PATH)


async def get_user_by_puuid_and_discord_id(discord_user_id: int, puuid: str) -> UserAccount | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM Users WHERE DiscordUserId = ? AND Puuid = ? LIMIT 1",
            (discord_user_id, puuid),
        ) as cursor:
            row = await cursor.fetchone()
            return UserAccount.from_row(row) if row else None


async def get_selected_user(discord_user_id: int) -> UserAccount | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM Users WHERE DiscordUserId = ? AND Selected = 1 LIMIT 1",
            (discord_user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return UserAccount.from_row(row) if row else None


async def get_users_by_discord_id(discord_user_id: int) -> list[UserAccount]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM Users WHERE DiscordUserId = ? ORDER BY Id ASC",
            (discord_user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [UserAccount.from_row(r) for r in rows]


async def save_or_update_user(user: UserAccount) -> UserAccount:
    """
    Saves a new user or updates existing tokens if the account already exists.
    If the new account is set to selected, unselects other accounts for this Discord user.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = user.token_expires.isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        # Check if existing
        async with db.execute(
            "SELECT Id FROM Users WHERE DiscordUserId = ? AND Puuid = ? LIMIT 1",
            (user.discord_user_id, user.puuid),
        ) as cursor:
            existing = await cursor.fetchone()

        if user.selected:
            await db.execute(
                "UPDATE Users SET Selected = 0 WHERE DiscordUserId = ?",
                (user.discord_user_id,),
            )

        if existing:
            user_id = existing[0]
            await db.execute(
                """
                UPDATE Users SET
                    EncryptedToken = ?,
                    Nonce = ?,
                    Tag = ?,
                    Shard = ?,
                    Name = ?,
                    AccessToken = ?,
                    TokenExpires = ?,
                    Selected = ?,
                    UpdatedAt = ?
                WHERE Id = ?
                """,
                (
                    user.encrypted_token,
                    user.nonce,
                    user.tag,
                    user.shard,
                    user.name or "",
                    user.access_token,
                    expires_iso,
                    1 if user.selected else 0,
                    now_iso,
                    user_id,
                ),
            )
            user.id = user_id
        else:
            created_iso = user.created_at.isoformat()
            cursor = await db.execute(
                """
                INSERT INTO Users (
                    DiscordUserId, Puuid, EncryptedToken, Nonce, Tag,
                    Shard, Name, Selected, AccessToken, TokenExpires,
                    CreatedAt, UpdatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.discord_user_id,
                    user.puuid,
                    user.encrypted_token,
                    user.nonce,
                    user.tag,
                    user.shard,
                    user.name or "",
                    1 if user.selected else 0,
                    user.access_token,
                    expires_iso,
                    created_iso,
                    now_iso,
                ),
            )
            user.id = cursor.lastrowid

        await db.commit()
    return user


async def update_account_tokens(
    user_id: int,
    access_token: str,
    token_expires: datetime,
    encrypted_token: bytes,
    nonce: bytes,
    tag: bytes,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    expires_iso = token_expires.isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE Users SET
                AccessToken = ?,
                TokenExpires = ?,
                EncryptedToken = ?,
                Nonce = ?,
                Tag = ?,
                UpdatedAt = ?
            WHERE Id = ?
            """,
            (
                access_token,
                expires_iso,
                encrypted_token,
                nonce,
                tag,
                now_iso,
                user_id,
            ),
        )
        await db.commit()


async def update_account_shard(user_id: int, shard: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE Users SET Shard = ?, UpdatedAt = ? WHERE Id = ?",
            (shard, now_iso, user_id),
        )
        await db.commit()


async def set_selected_account(discord_user_id: int, account_id: int) -> bool:
    """Sets the account as selected and unselects others for this Discord user."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Check if the account belongs to this Discord user
        async with db.execute(
            "SELECT Id FROM Users WHERE DiscordUserId = ? AND Id = ?",
            (discord_user_id, account_id),
        ) as cursor:
            if not await cursor.fetchone():
                return False

        await db.execute(
            "UPDATE Users SET Selected = 0 WHERE DiscordUserId = ?",
            (discord_user_id,),
        )
        await db.execute(
            "UPDATE Users SET Selected = 1 WHERE Id = ?",
            (account_id,),
        )
        await db.commit()
        return True


async def delete_account(discord_user_id: int, account_id: int) -> UserAccount | None:
    """Deletes the account. If it was selected, selects another account if available."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM Users WHERE DiscordUserId = ? AND Id = ?",
            (discord_user_id, account_id),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            deleted_user = UserAccount.from_row(row)

        await db.execute("DELETE FROM Users WHERE Id = ?", (account_id,))

        if deleted_user.selected:
            # Pick next available account to be selected
            async with db.execute(
                "SELECT Id FROM Users WHERE DiscordUserId = ? LIMIT 1",
                (discord_user_id,),
            ) as cursor:
                remaining = await cursor.fetchone()
                if remaining:
                    await db.execute(
                        "UPDATE Users SET Selected = 1 WHERE Id = ?",
                        (remaining[0],),
                    )

        await db.commit()
        return deleted_user


async def get_user_by_name(name: str) -> UserAccount | None:
    """Finds an account in the database matching the given Riot Name or Name#Tag."""
    clean_name = name.split("#")[0].strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM Users WHERE LOWER(Name) = ? OR LOWER(Name) LIKE ? ORDER BY Selected DESC LIMIT 1",
            (clean_name, f"{clean_name}#%"),
        ) as cursor:
            row = await cursor.fetchone()
            return UserAccount.from_row(row) if row else None


async def get_all_users() -> list[UserAccount]:
    """Retrieves all accounts stored across all users in the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM Users ORDER BY Id ASC") as cursor:
            rows = await cursor.fetchall()
            return [UserAccount.from_row(r) for r in rows]


async def get_tracked_friends(guild_id: int) -> list[TrackedFriend]:
    """Retrieves all tracked friends for a specific guild (or global 0)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT * FROM TrackedFriends WHERE GuildId = ? ORDER BY Id ASC",
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [TrackedFriend.from_row(r) for r in rows]


async def add_tracked_friend(
    guild_id: int, name: str, tag: str, puuid: str | None = None, region: str = "ap"
) -> TrackedFriend:
    """Adds or updates a tracked friend in the database."""
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO TrackedFriends (GuildId, Name, Tag, Puuid, Region, CreatedAt)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(GuildId, Name, Tag) DO UPDATE SET
                Puuid = COALESCE(excluded.Puuid, TrackedFriends.Puuid),
                Region = excluded.Region;
            """,
            (guild_id, name, tag, puuid, region, now_iso),
        )
        await db.commit()

        async with db.execute(
            "SELECT * FROM TrackedFriends WHERE GuildId = ? AND LOWER(Name) = LOWER(?) AND LOWER(Tag) = LOWER(?) LIMIT 1",
            (guild_id, name, tag),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return TrackedFriend.from_row(row)
            raise ValueError(f"Failed to retrieve newly added friend: {name}#{tag}")


async def remove_tracked_friend(guild_id: int, name: str, tag: str) -> bool:
    """Removes a tracked friend from the database. Returns True if removed."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM TrackedFriends WHERE GuildId = ? AND LOWER(Name) = LOWER(?) AND LOWER(Tag) = LOWER(?)",
            (guild_id, name, tag),
        )
        await db.commit()
        return cursor.rowcount > 0


async def ensure_default_friends(guild_id: int, default_friends: list[str]) -> list[TrackedFriend]:
    """
    If no friends exist for this guild, seed them with the default friend list.
    """
    existing = await get_tracked_friends(guild_id)
    if existing:
        return existing

    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        for item in default_friends:
            if "#" not in item:
                continue
            parts = item.split("#", 1)
            name, tag = parts[0].strip(), parts[1].strip()
            await db.execute(
                """
                INSERT OR IGNORE INTO TrackedFriends (GuildId, Name, Tag, Puuid, Region, CreatedAt)
                VALUES (?, ?, ?, NULL, 'ap', ?)
                """,
                (guild_id, name, tag, now_iso),
            )
        await db.commit()
    return await get_tracked_friends(guild_id)


