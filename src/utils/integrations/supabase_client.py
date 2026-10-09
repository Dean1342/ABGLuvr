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


def parse_timestamp(value) -> datetime.datetime:
    # Supabase timestamptz string -> aware UTC datetime.
    dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


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
# Round 3 added the /model reset + persona-switch cutoff:
#   alter table user_settings add column context_reset_at timestamptz;

async def get_all_user_settings() -> list[dict]:
    client = await get_client()
    # select * so restoring still works before the context_reset_at column exists
    result = await client.table("user_settings").select("*").execute()
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


# --- channel_summaries ---
# Rolling summary of each channel's older conversation (utils/conversation/memory.py).
# Table DDL (run once in Supabase):
#   create table channel_summaries (
#     channel_id       bigint primary key,
#     guild_id         bigint,
#     summary          text        not null,
#     up_to_message_id bigint      not null,
#     up_to_at         timestamptz not null,
#     updated_at       timestamptz default now()
#   );

async def get_channel_summary(channel_id: int) -> dict | None:
    client = await get_client()
    result = await client.table("channel_summaries").select("*").eq("channel_id", channel_id).limit(1).execute()
    return result.data[0] if result.data else None


async def upsert_channel_summary(data: dict) -> None:
    client = await get_client()
    await client.table("channel_summaries").upsert(
        {**data, "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()},
        on_conflict="channel_id"
    ).execute()


# --- remembered_facts ---
# Facts people explicitly asked the bot to remember (utils/conversation/memory.py).
# scope: 'user' (about everyone in subject_ids), 'channel' (only in channel_id) or 'server'.
# Table DDL (run once in Supabase):
#   create table remembered_facts (
#     id                bigint generated always as identity primary key,
#     guild_id          bigint not null,
#     scope             text   not null check (scope in ('user', 'channel', 'server')),
#     channel_id        bigint,
#     subject_ids       bigint[],
#     fact              text   not null,
#     added_by          bigint,
#     source_message_id bigint,
#     created_at        timestamptz default now()
#   );
#   create index remembered_facts_guild on remembered_facts (guild_id);
# Before multi-person facts, the table had `subject_id bigint`; migrate with:
#   alter table remembered_facts add column subject_ids bigint[];
#   update remembered_facts set subject_ids = array[subject_id] where subject_id is not null;
#   alter table remembered_facts drop column subject_id;

async def get_facts(guild_id: int) -> list[dict]:
    client = await get_client()
    result = await client.table("remembered_facts").select("*").eq("guild_id", guild_id).order("id").execute()
    return result.data or []


async def insert_fact(data: dict) -> dict | None:
    client = await get_client()
    result = await client.table("remembered_facts").insert(data).execute()
    return result.data[0] if result.data else None


async def delete_facts(guild_id: int, fact_ids: list[int]) -> list[int]:
    client = await get_client()
    result = (
        await client.table("remembered_facts")
        .delete()
        .eq("guild_id", guild_id)
        .in_("id", fact_ids)
        .execute()
    )
    return [row["id"] for row in result.data or []]


# --- tldr_results ---
# TLDR summaries and evidence reports keyed by the embed's message id, so replies to a TLDR
# keep their video context across restarts (cogs/transcribe.py). media_key points at the
# video's media_analyses row (see below). Pruned after 30 days.
# Table DDL (run once in Supabase):
#   create table tldr_results (
#     message_id bigint primary key,
#     title      text,
#     summary    text,
#     transcript text,
#     created_at timestamptz default now()
#   );

async def upsert_tldr_result(message_id: int, title: str, summary: str, transcript: str,
                             media_key: str | None = None) -> None:
    client = await get_client()
    row = {"message_id": message_id, "title": title, "summary": summary, "transcript": transcript}
    try:
        await client.table("tldr_results").upsert({**row, "media_key": media_key}, on_conflict="message_id").execute()
    except Exception as e:
        # media_key (Phase 3) may not exist yet; the TLDR itself still has to be saved.
        print(f"[tldr] saving without media_key ({type(e).__name__}); run the media_analyses SQL")
        await client.table("tldr_results").upsert(row, on_conflict="message_id").execute()


async def get_tldr_result(message_id: int) -> dict | None:
    client = await get_client()
    result = await client.table("tldr_results").select("*").eq("message_id", message_id).limit(1).execute()
    return result.data[0] if result.data else None


async def prune_tldr_results(older_than: datetime.datetime) -> None:
    client = await get_client()
    await client.table("tldr_results").delete().lt("created_at", older_than.isoformat()).execute()


# --- media_analyses ---
# What the bot learned from watching a video (utils/media/store.py): the watchers' reports
# and the post's metadata, found by any id the video is known by (e.g. instagram:DeObnKXMGkV,
# tiktok:7420..., tiktok-short:ZPLjsNRF9, discord:<attachment id>), so the same video isn't
# downloaded and watched again for 30 days. No video or frames are stored.
# Table DDL (run once in Supabase):
#   create table media_analyses (
#     id         bigint generated always as identity primary key,
#     keys       text[]      not null,
#     version    int         not null,
#     metadata   jsonb,
#     evidence   jsonb       not null,
#     created_at timestamptz default now()
#   );
#   create index media_analyses_keys on media_analyses using gin (keys);
#   alter table tldr_results add column media_key text;

async def find_media_analysis(keys: list[str], version: int, since: datetime.datetime) -> dict | None:
    client = await get_client()
    result = (
        await client.table("media_analyses").select("*")
        .overlaps("keys", keys)
        .eq("version", version)
        .gt("created_at", since.isoformat())
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


async def insert_media_analysis(data: dict) -> dict | None:
    client = await get_client()
    result = await client.table("media_analyses").insert(data).execute()
    return result.data[0] if result.data else None


async def set_media_keys(row_id: int, keys: list[str]) -> None:
    client = await get_client()
    await client.table("media_analyses").update({"keys": keys}).eq("id", row_id).execute()


async def prune_media_analyses(older_than: datetime.datetime) -> None:
    client = await get_client()
    await client.table("media_analyses").delete().lt("created_at", older_than.isoformat()).execute()
