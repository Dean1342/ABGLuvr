import httpx


async def is_expired_discord_cdn_url(url):
    # Check if a Discord CDN image URL is expired
    if not (isinstance(url, str) and url.startswith("https://cdn.discordapp.com/")):
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.head(url, timeout=5)
            return resp.status_code >= 400
    except Exception:
        return True


async def filter_expired_images_from_content(content):
    # Remove expired Discord CDN image URLs from multimodal content
    if not isinstance(content, list):
        return content

    filtered_content = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "image_url":
                image_url = part.get("image_url", {}).get("url", "")
                # Only keep image if it's not expired
                if not await is_expired_discord_cdn_url(image_url):
                    filtered_content.append(part)
                # If expired, we simply skip it (don't add to filtered_content)
            else:
                # Keep non-image parts as-is
                filtered_content.append(part)
        else:
            # Keep non-dict parts as-is
            filtered_content.append(part)

    return filtered_content


async def clean_conversation_history(conversation):
    # Remove expired image URLs from conversation history
    cleaned_conversation = []

    for message in conversation:
        if isinstance(message, dict):
            cleaned_message = message.copy()

            # Check if message content contains multimodal data with images
            content = cleaned_message.get("content")
            if isinstance(content, list):
                cleaned_content = await filter_expired_images_from_content(content)
                cleaned_message["content"] = cleaned_content

            cleaned_conversation.append(cleaned_message)
        else:
            cleaned_conversation.append(message)

    return cleaned_conversation


# Multimodal content helpers
async def build_multimodal_content(message):
    # Build multimodal content from a Discord message
    content = []
    if message.reference and message.reference.resolved:
        replied = message.reference.resolved
        if replied.content:
            # Make it clearer that this is a separate entity being replied to
            user_info = (
                f"User: {replied.author.display_name} "
                f"(username: {replied.author.name}#{replied.author.discriminator}, "
                f"id: {replied.author.id})"
            )
            content.append({"type": "text", "text": f"(This message is replying to a message from {user_info}. The original message was: {replied.content})"})
        for attachment in replied.attachments:
            if attachment.content_type and attachment.content_type.startswith("image"):
                url = attachment.url
                if not await is_expired_discord_cdn_url(url):
                    content.append({"type": "image_url", "image_url": {"url": url}})
    if message.content:
        content.append({"type": "text", "text": message.content})
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image"):
            url = attachment.url
            if not await is_expired_discord_cdn_url(url):
                content.append({"type": "image_url", "image_url": {"url": url}})
    return content
