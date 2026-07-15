import io
import os
import discord
from discord import app_commands
from discord.ext import commands
from typing import Literal
from openai import AsyncOpenAI

from utils.integrations.video import (
    download_audio, download_video, download_instagram_video, download_attachment,
    transcribe_audio, summarize_transcript,
    extract_frames, extract_url_from_text, normalize_url, extract_audio_track,
)

# Platforms where we download full video and extract frames for visual context
_SHORT_FORM_PLATFORMS = {"TikTok", "Instagram", "Twitter/X"}
_SHORT_FORM_MAX_DURATION = 180  # seconds
_WHISPER_SIZE_LIMIT = 25 * 1024 * 1024  # 25 MB — Whisper API hard limit

# Containers Whisper accepts directly. Anything else (.mov, .mkv, .avi, raw .aac, .wma…)
# must have its audio track extracted/transcoded to AAC/mp4 first via PyAV.
_WHISPER_SUPPORTED_EXTS = {"flac", "m4a", "mp3", "mp4", "mpeg", "mpga", "oga", "ogg", "wav", "webm"}

# TLDR result cache keyed by Discord message ID — used for video conversation context
tldr_results: dict[int, dict] = {}
_TLDR_MAX_CACHE = 100


def _store_tldr_result(msg_id: int, transcript: str, metadata: dict, summary: str) -> None:
    if len(tldr_results) >= _TLDR_MAX_CACHE:
        oldest = next(iter(tldr_results))
        del tldr_results[oldest]
    tldr_results[msg_id] = {"transcript": transcript, "metadata": metadata, "summary": summary}


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
) -> tuple[discord.Embed, list[discord.File], str, dict, str]:
    """
    Core TLDR pipeline shared by all invocation modes.
    Returns (embed, files, transcript, metadata, summary).
    Raises ValueError for user-facing errors, Exception for unexpected failures.
    """
    platform   = _detect_platform(url)
    media_path = None
    frames: list[str] = []
    used_vision = False

    try:
        if platform == "YouTube":
            raise ValueError("YouTube isn't supported — try TikTok, Instagram, Twitter/X, or Reddit instead.")

        elif platform in _SHORT_FORM_PLATFORMS:
            await on_step(f"Downloading {platform} video...")
            transcript = None
            metadata = {}

            # Try video download first (enables frame extraction for visual context)
            try:
                if platform == "Instagram":
                    media_path, metadata = await download_instagram_video(url)
                else:
                    media_path, metadata = await download_video(url)
            except ValueError as dl_err:
                print(f"[tldr] video download failed ({dl_err}), falling back to audio-only")
                media_path = None

            if media_path:
                duration = metadata.get("duration", 0) or 0
                video_size = os.path.getsize(media_path)
                # Extract frames regardless of file size — they're sent to vision, not Whisper
                if duration <= _SHORT_FORM_MAX_DURATION:
                    frames = extract_frames(media_path, duration)
                    used_vision = bool(frames)
                # Only send to Whisper if within the 25 MB API limit
                if video_size <= _WHISPER_SIZE_LIMIT:
                    await on_step("Transcribing...")
                    try:
                        transcript = await transcribe_audio(media_path, openai_client)
                    except Exception as whisper_err:
                        print(f"[tldr] video transcription failed ({whisper_err}), falling back to audio-only")
                        transcript = None
                else:
                    print(f"[tldr] video file {video_size // (1024 * 1024)} MB exceeds Whisper limit, falling back to audio-only")

            if not transcript:
                audio_path = None
                try:
                    if media_path:
                        # Video already on disk — demux the audio track in-process.
                        # PyAV copies the AAC stream without re-encoding: ~2 MB from a 27 MB mp4.
                        await on_step("Extracting audio track...")
                        try:
                            audio_path = await extract_audio_track(media_path)
                        except Exception as extraction_err:
                            print(f"[tldr] audio extraction failed ({extraction_err}), downloading instead")
                            audio_path = None

                    if audio_path is None:
                        step_msg = "Downloading audio..." if not media_path else "Retrying with audio download..."
                        await on_step(step_msg)
                        audio_path, audio_meta = await download_audio(url)
                        if not metadata:
                            metadata = audio_meta

                    await on_step("Transcribing...")
                    transcript = await transcribe_audio(audio_path, openai_client)
                except Exception as e:
                    raise ValueError(f"Could not transcribe video: {e}") from None
                finally:
                    if audio_path and os.path.exists(audio_path):
                        os.remove(audio_path)

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

        emb, files = _build_tldr_embed(
            summary, metadata, mode, platform,
            transcript, include_transcript, used_vision,
        )
        return emb, files, transcript, metadata, summary

    finally:
        if media_path and os.path.exists(media_path):
            os.remove(media_path)


async def _run_tldr_attachment(
    attachment: discord.Attachment,
    mode: str,
    include_transcript: bool,
    openai_client: AsyncOpenAI,
    on_step,
) -> tuple[discord.Embed, list[discord.File], str, dict, str]:
    """
    TLDR pipeline for Discord-uploaded video/audio files.
    Returns (embed, files, transcript, metadata, summary).
    """
    ct = (attachment.content_type or "").lower()
    is_video = ct.startswith("video/")
    is_audio = ct.startswith("audio/")
    if not (is_video or is_audio):
        raise ValueError("Attachment must be a video or audio file.")

    # Video files get their audio stripped to a tiny track before Whisper, so we can
    # accept larger uploads. Audio goes to Whisper (after optional transcode) — cap near its limit.
    max_size = 100 * 1024 * 1024 if is_video else 25 * 1024 * 1024
    if attachment.size > max_size:
        raise ValueError(
            f"File too large — max {max_size // (1024 * 1024)} MB "
            f"(this file is {attachment.size // (1024 * 1024)} MB)."
        )

    ext = attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else ""

    await on_step("Downloading attachment...")
    path = None
    audio_path = None
    try:
        path = await download_attachment(attachment.url, attachment.filename)
        metadata: dict = {
            "title": attachment.filename,
            "duration": None,
            "thumbnail": None,
            "uploader": "",
            "webpage_url": attachment.url,
        }

        frames: list[str] = []
        if is_video:
            try:
                import cv2
                cap = cv2.VideoCapture(path)
                fps = cap.get(cv2.CAP_PROP_FPS) or 25
                total = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                cap.release()
                duration = int(total / fps) if fps else 0
                metadata["duration"] = duration
                if duration <= _SHORT_FORM_MAX_DURATION:
                    frames = extract_frames(path, duration)
            except ImportError:
                pass

        # Convert to a Whisper-supported format when needed. Video is always stripped to
        # its audio track (handles .mov/.mkv/.avi and shrinks the upload); audio is only
        # transcoded when its container isn't one Whisper accepts.
        transcribe_target = path
        if is_video or (is_audio and ext not in _WHISPER_SUPPORTED_EXTS):
            await on_step("Extracting audio track...")
            try:
                audio_path = await extract_audio_track(path)
                transcribe_target = audio_path
            except Exception as extraction_err:
                print(f"[tldr attachment] audio extraction failed ({extraction_err})")
                if ext not in _WHISPER_SUPPORTED_EXTS:
                    raise ValueError(
                        f"Couldn't process this .{ext or 'file'} — its audio track could not be extracted."
                    ) from None
                transcribe_target = path  # supported container: send it as-is

        await on_step("Transcribing...")
        transcript = await transcribe_audio(transcribe_target, openai_client)
        if not transcript:
            raise ValueError("No speech detected in this file.")

        await on_step("Generating summary...")
        summary = await summarize_transcript(
            transcript, metadata, mode, openai_client,
            frames=frames or None,
        )

        platform = "Video" if is_video else "Audio"
        emb, files = _build_tldr_embed(
            summary, metadata, mode, platform,
            transcript, include_transcript, bool(frames),
        )
        return emb, files, transcript, metadata, summary

    finally:
        if path and os.path.exists(path):
            os.remove(path)
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)


class Transcribe(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="tldr",
        description="Transcribe and summarize a video from TikTok, Twitter/X, Instagram, Reddit, or an uploaded file.",
    )
    @app_commands.describe(
        url="Link to the video — leave blank to use the most recent video link in this channel",
        attachment="Upload a video or audio file directly to transcribe",
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
            openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=120.0)

            async def update(text: str):
                await progress.edit(content=text)

            if attachment is not None:
                emb, files, transcript, metadata, summary = await _run_tldr_attachment(
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
                emb, files, transcript, metadata, summary = await _run_tldr(
                    url, mode, include_transcript, openai_client, on_step=update
                )

            # progress IS the original deferred response — edit it in-place to the final embed
            if files:
                await progress.edit(content=None, embed=emb, attachments=files)
            else:
                await progress.edit(content=None, embed=emb)
            _store_tldr_result(progress.id, transcript, metadata, summary)

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
    openai_client      = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=120.0)

    async def _send_url(url: str) -> None:
        url = normalize_url(url)
        progress = await message.channel.send(f"Downloading {_detect_platform(url)} video...")
        try:
            async def update(text: str):
                await progress.edit(content=text)
            emb, files, transcript, metadata, summary = await _run_tldr(
                url, mode, include_transcript, openai_client, on_step=update
            )
            await progress.delete()
            sent = await message.channel.send(embed=emb, files=files)
            _store_tldr_result(sent.id, transcript, metadata, summary)
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
            emb, files, transcript, metadata, summary = await _run_tldr_attachment(
                att, mode, include_transcript, openai_client, on_step=update
            )
            await progress.delete()
            sent = await message.channel.send(embed=emb, files=files)
            _store_tldr_result(sent.id, transcript, metadata, summary)
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
