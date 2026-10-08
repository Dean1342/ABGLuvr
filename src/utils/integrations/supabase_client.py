import datetime
import os
from supabase import AsyncClient, acreate_client

_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables")
        _client = await acreate_client(url, key)
    return _client


# --- car_profiles ---

async def get_profile(user_id: int) -> dict | None:
    client = await get_client()
    try:
        result = await client.table("car_profiles").select("*").eq("user_id", user_id).single().execute()
        return result.data
    except Exception:
        return None


async def upsert_profile(user_id: int, guild_id: int, data: dict) -> None:
    client = await get_client()
    await client.table("car_profiles").upsert(
        {"user_id": user_id, "guild_id": guild_id, **data}
    ).execute()


# --- build_mods ---

async def get_mods(user_id: int) -> list[dict]:
    client = await get_client()
    result = (
        await client.table("build_mods")
        .select("*")
        .eq("user_id", user_id)
        .order("category")
        .order("name")
        .execute()
    )
    return result.data or []


async def upsert_mod(user_id: int, data: dict) -> None:
    client = await get_client()
    await client.table("build_mods").upsert(
        {"user_id": user_id, **data},
        on_conflict="user_id,name"
    ).execute()


async def delete_all_mods(user_id: int) -> None:
    client = await get_client()
    await client.table("build_mods").delete().eq("user_id", user_id).execute()


async def delete_mod(user_id: int, name: str) -> bool:
    client = await get_client()
    result = (
        await client.table("build_mods")
        .delete()
        .eq("user_id", user_id)
        .ilike("name", name)
        .execute()
    )
    return bool(result.data)


_ALLOWED_MOD_FIELDS = {'name', 'category', 'cost', 'paid', 'status', 'link', 'install_date', 'purchase_date', 'notes'}

async def bulk_upsert_mods(user_id: int, mods: list[dict]) -> None:
    client = await get_client()
    # Deduplicate by name — PostgreSQL can't update the same row twice in one upsert statement
    seen: dict[str, dict] = {}
    for m in mods:
        name = m.get('name')
        if name:
            seen[name] = m
    rows = [
        {"user_id": user_id, **{k: v for k, v in m.items() if k in _ALLOWED_MOD_FIELDS}}
        for m in seen.values()
    ]
    if not rows:
        return
    await client.table("build_mods").upsert(rows, on_conflict="user_id,name").execute()


# --- build_labor ---

async def get_labor(user_id: int) -> list[dict]:
    client = await get_client()
    result = (
        await client.table("build_labor")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return result.data or []


async def insert_labor(user_id: int, data: dict) -> None:
    client = await get_client()
    await client.table("build_labor").insert({"user_id": user_id, **data}).execute()


async def delete_labor(user_id: int, labor_id: str) -> bool:
    client = await get_client()
    result = (
        await client.table("build_labor")
        .delete()
        .eq("id", labor_id)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(result.data)


# --- scheduled_reminders ---
# Durable storage for scheduled ping/reminder actions so they survive bot restarts.
# Table DDL (run once in Supabase):
#   create table scheduled_reminders (
#     id uuid primary key default gen_random_uuid(),
#     channel_id  bigint      not null,
#     target_id   bigint      not null,
#     requester_id bigint     not null,
#     guild_id    bigint,
#     message     text,
#     count       int         not null default 1,
#     fire_at     timestamptz not null,
#     created_at  timestamptz default now()
#   );
# If you already created the table without `count`, run:
#   alter table scheduled_reminders add column count int not null default 1;

async def insert_reminder(data: dict) -> str | None:
    client = await get_client()
    result = await client.table("scheduled_reminders").insert(data).execute()
    if result.data:
        return result.data[0].get("id")
    return None


async def get_pending_reminders() -> list[dict]:
    client = await get_client()
    result = (
        await client.table("scheduled_reminders")
        .select("*")
        .order("fire_at")
        .execute()
    )
    return result.data or []


async def delete_reminder(reminder_id: str) -> None:
    client = await get_client()
    await client.table("scheduled_reminders").delete().eq("id", reminder_id).execute()


# --- user_settings ---
# Per-user, per-channel /persona and /model selections, so they survive restarts.
# Table DDL (run once in Supabase):
#   create table user_settings (
#     user_id    bigint      not null,
#     channel_id bigint      not null,
#     persona    text,
#     model      text,
#     updated_at timestamptz default now(),
#     primary key (user_id, channel_id)
#   );

async def get_all_user_settings() -> list[dict]:
    client = await get_client()
    result = await client.table("user_settings").select("user_id,channel_id,persona,model").execute()
    return result.data or []


async def upsert_user_setting(user_id: int, channel_id: int, **fields) -> None:
    client = await get_client()
    await client.table("user_settings").upsert(
        {
            "user_id": user_id,
            "channel_id": channel_id,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            **fields,
        },
        on_conflict="user_id,channel_id"
    ).execute()


# --- ai_logs ---
# Optional per-turn agent logs (enabled with AI_LOG_TO_DB=1; Heroku only keeps ~1500 log lines).
# Table DDL (run once in Supabase):
#   create table ai_logs (
#     id         bigint generated always as identity primary key,
#     created_at timestamptz default now(),
#     request_id text,
#     data       jsonb not null
#   );

async def insert_ai_log(log: dict) -> None:
    client = await get_client()
    await client.table("ai_logs").insert({"request_id": log.get("request_id"), "data": log}).execute()
