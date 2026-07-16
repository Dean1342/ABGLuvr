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

from utils.conversation.context import user_personas, user_conversations
from utils.ai.multimodal import build_multimodal_content, clean_conversation_history, has_non_image_attachments
from utils.core.datetime_utils import prepend_date_context
from utils.core.text_formatting import fix_social_media_links, contains_social_media_links, contains_user_mentions, remove_mentions_from_text
from utils.ai.message_processing import (
    get_system_prompt, check_spotify_keywords, find_foreign_conversation,
    build_user_message_content, get_function_schemas, handle_openai_response,
    send_response, update_conversation_history
)
from utils.interactions.actions import handle_pending_action

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

# Initialize bot
bot = MyBot(command_prefix="/", intents=intents)

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

@bot.event
async def on_message(message: discord.Message):
    # Handles incoming messages

    # Ignore bot messages and DMs
    if message.author.bot or not message.guild:
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
    user_id = message.author.id
    conv_key = (user_id, channel_id)

    # Check for foreign conversation context
    use_foreign_convo, foreign_conv_key, original_user_id, original_display_name = await find_foreign_conversation(
        message, bot, channel_id
    )

    active_conv_key = foreign_conv_key if use_foreign_convo else conv_key

    # Inject TLDR video context when user replies to a TLDR embed
    if message.reference and message.reference.message_id:
        try:
            from cogs.transcribe import tldr_results
            ref_id = message.reference.message_id
            if ref_id in tldr_results:
                result = tldr_results[ref_id]
                marker = f"[TLDR:{ref_id}]"
                conv = user_conversations.get(active_conv_key, [])
                if not any(marker in (m.get("content") or "") for m in conv):
                    title = result["metadata"].get("title", "Unknown")
                    ctx_block = (
                        f"{marker}\nThe user is asking about a video they TLDRed.\n"
                        f"Title: \"{title}\"\nTranscript:\n{result['transcript'][:8000]}"
                    )
                    insert_at = 1 if len(conv) > 0 else 0
                    conv.insert(insert_at, {"role": "system", "content": ctx_block})
                    conv.insert(insert_at + 1, {"role": "assistant", "content": result["summary"]})
                    user_conversations[active_conv_key] = conv
        except Exception:
            pass  # never let this block the normal message pipeline

    persona = user_personas.get(active_conv_key, "Default")
    
    # Get system prompt and model
    system_prompt, model = await get_system_prompt(persona, active_conv_key)
    
    # Add Spotify instruction if needed
    spotify_instruction = ""
    if check_spotify_keywords(message.content or ""):
        spotify_instruction = "\n\nNOTE: Only add Spotify links if you are specifically recommending music that the user has explicitly requested. Do NOT add Spotify links to general conversations."
    
    current_system_prompt = prepend_date_context(system_prompt + spotify_instruction)

    # Initialize or update conversation
    conversation = user_conversations.get(active_conv_key, [])
    if not conversation or conversation[0]["role"] != "system":
        conversation = [{"role": "system", "content": current_system_prompt}]
    else:
        conversation[0]["content"] = current_system_prompt

    # Build multimodal content from message
    content = await build_multimodal_content(message)

    # Check if message has non-image file attachments to determine if web search should be available
    has_files = has_non_image_attachments(message)

    # Set up OpenAI client
    openai_api_key = os.getenv('OPENAI_API_KEY', 'YOUR_OPENAI_API_KEY')
    if openai_api_key == 'YOUR_OPENAI_API_KEY':
        await message.reply("⚠️ OpenAI API key not configured. Please check your environment variables.")
        return
    
    client = AsyncOpenAI(
        api_key=openai_api_key,
        timeout=60.0,
        max_retries=2
    )

    # Build user message for OpenAI
    api_message_content, clean_message_content, display_name, username, user_id = build_user_message_content(
        message, content, original_user_id, original_display_name
    )

    # Clean conversation history
    conversation = await clean_conversation_history(conversation)
    api_ready_conversation = []
    for msg in conversation:
        api_msg = {"role": msg["role"], "content": msg["content"]}
        api_ready_conversation.append(api_msg)
        
    messages = api_ready_conversation + [{"role": "user", "content": api_message_content}]

    # Call OpenAI and send response
    async with message.channel.typing():
        function_schemas = get_function_schemas()
        choice, answer, pending_action = await handle_openai_response(client, messages, function_schemas, model, openai_api_key)

        if choice is None:
            await message.reply(answer)
            return

    # Capture the sent ack so an interactive action can react to it. Confirmation/
    # execution runs OUTSIDE the typing() block so the reaction wait doesn't hang it.
    # For ping/schedule acks, suppress mentions so the target isn't pinged (spoiled)
    # by the acknowledgement — only the actual action should ping them.
    ack_message = await send_response(message, answer, suppress_mentions=bool(pending_action))

    if pending_action:
        await handle_pending_action(bot, message, ack_message, pending_action)

    # Update conversation history
    await update_conversation_history(
        conversation, clean_message_content, answer, user_id, display_name, username, active_conv_key, openai_api_key
    )

# Run the bot
if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))