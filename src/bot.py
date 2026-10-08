import os
import sys
import asyncio
import re
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from openai import AsyncOpenAI

# Load environment variables early so imports that rely on them don't fail
# Specifically target the .env file in the parent directory (root of the workspace)
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(env_path)

# Add src directory to Python path so imports work correctly
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils.conversation.context import user_personas, user_models, MODELS, resolve_model_name
from utils.conversation.channel_context import record_message, update_message, forget_message, build_context
from utils.ai.multimodal import build_multimodal_content
from utils.core.text_formatting import fix_social_media_links, contains_social_media_links, contains_user_mentions, remove_mentions_from_text
from utils.ai.message_processing import build_user_message_content, send_response
from utils.ai.prompts import build_instructions, resolve_persona
from utils.ai.agent import run_agent
from utils.ai.tools import ToolContext
from utils.ai.router import match_currency_conversion
from utils.integrations.currency import convert_currency, format_conversion
from utils.interactions.actions import handle_pending_action, confirmation_footer

# Main bot entry point and event handlers

# Set up Discord bot intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True  # needed for the ✅ confirmation gate on interactive actions

# Main bot class
class MyBot(commands.Bot):
    async def setup_hook(self):
        # Setup cogs and sync commands
        try:
            from cogs.spotify import Spotify
            await self.add_cog(Spotify(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.rate import Rate
            await self.add_cog(Rate(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.persona import Persona
            await self.add_cog(Persona(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.help import Help
            await self.add_cog(Help(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.model import Model
            await self.add_cog(Model(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.transcribe import Transcribe
            await self.add_cog(Transcribe(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.build import Build
            await self.add_cog(Build(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.admin import Admin
            await self.add_cog(Admin(self))
        except Exception as e:
            import traceback
            traceback.print_exc()

# Initialize bot
bot = MyBot(command_prefix="/", intents=intents)

# One shared OpenAI client (connection pooling); retries are handled by the SDK.
_openai_client = None


def get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'), timeout=60.0, max_retries=2)
    return _openai_client

@bot.event
async def on_ready():
    # Called when the bot is ready
    try:
        await asyncio.sleep(2)
        await bot.tree.sync()
    except Exception as e:
        import traceback
        traceback.print_exc()
    # Re-arm any scheduled reminders that were persisted before a restart.
    try:
        from utils.interactions.actions import restore_scheduled_reminders
        await restore_scheduled_reminders(bot)
    except Exception as e:
        import traceback
        traceback.print_exc()
    # Restore saved /persona and /model selections.
    try:
        from utils.conversation.settings import restore_user_settings
        await restore_user_settings()
    except Exception as e:
        import traceback
        traceback.print_exc()

def _tldr_context(message):
    # When a message replies to a TLDR embed, hand the agent that video's transcript.
    ref = message.reference
    if not ref or not ref.message_id:
        return None
    from cogs.transcribe import tldr_results
    result = tldr_results.get(ref.message_id)
    if not result:
        return None
    title = result["metadata"].get("title", "Unknown")
    return {
        "role": "system",
        "content": (
            "You posted a TLDR of this video, and the next user message is a direct reply to it, "
            "so treat the video as the obvious subject.\n"
            f"Title: \"{title}\"\nYour summary: {result['summary']}\n"
            f"Transcript:\n{result['transcript'][:8000]}"
        ),
    }


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    # Keep channel context current (TLDR progress messages turn into embeds this way).
    if after.guild:
        update_message(after, bot.user.id)


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    forget_message(payload.channel_id, payload.message_id)


@bot.event
async def on_message(message: discord.Message):
    # Handles incoming messages

    if not message.guild:
        return
    # Everything in the channel (the bot's own messages included) feeds channel context.
    record_message(message, bot.user.id)
    if message.author.bot:
        return

    # TLDR mention shortcut — run BEFORE the link fixer, but don't return yet so the
    # fixer can still clean up the raw social media URL in the same message
    is_tldr = bot.user in message.mentions and re.search(r'/tldr', message.content or '', re.IGNORECASE)
    if is_tldr:
        from cogs.transcribe import handle_tldr_mention
        await handle_tldr_mention(message)
        # fall through to link fixer below

    # Check for social media links that need fixing FIRST (before any channel restrictions)
    if message.content and contains_social_media_links(message.content):
        fixed_content, link_changed = fix_social_media_links(message.content)
        
        if link_changed:
            try:
                # Delete the original message
                await message.delete()
                
                # Add sub-text footer using Discord markdown with blank line separation
                footer_message = "\n\n-# 🔗 Embed Fixed & Resent • Link automatically fixed for better Discord embeds"
                content_with_footer = fixed_content + footer_message
                
                # Check if message contains user mentions to prevent double pings
                has_mentions = contains_user_mentions(fixed_content)
                
                # Try webhook approach first for better user attribution
                try:
                    webhooks = await message.channel.webhooks()
                    webhook = None
                    
                    # Find existing bot webhook or create one
                    for wh in webhooks:
                        if wh.user == bot.user:
                            webhook = wh
                            break
                    
                    if not webhook:
                        webhook = await message.channel.create_webhook(name="ABGLuvr Link Fixer")
                    
                    if has_mentions:
                        # Two-step approach to prevent double pings but keep highlighting
                        # Step 1: Send without mentions
                        content_without_mentions, original_content = remove_mentions_from_text(content_with_footer)
                        sent_message = await webhook.send(
                            content=content_without_mentions,
                            username=message.author.display_name,
                            avatar_url=message.author.avatar.url if message.author.avatar else None,
                            allowed_mentions=discord.AllowedMentions.none(),
                            wait=True
                        )
                        
                        # Step 2: Edit to include mentions (won't trigger new notifications)
                        await sent_message.edit(
                            content=content_with_footer,
                            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True)
                        )
                    else:
                        # No mentions, send normally
                        await webhook.send(
                            content=content_with_footer,
                            username=message.author.display_name,
                            avatar_url=message.author.avatar.url if message.author.avatar else None,
                            allowed_mentions=discord.AllowedMentions(everyone=True, users=True, roles=True)
                        )
                    
                except (discord.Forbidden, discord.HTTPException):
                    # Fallback: Send as bot with user attribution in the message
                    attribution_content = f"**{message.author.display_name}:** {content_with_footer}"
                    
                    if has_mentions:
                        # Two-step approach for fallback too
                        attribution_no_mentions, _ = remove_mentions_from_text(attribution_content)
                        sent_message = await message.channel.send(
                            content=attribution_no_mentions,
                            allowed_mentions=discord.AllowedMentions.none()
                        )
                        
                        await sent_message.edit(
                            content=attribution_content,
                            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True)
                        )
                    else:
                        await message.channel.send(
                            content=attribution_content,
                            allowed_mentions=discord.AllowedMentions(everyone=True, users=True, roles=True)
                        )
                
                # Return early to prevent normal bot processing
                return
                
            except discord.Forbidden:
                # If we can't delete the message or create webhook, just continue with normal processing
                pass
            except Exception as e:
                # Log error but continue with normal processing
                print(f"Error fixing social media links: {e}")

    if is_tldr:
        return  # skip LLM pipeline (link fixer already returned if it ran)

    # Channel restrictions and mention override (for normal bot functionality)
    channel_ids = os.getenv("CHANNEL_IDS", os.getenv("CHANNEL_ID", ""))
    allowed_channels = [cid.strip() for cid in channel_ids.split(",") if cid.strip()]
    mentioned = bot.user in message.mentions
    if allowed_channels:
        if str(message.channel.id) not in allowed_channels and not mentioned:
            return
        if str(message.channel.id) in allowed_channels and message.content.startswith("!"):
            return

    channel_id = message.channel.id
    conv_key = (message.author.id, channel_id)

    persona = resolve_persona(user_personas.get(conv_key))
    model_name = resolve_model_name(user_models.get(conv_key))
    instructions = build_instructions(persona)

    # Build multimodal content from message (quotes the replied-to message, attaches images/files)
    content = await build_multimodal_content(message)

    openai_api_key = os.getenv('OPENAI_API_KEY', 'YOUR_OPENAI_API_KEY')
    if openai_api_key == 'YOUR_OPENAI_API_KEY':
        await message.reply("⚠️ OpenAI API key not configured. Please check your environment variables.")
        return

    api_message_content, display_name, username, user_id = build_user_message_content(message, content)

    # Plain conversions ("50 usd to eur") are answered directly, no LLM involved.
    conversion = None if message.attachments else match_currency_conversion(message.content or "")
    if conversion:
        answer = format_conversion(await convert_currency(*conversion))
        await send_response(message, answer)
        return

    # Recent channel messages (everyone's, oldest first), plus video context for TLDR replies
    history = await build_context(message, bot.user.id, user_id)
    try:
        tldr = _tldr_context(message)
        if tldr:
            history.append(tldr)
    except Exception:
        pass  # never let this block the normal message pipeline

    client = get_openai_client()
    model = MODELS[model_name]
    ctx = ToolContext(
        client=client,
        model_id=model["id"],
        instructions=instructions,
        reasoning=model["api"]["reasoning"],
    )
    log_meta = {
        "user_id": user_id,
        "guild_id": message.guild.id,
        "channel_id": channel_id,
        "persona": persona,
        "context_messages": len(history),
        "message": (message.content or "")[:200],
    }

    async with message.channel.typing():
        result = await run_agent(client, model_name, instructions, history, api_message_content, ctx, log_meta)

    if result.failed:
        await message.reply(result.text)
        return

    pending_actions = result.pending_actions
    answer = result.text + confirmation_footer(pending_actions)

    # For ping/schedule acks, suppress mentions so the target isn't pinged (spoiled)
    # by the acknowledgement — only the actual action should ping them.
    ack_message = await send_response(message, answer, suppress_mentions=bool(pending_actions))

    # Confirmation/execution runs OUTSIDE the typing() block so the reaction wait doesn't hang it.
    for pending in pending_actions:
        await handle_pending_action(bot, message, ack_message, pending)

# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))