from dataclasses import dataclass
from datetime import datetime, timezone


def parse_datetime(val: str | datetime) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        # Handle ISO strings
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


@dataclass
class UserAccount:
    id: int | None
    discord_user_id: int
    puuid: str
    encrypted_token: bytes
    nonce: bytes
    tag: bytes
    shard: str
    name: str | None
    selected: bool
    access_token: str
    token_expires: datetime
    created_at: datetime = datetime.now(timezone.utc)
    updated_at: datetime = datetime.now(timezone.utc)

    @classmethod
    def from_row(cls, row: tuple) -> "UserAccount":
        (
            user_id,
            discord_user_id,
            puuid,
            encrypted_token,
            nonce,
            tag,
            shard,
            name,
            selected,
            access_token,
            token_expires,
            created_at,
            updated_at,
        ) = row

        return cls(
            id=user_id,
            discord_user_id=discord_user_id,
            puuid=puuid,
            encrypted_token=encrypted_token,
            nonce=nonce,
            tag=tag,
            shard=shard,
            name=name,
            selected=bool(selected),
            access_token=access_token,
            token_expires=parse_datetime(token_expires),
            created_at=parse_datetime(created_at),
            updated_at=parse_datetime(updated_at),
        )


@dataclass
class TrackedFriend:
    id: int | None
    guild_id: int
    name: str
    tag: str
    puuid: str | None
    region: str = "ap"
    created_at: datetime = datetime.now(timezone.utc)

    @property
    def riot_id(self) -> str:
        return f"{self.name}#{self.tag}"

    @classmethod
    def from_row(cls, row: tuple) -> "TrackedFriend":
        (f_id, guild_id, name, tag, puuid, region, created_at) = row
        return cls(
            id=f_id,
            guild_id=guild_id,
            name=name,
            tag=tag,
            puuid=puuid,
            region=region or "ap",
            created_at=parse_datetime(created_at),
        )

