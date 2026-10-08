# Durable /persona and /model selections (and the /model reset / persona-switch context
# cutoff), backed by the Supabase user_settings table.
# The in-memory dicts in context.py stay the source of truth at runtime; this module
# loads them on startup and writes changes through. DB failures are logged and never
# break a command.
from utils.conversation.context import user_personas, user_models, MODELS
from utils.conversation.channel_context import restore_reset
from utils.ai.prompts import enabled_personas
from utils.integrations import supabase_client as db

_settings_restored = False  # guard: on_ready can fire on every reconnect


async def restore_user_settings():
    global _settings_restored
    if _settings_restored:
        return
    _settings_restored = True
    try:
        rows = await db.get_all_user_settings()
    except Exception as e:
        print(f"[settings] could not load user settings: {type(e).__name__}: {e}")
        return

    personas = set(enabled_personas())
    restored = 0
    for r in rows:
        key = (int(r["user_id"]), int(r["channel_id"]))
        # Deprecated/unknown values are kept out; bot.py falls back to the defaults.
        if r.get("persona") in personas:
            user_personas[key] = r["persona"]
        if r.get("model") in MODELS:
            user_models[key] = r["model"]
        if r.get("context_reset_at"):
            restore_reset(*key, db.parse_timestamp(r["context_reset_at"]))
        restored += 1
    if restored:
        print(f"[settings] restored settings for {restored} user/channel pair(s)")


async def save_user_setting(user_id, channel_id, **fields):
    try:
        await db.upsert_user_setting(user_id, channel_id, **fields)
    except Exception as e:
        print(f"[settings] failed to save {fields} for {user_id}/{channel_id}: {type(e).__name__}: {e}")


async def save_context_reset(user_id, channel_id, when):
    # Separate write so a missing context_reset_at column can't break persona/model saves.
    await save_user_setting(user_id, channel_id, context_reset_at=when.isoformat())
