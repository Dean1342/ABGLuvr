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
