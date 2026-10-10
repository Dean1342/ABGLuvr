# Which videos a Discord message is about, so the agent can watch them when asked
# (the inspect_video tool). Only explicit references count: a video link or upload in the
# message itself, in the message it replies to, or the video behind a TLDR it replies to.
# Nearby messages aren't guessed at, so a bare "thoughts?" never starts a download (a TLDR
# posted just before is already covered by bot._tldr_context).
#
# An X link is only a video if the post has one: image and text posts are left to
# utils/links (checked with the same cached fxtwitter lookup), and a post whose video is
# really in the post it quotes points at that one.
import asyncio
from dataclasses import dataclass

import discord

from utils.conversation.channel_context import author_of, snap_label
from utils.integrations.video import extract_video_urls, normalize_url
from utils.links import safety, x_post
from utils.links.resolve import EARLIER
from utils.links.urls import x_post_id
from utils.media.store import url_key
from utils.media.watch import detect_platform
from utils.media.youtube_info import youtube_id

MAX_VIDEOS = 4


@dataclass
class VideoRef:
    ref: str                        # what the model passes to inspect_video: "v1", "v2", ...
    where: str                      # for the model: "TikTok link in the message being replied to (posted by Jag)"
    url: str | None = None          # normalized post link (or a TLDR embed's link)
    attachment: object | None = None  # discord.Attachment
    media_key: str | None = None    # a TLDR's cached analysis


_UNSET = object()


async def find_videos(message, bot_user_id, replied=_UNSET, earlier=None) -> list[VideoRef]:
    # replied: the message it replies to if the caller already fetched it (replied_message).
    # earlier: a recent message with a link or upload (channel_context.recent_reference), only
    # used when nothing else is referred to.
    found: list[VideoRef] = []
    _collect(message, "in this message", found)
    if replied is _UNSET:
        replied = await replied_message(message)
    if replied is not None:
        if replied.author.id == bot_user_id:
            if replied.embeds:
                await _collect_tldr(replied, found)
            # Otherwise its links are the bot's own citations, not videos they're asking about.
        else:
            _collect(replied, f"in the message being replied to (posted by {snap_label(author_of(replied))})", found)
    if not found and earlier is not None:
        _collect(earlier, EARLIER.format(who=snap_label(author_of(earlier))), found)
    checked = await asyncio.gather(*(_x_video(v) for v in found))
    found = [v for v in checked if v is not None][:MAX_VIDEOS]
    for i, video in enumerate(found, 1):
        video.ref = f"v{i}"
    return found


def videos_note(videos: list[VideoRef]) -> dict:
    # History entry placed just before the current message.
    lines = ["# Videos",
             "The latest message refers to these videos. You haven't watched one unless it says so; "
             "call inspect_video with its ref before saying anything about what's in it."]
    for video in videos:
        seen = " You summarized it already (what you know is above); inspect it again only for more detail." \
            if video.media_key else ""
        if youtube_id(video):
            seen += (" It's on YouTube: inspect_youtube gives its title, channel, description, numbers and comments "
                     "without watching.")
        lines.append(f"- {video.ref}: {video.where}.{seen}")
    return {"role": "system", "content": "\n".join(lines)}


def _safe_filename(name):
    # The note is the bot's own framing (developer role), and filenames are user-chosen.
    return " ".join(str(name or "").split())[:80].replace('"', "'")


def _same_video(url):
    # One key per video, so a share link and Discord's embed URL for it ("youtu.be/X?si=…" vs
    # "youtube.com/watch?v=X") aren't listed as two videos.
    return url_key(url) or url


def _collect(msg, where, found):
    seen = {_same_video(v.url) for v in found if v.url} | {getattr(v.attachment, "id", None) for v in found}
    for att in msg.attachments:
        if (att.content_type or "").startswith("video/") and att.id not in seen:
            found.append(VideoRef("", f"video uploaded {where} ({_safe_filename(att.filename)})", attachment=att))
            seen.add(att.id)
    # Link previews (embeds) count too, e.g. another bot reposting a link.
    links = extract_video_urls(msg.content or "")
    links += [e.url for e in msg.embeds if e.url and extract_video_urls(e.url)]
    for link in links:
        url = normalize_url(link)
        if _same_video(url) not in seen:
            found.append(VideoRef("", f"{detect_platform(url)} link {where}", url=url))
            seen.add(_same_video(url))


async def _collect_tldr(replied, found):
    # The bot's own embed: if it's a TLDR, its video (cached, and re-watchable from its link).
    from cogs.transcribe import get_tldr_result  # lazy: cogs import utils, not the other way around
    result = await get_tldr_result(replied.id)
    if result is None:
        return  # some other embed (/rate, /build)
    url = replied.embeds[0].url
    found.append(VideoRef("", "the video your TLDR (the message being replied to) is about",
                          url=normalize_url(url) if url and extract_video_urls(url) else url,
                          media_key=result["metadata"].get("media_key")))


async def _x_video(video):
    # The VideoRef if it's really a video, re-pointed at a quoted post's video, or None.
    post_id = x_post_id(video.url) if video.url else None
    if post_id is None or video.media_key:
        return video
    try:
        post = await x_post.fetch_post(post_id)
    except safety.LinkError as e:
        # Gone or private: nothing to watch. Network trouble: keep it, the download may still work.
        return None if e.code in ("not_found", "unavailable") else video
    if post.has_video:
        return video
    if post.quote and post.quote.has_video:
        video.url = post.quote.url
        video.where += ", in the post it quotes"
        return video
    return None


async def replied_message(message):
    ref = message.reference
    if not ref or not ref.message_id:
        return None
    if getattr(ref.resolved, "author", None) is not None:  # not a DeletedReferencedMessage
        return ref.resolved
    # Discord doesn't always include the replied-to message (older ones especially).
    try:
        return await message.channel.fetch_message(ref.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None
