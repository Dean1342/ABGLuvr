# Which videos a Discord message is about, so the agent can watch them when asked
# (the inspect_video tool). Only explicit references count: a video link or upload in the
# message itself, in the message it replies to, or the video behind a TLDR it replies to.
# Nearby messages aren't guessed at, so a bare "thoughts?" never starts a download (a TLDR
# posted just before is already covered by bot._tldr_context).
from dataclasses import dataclass

import discord

from utils.integrations.video import extract_video_urls, normalize_url
from utils.media.watch import detect_platform

MAX_VIDEOS = 4


@dataclass
class VideoRef:
    ref: str                        # what the model passes to inspect_video: "v1", "v2", ...
    where: str                      # for the model: "TikTok link in the message being replied to (posted by Jag)"
    url: str | None = None          # normalized post link (or a TLDR embed's link)
    attachment: object | None = None  # discord.Attachment
    media_key: str | None = None    # a TLDR's cached analysis


async def find_videos(message, bot_user_id) -> list[VideoRef]:
    found: list[VideoRef] = []
    _collect(message, "in this message", found)
    replied = await _replied(message)
    if replied is not None:
        if replied.author.id == bot_user_id and replied.embeds:
            await _collect_tldr(replied, found)
        else:
            _collect(replied, f"in the message being replied to (posted by {replied.author.display_name})", found)
    found = found[:MAX_VIDEOS]
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
        lines.append(f"- {video.ref}: {video.where}.{seen}")
    return {"role": "system", "content": "\n".join(lines)}


def _collect(msg, where, found):
    seen = {v.url for v in found} | {getattr(v.attachment, "id", None) for v in found}
    for att in msg.attachments:
        if (att.content_type or "").startswith("video/") and att.id not in seen:
            found.append(VideoRef("", f"video uploaded {where} ({att.filename})", attachment=att))
            seen.add(att.id)
    # Link previews (embeds) count too, e.g. another bot reposting a link.
    links = extract_video_urls(msg.content or "")
    links += [e.url for e in msg.embeds if e.url and extract_video_urls(e.url)]
    for link in links:
        url = normalize_url(link)
        if url not in seen:
            found.append(VideoRef("", f"{detect_platform(url)} link {where}", url=url))
            seen.add(url)


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


async def _replied(message):
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
