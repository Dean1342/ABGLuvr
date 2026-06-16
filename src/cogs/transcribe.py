import io
import os
import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal
from openai import AsyncOpenAI

from utils.integrations.video import (
    download_audio, download_video, transcribe_audio, summarize_transcript,
    extract_frames, extract_url_from_text, normalize_url,
)

# Platforms where we download full video and extract frames for visual context
_SHORT_FORM_PLATFORMS = {"TikTok", "Instagram", "Twitter/X"}
_SHORT_FORM_MAX_DURATION = 180  # seconds


def _detect_platform(url: str) -> str:
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:    return "YouTube"
    if "twitter.com" in u or "x.com" in u:       return "Twitter/X"
    if "tiktok.com" in u:                         return "TikTok"
    if "instagram.com" in u:                      return "Instagram"
    if "reddit.com" in u or "redd.it" in u:      return "Reddit"
    return "Video"


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
    used_vision: bool,
) -> tuple[discord.Embed, list[discord.File]]:
    title       = (metadata.get("title") or "Video Summary")[:200]
    dur_str     = _fmt_duration(metadata.get("duration", 0))
    icon        = "📋" if mode == "brief" else "📄"
    mode_label  = "Brief" if mode == "brief" else "Detailed"
    src_label   = "Whisper + Vision" if used_vision else "Whisper"

    emb = discord.Embed(
        title=f"{icon} {title}",
        url=metadata.get("webpage_url"),
        description=summary,
        color=_platform_color(platform),
    )
    if metadata.get("thumbnail"):
        emb.set_thumbnail(url=metadata["thumbnail"])
    emb.set_footer(text=f"{platform} • {dur_str} • Transcribed with {src_label} • {mode_label} summary")

    files = []
    if include_transcript:
        if len(transcript) > 800:
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
) -> tuple[discord.Embed, list[discord.File]]:
    """
    Core TLDR pipeline shared by all three invocation modes.
    Calls on_step(text) to push progress updates to the caller.
    Raises ValueError for user-facing errors, Exception for unexpected failures.
    """
    platform   = _detect_platform(url)
    media_path = None
    frames: list[str] = []
    used_vision = False

    try:
        if platform in _SHORT_FORM_PLATFORMS:
            await on_step(f"Downloading {platform} video...")
            media_path, metadata = await download_video(url)
            duration = metadata.get("duration", 0) or 0
            if duration <= _SHORT_FORM_MAX_DURATION:
                frames = extract_frames(media_path, duration)
                used_vision = bool(frames)
        else:
            await on_step(f"Downloading {platform} audio...")
            media_path, metadata = await download_audio(url)

        await on_step("Transcribing...")
        transcript = await transcribe_audio(media_path, openai_client)

        if not transcript:
            raise ValueError("No speech detected in this video.")

        await on_step("Generating summary...")
        summary = await summarize_transcript(
            transcript, metadata, mode, openai_client,
            frames=frames or None,
        )

        return _build_tldr_embed(
            summary, metadata, mode, platform,
            transcript, include_transcript, used_vision,
        )

    finally:
        if media_path and os.path.exists(media_path):
            os.remove(media_path)


class Transcribe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="tldr",
        description="Transcribe and summarize a video from YouTube, TikTok, Twitter/X, Instagram, Reddit, and more.",
    )
    @app_commands.describe(
        url="Link to the video — leave blank to use the most recent video link in this channel",
        mode="Summary length — brief bullet points (default) or detailed paragraphs",
        include_transcript="Also attach the full raw transcript alongside the summary",
    )
    async def tldr(
        self,
        interaction: discord.Interaction,
        url: str = None,
        mode: Literal["brief", "detailed"] = "brief",
        include_transcript: bool = False,
    ):
        await interaction.response.defer()
        progress = await interaction.followup.send("Working...", wait=True)

        try:
            openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=120.0)

            if url is None:
                await progress.edit(content="Searching for recent video link...")
                url = await _find_recent_video_url(interaction.channel)
                if url is None:
                    await progress.edit(content="No recent video link found in this channel. Provide a URL directly.")
                    return

            url = normalize_url(url)

            async def update(text: str):
                await progress.edit(content=text)

            emb, files = await _run_tldr(url, mode, include_transcript, openai_client, on_step=update)
            # progress IS the original deferred response — edit it in-place to the final embed
            if files:
                await progress.edit(content=None, embed=emb, attachments=files)
            else:
                await progress.edit(content=None, embed=emb)

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
    Handles `@abgluvr /tldr [-detailed] [-transcript]` sent as a reply to a video message.
    Called from on_message before the LLM pipeline.

    Flags (any order, case-insensitive):
      -detailed    → mode="detailed"  (default: "brief")
      -transcript  → include_transcript=True
    """
    if not message.reference:
        await message.reply("Reply to a message containing a video link to use this.")
        return

    try:
        ref_msg = await message.channel.fetch_message(message.reference.message_id)
    except (discord.NotFound, discord.HTTPException):
        await message.reply("Could not find the message you replied to.")
        return

    # Try plain text first, then embed URLs (bot's own fixed-link messages use embeds)
    url = extract_url_from_text(ref_msg.content or "")
    if not url:
        for embed in ref_msg.embeds:
            if embed.url:
                url = embed.url
                break
    if not url:
        await message.reply("No video link found in the message you replied to.")
        return

    url               = normalize_url(url)
    content_lower     = message.content.lower()
    mode              = "detailed" if "-detailed" in content_lower else "brief"
    include_transcript = "-transcript" in content_lower

    progress = await message.channel.send(f"Downloading {_detect_platform(url)} video...")

    try:
        openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=120.0)

        async def update(text: str):
            await progress.edit(content=text)

        emb, files = await _run_tldr(url, mode, include_transcript, openai_client, on_step=update)
        await progress.delete()
        await message.channel.send(embed=emb, files=files)

    except ValueError as e:
        await progress.edit(content=f"Error: {e}")
    except Exception as e:
        print(f"[tldr mention] Unexpected error: {e}")
        await progress.edit(content="Something went wrong. The video may be unavailable or unsupported.")
