# Bot helper functions for message processing and conversation management
import os
import re
import asyncio
import discord
import datetime
import json
from openai import AsyncOpenAI
from utils.conversation.context import user_personas, user_conversations, user_models, GLOBAL_BEHAVIOR, PERSONAS, MODELS, trim_conversation_by_tokens
from utils.ai.multimodal import build_multimodal_content, clean_conversation_history
from utils.core.datetime_utils import prepend_date_context
from utils.integrations.websearch import web_search_and_summarize
from utils.integrations.currency import convert_currency
from utils.core.text_formatting import format_discord_links
from utils.conversation.persona_loaders import load_jagbir_persona, load_lemon_persona, load_epoe_persona
from utils.interactions.actions import (
    get_interaction_function_schemas, build_pending_action, build_ack_instruction,
    build_delivery_instruction
)


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


async def get_system_prompt(persona, active_conv_key):
    # Get the system prompt for the given persona and user's selected model
    if persona == "Jagbir":
        system_prompt = GLOBAL_BEHAVIOR + " " + load_jagbir_persona()
    elif persona == "Lemon":
        system_prompt = GLOBAL_BEHAVIOR + " " + load_lemon_persona()
    elif persona == "Epoe":
        system_prompt = GLOBAL_BEHAVIOR + " " + load_epoe_persona()
    else:
        system_prompt = GLOBAL_BEHAVIOR + " " + PERSONAS[persona]
    
    # Get user's selected model, default to GPT-5.4 Mini
    user_model = user_models.get(active_conv_key, "GPT-5.4 Mini")
    model_id = MODELS[user_model]["id"]
    
    return system_prompt, model_id


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
    clean_message_content = content if content else message.content
    
    if isinstance(content, list) and content:
        api_message_content = ([{"type": "text", "text": context_note + author_info}] if context_note else [{"type": "text", "text": author_info}]) + content
    elif isinstance(content, list):
        api_message_content = [{"type": "text", "text": context_note + author_info}] if context_note else [{"type": "text", "text": author_info}]
    else:
        api_message_content = (context_note + author_info + "\n" + (str(content) if content else message.content))
    
    return api_message_content, clean_message_content, display_name, username, user_id


def get_function_schemas():
    current_date = datetime.date.today().strftime('%A, %B %d, %Y')
    # Return function schemas for OpenAI function calling
    return [
        {
            "name": "web_search",
            "description": (
                f"Searches the web and returns up-to-date information from real sources. "
                f"Today's date is {current_date}. Your internal knowledge has a training cutoff and may be stale, incomplete, or wrong for anything specific.\n\n"
                f"CALL this whenever giving an accurate answer depends on information you cannot reliably recall from memory, including:\n"
                f"- Current events, news, recent releases, or anything time-sensitive (prices, scores, weather, schedules, 'latest'/'newest' anything).\n"
                f"- Specific facts about real people, companies, products, software versions, specs, or events — especially niche or recent ones.\n"
                f"- Any question where being out of date or slightly wrong would matter to the user (statistics, dates, records, 'who/what/when/where is...', 'how much does X cost', 'is X still...').\n"
                f"- Things that plausibly changed after your training cutoff, or that you are not confident you know precisely.\n\n"
                f"Prefer searching over guessing when the user clearly wants a factual, correct answer. It is better to search than to hallucinate a confident-but-wrong answer.\n\n"
                f"Do NOT call this for:\n"
                f"- Casual conversation, opinions, jokes, roleplay, or staying in character.\n"
                f"- Creative writing, brainstorming, summarizing, or transforming text the user already provided.\n"
                f"- Math, logic, coding, or reasoning you can do yourself.\n"
                f"- Timeless general knowledge you are confident about (basic definitions, common facts, well-established concepts).\n"
                f"- Information already present earlier in this conversation.\n"
                f"When unsure whether a factual question needs current data, lean toward searching; when the message is clearly casual or self-contained, don't."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A concise, keyword-focused search query capturing exactly what needs to be looked up. "
                            "Resolve references from the conversation into a standalone query (e.g. replace 'he'/'that game'/'there' with the actual name). "
                            "Do not include conversational filler; write it the way you'd type it into a search engine."
                        )
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "convert_currency",
            "description": (
                "Converts a specific amount of money from one currency into another using the live exchange rate. "
                "ONLY call this when the user is explicitly and unambiguously asking to convert a value between currencies "
                "and clearly wants the equivalent amount in the target currency. "
                "Trigger examples: 'convert 50 USD to EUR', 'how much is 100 euros in dollars', "
                "'what's 20 GBP in yen', 'what would 5.69 dollars be in euros'.\n\n"
                "Do NOT call this function in these cases:\n"
                "- The user is comparing prices or values that are already stated in different currencies (e.g. '$5.69/gallon vs 2.20 euro/liter') — "
                "just discuss/reason about the comparison in text; do not silently convert.\n"
                "- Currencies or amounts are merely mentioned in passing, as context, or as part of a broader discussion.\n"
                "- The user is asking about exchange rate trends, economics, or opinions rather than a concrete conversion.\n"
                "- The target currency is ambiguous or not specified. When in doubt, answer in text and ask what they want converted "
                "instead of calling this function."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "The amount to convert"
                    },
                    "from_currency": {
                        "type": "string",
                        "description": "The source currency code (e.g., USD, EUR, GBP)"
                    },
                    "to_currency": {
                        "type": "string",
                        "description": "The explicit target currency code the user asked to convert into (e.g., USD, EUR, GBP). Only set this when the user clearly named or implied a specific target currency."
                    }
                },
                "required": ["amount", "from_currency", "to_currency"]
            }
        },
        *get_interaction_function_schemas()
    ]


async def handle_openai_response(client, messages, function_schemas, model, openai_api_key):
    # Handle OpenAI API response with robust error handling
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            # Initial API call
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                functions=function_schemas,
                function_call="auto"
            )
            choice = response.choices[0]
            pending_action = None  # discord-side action returned up to on_message

            # Handle function calls if requested
            if choice.finish_reason == "function_call":
                func_name = choice.message.function_call.name
                func_args = choice.message.function_call.arguments
                args = json.loads(func_args)

                if func_name == "web_search":
                    try:
                        web_context = await web_search_and_summarize(args["query"], openai_api_key)

                        # Inject the search results as context while preserving the full
                        # conversation history, so follow-ups and references stay coherent.
                        search_context = (
                            f"[WEB SEARCH RESULTS for the user's latest message]\n"
                            f"{web_context}\n\n"
                            f"Use the information above to answer the user's most recent message accurately. "
                            f"Only use what's relevant; ignore results that don't help. If the results don't actually "
                            f"answer the question, say so honestly instead of making something up. "
                            f"Don't announce that you searched — just weave the facts in naturally as if you already knew them, "
                            f"staying completely in character and consistent with your personality and speaking style. "
                            f"Respond conversationally, not as a formal report."
                        )

                        # Preserve the original conversation and append the search context as a
                        # system message right before generating the final response.
                        final_messages = messages + [{"role": "system", "content": search_context}]

                        response2 = await client.chat.completions.create(
                            model=model,
                            messages=final_messages
                        )
                        answer = response2.choices[0].message.content
                        
                        # Add source links if available
                        source_pattern = re.compile(r"Source: (.*?) \((https?://[^)]+)\)")
                        sources = source_pattern.findall(web_context)
                        if sources:
                            sources_section = "\n\nSources:\n" + "\n".join([
                                f"[{title}]({url})" if title else f"{url}" for title, url in sources
                            ])
                            answer = answer.strip() + sources_section
                            
                    except Exception as e:
                        print(f"Error in web search processing: {e}")
                        answer = "I found some information from web search, but I'm having trouble processing it right now. Please try again."
                
                elif func_name == "convert_currency":
                    try:
                        result = await convert_currency(
                            args["amount"], 
                            args["from_currency"], 
                            args["to_currency"]
                        )
                        
                        if result.get("success"):
                            # Format all numbers with commas for better readability
                            def format_number(num):
                                if isinstance(num, float):
                                    # For decimals, format to 2 places and add commas
                                    return f"{num:,.2f}".rstrip('0').rstrip('.')
                                else:
                                    # For integers, just add commas
                                    return f"{num:,}"
                            
                            original_formatted = format_number(result['original_amount'])
                            converted_formatted = format_number(result['converted_amount'])
                            rate_formatted = format_number(result['exchange_rate'])
                            
                            answer = (
                                f"{original_formatted} {result['from_currency']} = "
                                f"**{converted_formatted} {result['to_currency']}**\n\n"
                                f"-# *Converted via ExchangeRate-API (Rate: 1 {result['from_currency']} = {rate_formatted} {result['to_currency']})*"
                            )
                        else:
                            answer = f"❌ Currency conversion failed: {result.get('error', 'Unknown error')}"
                    except Exception as e:
                        print(f"Error in currency conversion: {e}")
                        answer = "❌ Currency conversion failed due to an unexpected error. Please try again."

                elif func_name in ("spam_ping", "schedule_message"):
                    # These require Discord context (guild/channel/reactions) that doesn't
                    # exist here, so we don't execute — we build a normalized pending action
                    # and return it up to on_message. The ack itself is generated in the
                    # persona's voice via a second, function-free completion.
                    try:
                        pending_action = build_pending_action(func_name, args)

                        # Only craft a delivery message when the user actually gave something
                        # to say. With no note it's just a bare ping — don't invent meta-text.
                        if pending_action.get("note"):
                            # Craft the ACTUAL message sent to the target in the bot's own
                            # persona voice (second person), unless the user wanted a verbatim
                            # phrase. Done now (client available) so scheduled sends stay LLM-free.
                            delivery_messages = messages + [
                                {"role": "system", "content": build_delivery_instruction(pending_action)}
                            ]
                            delivery_resp = await client.chat.completions.create(
                                model=model,
                                messages=delivery_messages
                            )
                            crafted = (delivery_resp.choices[0].message.content or "").strip().strip('"').strip()
                            if crafted:
                                pending_action["delivery_text"] = crafted

                        # Persona-voiced acknowledgement to the requester.
                        instruction = build_ack_instruction(pending_action)
                        final_messages = messages + [{"role": "system", "content": instruction}]
                        response2 = await client.chat.completions.create(
                            model=model,
                            messages=final_messages
                        )
                        answer = response2.choices[0].message.content
                    except Exception as e:
                        print(f"Error building interactive action: {e}")
                        pending_action = None
                        answer = "hmm, i couldn't set that up right now — try again?"

                else:
                    answer = f"Function {func_name} not implemented."

            # Handle regular text response
            else:
                answer = choice.message.content

            return choice, answer, pending_action  # Success, return the final answer

        except Exception as e:
            print(f"OpenAI API Error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt + 1 >= max_retries:
                # All retries failed, return an error message to the user
                return None, "⚠️ Sorry, I'm having trouble connecting to the AI service after multiple attempts. Please try again later.", None
            await asyncio.sleep(1.5)  # Wait before retrying

    # Fallback in case the loop finishes unexpectedly
    return None, "⚠️ An unexpected error occurred while processing your request.", None


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