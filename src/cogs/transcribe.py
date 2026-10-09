import asyncio
import datetime
import io
import os
import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal
from openai import AsyncOpenAI

from utils.integrations.video import extract_url_from_text, normalize_url
from utils.integrations import supabase_client as db
from utils.media.evidence import summarize_video
from utils.media.watch import detect_platform, watch_attachment, watch_url

# TLDR result cache keyed by Discord message ID — used for video conversation context.
# Written through to Supabase tldr_results so replies to a TLDR keep working after a
# restart; the DB copy keeps only what bot.py's _tldr_context uses. "transcript" holds
# the whole evidence report (speech, on-screen text, what happens), not just speech.
tldr_results: dict[int, dict] = {}
_TLDR_MAX_CACHE = 100
_TLDR_STORED_TRANSCRIPT_CHARS = 60_000  # matches what follow-ups can use (bot.TLDR_CONTEXT_CHARS)
_TLDR_RETENTION = datetime.timedelta(days=30)
_background_tasks = set()  # strong refs so fire-and-forget saves aren't GC'd mid-flight


def _cache_tldr_result(msg_id: int, result: dict) -> None:
    if len(tldr_results) >= _TLDR_MAX_CACHE:
        oldest = next(iter(tldr_results))
        del tldr_results[oldest]
    tldr_results[msg_id] = result


async def _save_tldr_result(msg_id: int, transcript: str, metadata: dict, summary: str) -> None:
    try:
        await db.upsert_tldr_result(msg_id, metadata.get("title") or "Unknown", summary,
                                    transcript[:_TLDR_STORED_TRANSCRIPT_CHARS], metadata.get("media_key"))
    except Exception as e:
        print(f"[tldr] couldn't save result {msg_id}: {type(e).__name__}: {e}")


def _store_tldr_result(msg_id: int, transcript: str, metadata: dict, summary: str) -> None:
    _cache_tldr_result(msg_id, {"transcript": transcript, "metadata": metadata, "summary": summary})
    task = asyncio.create_task(_save_tldr_result(msg_id, transcript, metadata, summary))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def get_tldr_result(msg_id: int) -> dict | None:
    if msg_id in tldr_results:
        return tldr_results[msg_id]
    try:
        row = await db.get_tldr_result(msg_id)
    except Exception as e:
        print(f"[tldr] couldn't load result {msg_id}: {type(e).__name__}: {e}")
        return None
    if not row:
        return None
    result = {"transcript": row["transcript"] or "", "summary": row["summary"] or "",
              "metadata": {"title": row["title"], "media_key": row.get("media_key")}}
    _cache_tldr_result(msg_id, result)
    return result


async def prune_stored_tldrs() -> None:
    try:
        await db.prune_tldr_results(datetime.datetime.now(datetime.timezone.utc) - _TLDR_RETENTION)
    except Exception as e:
        print(f"[tldr] couldn't prune stored results: {type(e).__name__}: {e}")


def _platform_color(platform: str) -> discord.Color:
    return {
        "YouTube":   discord.Color.from_rgb(255, 0, 0),
        "Twitter/X": discord.Color.from_rgb(29, 161, 242),
        "TikTok":    discord.Color.from_rgb(0, 0, 0),
        "Instagram": discord.Color.from_rgb(225, 48, 108),
        "Reddit":    discord.Color.from_rgb(255, 69, 0),
    }.get(platform, discord.Color.blurple())


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _build_tldr_embed(
    summary: str,
    metadata: dict,
    mode: str,
    platform: str,
    transcript: str,
    include_transcript: bool,
    src_label: str,
) -> tuple[discord.Embed, list[discord.File]]:
    title       = (metadata.get("title") or "Video Summary")[:200]
    dur_str     = _fmt_duration(metadata.get("duration", 0))
    icon        = "📋" if mode == "brief" else "📄"
    mode_label  = "Brief" if mode == "brief" else "Detailed"

    emb = discord.Embed(
        title=f"{icon} {title}",
        url=metadata.get("webpage_url"),
        description=summary[:4096],  # Discord's embed description limit
        color=_platform_color(platform),
    )
    if metadata.get("thumbnail"):
        emb.set_thumbnail(url=metadata["thumbnail"])
    emb.set_footer(text=f"{platform} • {dur_str} • {src_label} • {mode_label} summary")

    files = []
    if include_transcript:
        if not transcript.strip():
            emb.add_field(name="Full Transcript", value="*(no speech in this video)*", inline=False)
        elif len(transcript) > 800:
            txt_bytes = io.BytesIO(transcript.encode("utf-8"))
            files.append(discord.File(
                txt_bytes,
                filename=f"transcript_{platform.lower().replace('/', '-')}.txt",
            ))
            emb.add_field(name="Full Transcript", value="*(attached as .txt file)*", inline=False)
        else:
            emb.add_field(name="Full Transcript", value=f"```{transcript}```", inline=False)

    return emb, files


async def _find_recent_video_url(channel) -> str | None:
    """Scan last 30 messages in channel for a recognizable video URL."""
    try:
        async for msg in channel.history(limit=30):
            url = extract_url_from_text(msg.content or "")
            if url:
                return url
    except Exception:
        pass
    return None


async def _run_tldr(
    url: str,
    mode: str,
    include_transcript: bool,
    openai_client: AsyncOpenAI,
    on_step,   # async callable(str) for progress updates
) -> tuple[discord.Embed, list[discord.File], str, dict, str]:
    """
    Core TLDR pipeline shared by all invocation modes.
    Returns (embed, files, evidence report, metadata, summary).
    Raises ValueError for user-facing errors, Exception for unexpected failures.
    """
    watched = await watch_url(url, openai_client, on_step)
    return await _finish_tldr(watched, mode, include_transcript, openai_client, on_step)


async def _run_tldr_attachment(
    attachment: discord.Attachment,
    mode: str,
    include_transcript: bool,
    openai_client: AsyncOpenAI,
    on_step,
) -> tuple[discord.Embed, list[discord.File], str, dict, str]:
    """TLDR pipeline for Discord-uploaded video/audio files. Same return as _run_tldr."""
    watched = await watch_attachment(attachment, openai_client, on_step)
    return await _finish_tldr(watched, mode, include_transcript, openai_client, on_step)


async def _finish_tldr(watched, mode, include_transcript, openai_client, on_step):
    await on_step("Writing the summary...")
    summary = await summarize_video(watched.evidence, watched.metadata, mode, openai_client)
    emb, files = _build_tldr_embed(
        summary, watched.metadata, mode, watched.platform,
        watched.evidence.transcript(), include_transcript, watched.evidence.label(),
    )
    metadata = {**watched.metadata, "media_key": watched.key}
    return emb, files, watched.evidence.report(), metadata, summary


class Transcribe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="tldr",
        description="Watch and summarize a video from YouTube, TikTok, Twitter/X, Instagram, Reddit, or a file.",
    )
    @app_commands.describe(
        url="Link to the video — leave blank to use the most recent video link in this channel",
        attachment="Upload a video or audio file directly to summarize",
        mode="Summary length — brief bullet points (default) or detailed paragraphs",
        include_transcript="Also attach the full raw transcript alongside the summary",
    )
    async def tldr(
        self,
        interaction: discord.Interaction,
        url: str = None,
        attachment: discord.Attachment = None,
        mode: Literal["brief", "detailed"] = "brief",
        include_transcript: bool = False,
    ):
        await interaction.response.defer()
        progress = await interaction.followup.send("Working...", wait=True)

        try:
            openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=300.0)

            async def update(text: str):
                await progress.edit(content=text)

            if attachment is not None:
                emb, files, evidence, metadata, summary = await _run_tldr_attachment(
                    attachment, mode, include_transcript, openai_client, on_step=update
                )
            else:
                if url is None:
                    await progress.edit(content="Searching for recent video link...")
                    url = await _find_recent_video_url(interaction.channel)
                    if url is None:
                        await progress.edit(content="No recent video link found. Provide a URL or upload a file.")
                        return
                url = normalize_url(url)
                emb, files, evidence, metadata, summary = await _run_tldr(
                    url, mode, include_transcript, openai_client, on_step=update
                )

            # progress IS the original deferred response — edit it in-place to the final embed
            if files:
                await progress.edit(content=None, embed=emb, attachments=files)
            else:
                await progress.edit(content=None, embed=emb)
            _store_tldr_result(progress.id, evidence, metadata, summary)

        except ValueError as e:
            await progress.edit(content=f"Error: {e}")
        except Exception as e:
            print(f"[tldr] Unexpected error: {e}")
            await progress.edit(content="Something went wrong. The video may be unavailable, restricted, or from an unsupported platform.")


async def setup(bot):
    await bot.add_cog(Transcribe(bot))


# ── Mention handler (imported by bot.py and called from on_message) ────────────

async def handle_tldr_mention(message: discord.Message) -> None:
    """
    Handles `@abgluvr /tldr [-detailed] [-transcript]` in any of these forms:
      - Current message contains a video URL  (e.g. "@bot /tldr https://tiktok.com/...")
      - Current message has a video attachment (e.g. "@bot /tldr" + uploaded file)
      - Reply to a message with a video URL or embed
      - Reply to a message with a video attachment

    Flags (any order, case-insensitive):
      -detailed    → mode="detailed"  (default: "brief")
      -transcript  → include_transcript=True
    """
    content_lower      = (message.content or "").lower()
    mode               = "detailed" if "-detailed" in content_lower else "brief"
    include_transcript = "-transcript" in content_lower
    openai_client      = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=300.0)

    async def _send_url(url: str) -> None:
        url = normalize_url(url)
        progress = await message.channel.send(f"Downloading {detect_platform(url)} video...")
        try:
            async def update(text: str):
                await progress.edit(content=text)
            emb, files, evidence, metadata, summary = await _run_tldr(
                url, mode, include_transcript, openai_client, on_step=update
            )
            await progress.delete()
            sent = await message.channel.send(embed=emb, files=files)
            _store_tldr_result(sent.id, evidence, metadata, summary)
        except ValueError as e:
            await progress.edit(content=f"Error: {e}")
        except Exception as e:
            print(f"[tldr mention url] Unexpected error: {e}")
            await progress.edit(content="Something went wrong. The video may be unavailable or unsupported.")

    async def _send_attachment(att: discord.Attachment) -> None:
        progress = await message.channel.send("Processing attachment...")
        try:
            async def update(text: str):
                await progress.edit(content=text)
            emb, files, evidence, metadata, summary = await _run_tldr_attachment(
                att, mode, include_transcript, openai_client, on_step=update
            )
            await progress.delete()
            sent = await message.channel.send(embed=emb, files=files)
            _store_tldr_result(sent.id, evidence, metadata, summary)
        except ValueError as e:
            await progress.edit(content=f"Error: {e}")
        except Exception as e:
            print(f"[tldr mention attachment] Unexpected error: {e}")
            await progress.edit(content="Something went wrong processing the attachment.")

    # 1. URL in the current message (user typed the link alongside @bot /tldr)
    url = extract_url_from_text(message.content or "")
    if url:
        await _send_url(url)
        return

    # 2. Attachment on the current message (user uploaded a file alongside @bot /tldr)
    for att in message.attachments:
        ct = (att.content_type or "").lower()
        if ct.startswith("video/") or ct.startswith("audio/"):
            await _send_attachment(att)
            return

    # 3. Replied-to message — check URL then attachment
    if not message.reference:
        await message.reply(
            "Include a video URL, attach a file, or reply to a message containing a video link."
        )
        return

    try:
        ref_msg = await message.channel.fetch_message(message.reference.message_id)
    except (discord.NotFound, discord.HTTPException):
        await message.reply("Could not find the message you replied to.")
        return

    # URL from ref text first, then embed URLs (bot resends fixed links via embeds)
    url = extract_url_from_text(ref_msg.content or "")
    if not url:
        for embed in ref_msg.embeds:
            if embed.url:
                url = embed.url
                break

    if url:
        await _send_url(url)
        return

    # Attachment on the referenced message
    for att in ref_msg.attachments:
        ct = (att.content_type or "").lower()
        if ct.startswith("video/") or ct.startswith("audio/"):
            await _send_attachment(att)
            return

    await message.reply("No video link or attachment found in the message you replied to.")
