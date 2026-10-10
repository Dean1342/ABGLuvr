import os
import sys
import asyncio
import re
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# Load environment variables early so imports that rely on them don't fail
# Specifically target the .env file in the parent directory (root of the workspace)
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
# override: .env wins over variables already in the shell. VS Code copies .env into each
# terminal when it opens, so a stale copy (e.g. a token reset since) would otherwise win.
# Heroku has no .env file, so its config vars are unaffected.
load_dotenv(env_path, override=True)

# Log lines include user text (any language); Windows consoles default to cp1252 and
# would raise UnicodeEncodeError mid-reply.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Add src directory to Python path so imports work correctly
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from utils.conversation.channel_context import record_message, update_message, forget_message, note_repost
from utils.core.text_formatting import fix_social_media_links, contains_social_media_links, contains_user_mentions, remove_mentions_from_text
from utils.ai.message_processing import send_response
from utils.ai.router import match_currency_conversion
from utils.ai.turn import answer, openai_client, prepare_turn, tldr_context
from utils.integrations.currency import convert_currency, format_conversion

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
        try:
            from cogs.memory import Memory
            await self.add_cog(Memory(self))
        except Exception as e:
            import traceback
            traceback.print_exc()
        try:
            from cogs.investigate import Investigate
            await self.add_cog(Investigate(self))
        except Exception as e:
            import traceback
            traceback.print_exc()

# Initialize bot
bot = MyBot(command_prefix="/", intents=intents)

# The shared OpenAI client and the TLDR follow-up context live in utils/ai/turn.py.
get_openai_client = openai_client
_tldr_context = tldr_context  # tests call it through bot

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
    # Drop stored TLDR transcripts and cached video analyses older than 30 days.
    try:
        from cogs.transcribe import prune_stored_tldrs
        await prune_stored_tldrs()
        from utils.media import store as media_store
        await media_store.prune()
    except Exception as e:
        import traceback
        traceback.print_exc()

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

    # Where the reply goes: the message itself, or its repost when the link fixer replaced it.
    reply_to = message

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
                        sent_message = reposted = await webhook.send(
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
                        reposted = await webhook.send(
                            content=content_with_footer,
                            username=message.author.display_name,
                            avatar_url=message.author.avatar.url if message.author.avatar else None,
                            allowed_mentions=discord.AllowedMentions(everyone=True, users=True, roles=True),
                            wait=True
                        )
                    
                except (discord.Forbidden, discord.HTTPException):
                    # Fallback: Send as bot with user attribution in the message
                    attribution_content = f"**{message.author.display_name}:** {content_with_footer}"
                    
                    if has_mentions:
                        # Two-step approach for fallback too
                        attribution_no_mentions, _ = remove_mentions_from_text(attribution_content)
                        sent_message = reposted = await message.channel.send(
                            content=attribution_no_mentions,
                            allowed_mentions=discord.AllowedMentions.none()
                        )
                        
                        await sent_message.edit(
                            content=attribution_content,
                            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True)
                        )
                    else:
                        reposted = await message.channel.send(
                            content=attribution_content,
                            allowed_mentions=discord.AllowedMentions(everyone=True, users=True, roles=True)
                        )
                
                # The webhook is the repost's author; remember who really sent it.
                if getattr(reposted, "webhook_id", None):
                    note_repost(message.channel.id, reposted.id, message.author)

                # A plain link post is done. If the bot was mentioned ("@ABGLuvr how? <link>"),
                # answer it, replying to the repost since the original is gone.
                if bot.user not in message.mentions or is_tldr:
                    return
                reply_to = reposted
                
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

    if os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_API_KEY") == "YOUR_OPENAI_API_KEY":
        await message.reply("⚠️ OpenAI API key not configured. Please check your environment variables.")
        return

    # Plain conversions ("50 usd to eur") are answered directly, no LLM involved.
    conversion = None if message.attachments else match_currency_conversion(message.content or "")
    if conversion:
        await send_response(message, format_conversion(await convert_currency(*conversion)))
        return

    # Context (channel window, memory, videos, links...), then the agent and the reply. The
    # model may offer a deep investigation instead; utils/ai/turn.py handles the buttons.
    turn = await prepare_turn(message, bot)
    await answer(turn, reply_to)

# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))