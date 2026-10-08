# Bot helper functions for message processing and conversation management
import re
import discord
from utils.core.text_formatting import format_discord_links
from utils.ai.agent import author_tag


def resolve_discord_user_id(user_str, guild):
    # Resolve a Discord user mention or name to a user ID. Accepts a raw mention token
    # (<@id>), or a display name / username / global name so users can target someone
    # by name without actually @-tagging (and pinging) them.
    if not user_str:
        return None
    match = re.match(r'<@!?(\d+)>', user_str)
    if match:
        return int(match.group(1))
    if guild is None:
        return None

    needle = user_str.strip().lstrip('@').lower()
    if not needle:
        return None

    def names_of(m):
        return [n for n in (m.display_name, m.name, getattr(m, 'global_name', None)) if n]

    # 1) Exact, case-insensitive match on any of the member's names.
    for m in guild.members:
        if any(n.lower() == needle for n in names_of(m)):
            return m.id

    # 2) Fallback: substring match, but only if it uniquely identifies one member
    #    (avoids pinging the wrong person on an ambiguous partial name).
    matches = [m for m in guild.members if any(needle in n.lower() for n in names_of(m))]
    if len(matches) == 1:
        return matches[0].id
    return None


def build_user_message_content(message, content):
    # The current message for OpenAI: speaker label + multimodal content
    display_name = message.author.display_name if hasattr(message.author, 'display_name') else message.author.name
    username = message.author.name
    user_id = message.author.id

    # Speaker label, same format as the channel history (see agent.author_tag)
    author_info = author_tag(display_name)

    if isinstance(content, list):
        api_message_content = [{"type": "text", "text": author_info}] + content
    else:
        api_message_content = author_info + " " + (str(content) if content else message.content)

    return api_message_content, display_name, username, user_id


async def send_response(message, answer, suppress_mentions=False):
    # Send a response to Discord. Returns the primary sent message so callers can
    # act on it (e.g. attach a confirmation reaction for interactive actions).
    # suppress_mentions=True stops the reply from pinging anyone — used for the ack of
    # a ping/schedule action so the target isn't notified (spoiled) before it fires.
    answer = format_discord_links(answer)
    max_len = 2000
    kwargs = {"allowed_mentions": discord.AllowedMentions.none()} if suppress_mentions else {}

    if len(answer) <= max_len:
        return await message.reply(answer, **kwargs)
    else:
        first = None
        for i in range(0, len(answer), max_len):
            sent = await message.channel.send(answer[i:i+max_len], **kwargs)
            if first is None:
                first = sent
        return first
