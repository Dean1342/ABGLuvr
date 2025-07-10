# Bot helper functions for message processing and conversation management
import os
import re
import asyncio
import discord
from openai import AsyncOpenAI
from utils.conversation.context import user_personas, user_conversations, GLOBAL_BEHAVIOR, PERSONAS, trim_conversation_by_tokens
from utils.ai.multimodal import build_multimodal_content, clean_conversation_history
from utils.core.datetime_utils import prepend_date_context
from utils.integrations.websearch import web_search_and_summarize
from utils.core.text_formatting import format_discord_links
from utils.conversation.persona_loaders import load_jagbir_persona, load_lemon_persona, load_epoe_persona


def resolve_discord_user_id(user_str, guild):
    # Resolve a Discord user mention or name to user ID
    match = re.match(r'<@!?(\d+)>', user_str)
    if match:
        return int(match.group(1))
    member = discord.utils.find(lambda m: m.display_name == user_str or m.name == user_str, guild.members)
    if member:
        return member.id
    return None


async def get_system_prompt(persona, active_conv_key):
    # Get the system prompt for the given persona
    if persona == "Jagbir":
        system_prompt = GLOBAL_BEHAVIOR + " " + load_jagbir_persona()
        model = "gpt-4.1-2025-04-14"
    elif persona == "Lemon":
        system_prompt = GLOBAL_BEHAVIOR + " " + load_lemon_persona()
        model = os.getenv("OPENAI_FINAL_MODEL", "gpt-4.1-2025-04-14")
    elif persona == "Epoe":
        system_prompt = GLOBAL_BEHAVIOR + " " + load_epoe_persona()
        model = os.getenv("OPENAI_FINAL_MODEL", "gpt-4.1-2025-04-14")
    else:
        system_prompt = GLOBAL_BEHAVIOR + " " + PERSONAS[persona]
        model = os.getenv("OPENAI_FINAL_MODEL", "gpt-4.1-2025-04-14")
    
    return system_prompt, model


def check_spotify_keywords(message_content):
    # Check if message contains music-related keywords
    explicit_music_keywords = [
        "recommend music", "suggest songs", "what song", "play song", 
        "good music", "spotify link", "spotify search"
    ]
    return any(keyword in message_content.lower() for keyword in explicit_music_keywords)


async def find_foreign_conversation(message, bot, channel_id):
    # Check if replying to a bot message and find the original conversation context
    use_foreign_convo = False
    foreign_conv_key = None
    original_user_id = None
    original_display_name = None
    
    if message.reference and message.reference.resolved:
        replied = message.reference.resolved
        if replied.author.id == bot.user.id:
            for (other_user_id, ch_id), convo in user_conversations.items():
                if ch_id == channel_id and convo and len(convo) > 1:
                    last_assistant_msg = next((msg for msg in reversed(convo) if msg["role"] == "assistant"), None)
                    if last_assistant_msg:
                        content_match = False
                        
                        if "responding_to" in last_assistant_msg:
                            if isinstance(last_assistant_msg["content"], str):
                                content_match = last_assistant_msg["content"].strip() == replied.content.strip()
                            elif isinstance(last_assistant_msg["content"], list):
                                text_content = " ".join(
                                    part.get("text", "") for part in last_assistant_msg["content"] 
                                    if isinstance(part, dict) and "text" in part
                                )
                                content_match = text_content.strip() == replied.content.strip()
                            
                            if content_match:
                                use_foreign_convo = True
                                foreign_conv_key = (other_user_id, channel_id)
                                original_user_id = last_assistant_msg["responding_to"].get("user_id")
                                original_display_name = last_assistant_msg["responding_to"].get("display_name")
                                break
                        
                        elif last_assistant_msg.get("content"):
                            if isinstance(last_assistant_msg["content"], str):
                                content_match = last_assistant_msg["content"].strip() == replied.content.strip()
                            elif isinstance(last_assistant_msg["content"], list):
                                text_content = " ".join(
                                    part.get("text", "") for part in last_assistant_msg["content"] 
                                    if isinstance(part, dict) and "text" in part
                                )
                                content_match = text_content.strip() == replied.content.strip()
                            
                            if content_match:
                                use_foreign_convo = True
                                foreign_conv_key = (other_user_id, channel_id)
                                break
    
    return use_foreign_convo, foreign_conv_key, original_user_id, original_display_name


def build_user_message_content(message, content, original_user_id, original_display_name):
    # Build the user message content for OpenAI
    display_name = message.author.display_name if hasattr(message.author, 'display_name') else message.author.name
    username = message.author.name
    user_id = message.author.id
    
    # Create author instruction for the model
    author_info = f"[IMPORTANT: The current message is from user {display_name} (username: {username}, id: {user_id}). Always respond directly to THIS user in your reply.]"
    
    # Add context clarification if replying to a foreign conversation
    context_note = ""
    if original_user_id and original_user_id != user_id:
        context_note = (
            f"[NOTE: The original message being discussed was sent by {original_display_name} (user id: {original_user_id}). "
            f"The current question is from {display_name} (user id: {user_id}). "
            f"Please do NOT refer to the original content as belonging to {display_name}. If referencing the original content, use third person or clarify the sender.]"
        )
    
    # Build multimodal content with author info
    if isinstance(content, list) and content:
        user_message_content = ([{"type": "text", "text": context_note + author_info}] if context_note else [{"type": "text", "text": author_info}]) + content
    elif isinstance(content, list):
        user_message_content = [{"type": "text", "text": context_note + author_info}] if context_note else [{"type": "text", "text": author_info}]
    else:
        user_message_content = (context_note + author_info + "\n" + (str(content) if content else message.content))
    
    return user_message_content, display_name, username, user_id


def get_function_schemas():
    # Return function schemas for OpenAI function calling
    return [
        {
            "name": "web_search",
            "description": "Performs a web search for the given query and returns relevant results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"}
                },
                "required": ["query"]
            }
        }
    ]


async def handle_openai_response(client, messages, function_schemas, model, openai_api_key):
    # Handle OpenAI API response
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                functions=function_schemas,
                function_call="auto"
            )
            choice = response.choices[0]
            break  # Success, exit retry loop
        except Exception as e:
            retry_count += 1
            print(f"OpenAI API Error (attempt {retry_count}/{max_retries}): {e}")
            
            if retry_count >= max_retries:
                return None, "⚠️ Sorry, I'm having trouble connecting to the AI service. Please try again in a moment."
            
            # Wait a bit before retrying
            await asyncio.sleep(1)
    
    # Handle function calls
    if choice.finish_reason == "function_call":
        func_name = choice.message.function_call.name
        func_args = choice.message.function_call.arguments
        import json
        args = json.loads(func_args)
        
        if func_name == "web_search":
            web_context = await web_search_and_summarize(args["query"], openai_api_key)
            messages.append({
                "role": "function",
                "name": "web_search",
                "content": web_context
            })
            
            # Get the original user content for re-asking
            original_content = messages[-3]["content"] if len(messages) >= 3 else ""
            
            # Re-ask with web context
            messages.append({
                "role": "user",
                "content": original_content
            })
            
            try:
                response2 = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    functions=function_schemas,
                    function_call="none"
                )
                answer = response2.choices[0].message.content
            except Exception as e:
                print(f"OpenAI API Error (web search follow-up): {e}")
                answer = "I found some information from web search, but I'm having trouble processing it right now. Please try again."
            
            # Add source links if available
            import re
            source_pattern = re.compile(r"Source: (.*?) \((https?://[^)]+)\)")
            sources = source_pattern.findall(web_context)
            if sources:
                sources_section = "\n\nSources:\n" + "\n".join([
                    f"[{title}]({url})" if title else f"{url}" for title, url in sources
                ])
                answer = answer.strip() + sources_section
        else:
            answer = f"Function {func_name} not implemented."
    else:
        answer = choice.message.content
    
    return choice, answer


async def send_response(message, answer):
    # Send a response to Discord
    answer = format_discord_links(answer)
    max_len = 2000
    
    if len(answer) <= max_len:
        await message.reply(answer)
    else:
        for i in range(0, len(answer), max_len):
            await message.channel.send(answer[i:i+max_len])


async def update_conversation_history(conversation, user_message_content, answer, user_id, display_name, username, active_conv_key, openai_api_key):
    # Update the conversation history with the latest exchange
    # Add user message with metadata
    current_user_context = {"role": "user", "content": user_message_content}
    
    # Add assistant response with metadata
    assistant_response_with_target = {
        "role": "assistant", 
        "content": answer,
        "responding_to": {
            "user_id": user_id,
            "display_name": display_name,
            "username": username
        }
    }
    
    conversation.append(current_user_context)
    conversation.append(assistant_response_with_target)
    
    # Trim conversation if needed
    conversation = await trim_conversation_by_tokens(
        conversation,
        max_tokens=55000,
        model="gpt-4.1-2025-04-14",
        openai_api_key=openai_api_key
    )
    
    user_conversations[active_conv_key] = conversation
    return conversation
