import re

# Provides text formatting and uncensoring helpers
def format_discord_links(text):
    url_pattern = re.compile(r'https?://[^\s<>()]+')
    return url_pattern.sub(lambda m: f"<{m.group(0)}>", text)

def uncensor_response(response: str) -> str:
    words = [
        "fuck", "fucking", "shit", "bitch", "dick", "ass", "pussy", "cunt", "twat", "bastard", "slut", "whore"
    ]
    censor_chars = r"[\*\#\@\$\%\!\-\_ ]*"
    patterns = []
    for word in words:
        pattern = re.compile(
            r"\b" + "".join([c + censor_chars for c in word[:-1]]) + word[-1] + r"\b",
            re.IGNORECASE
        )
        patterns.append((pattern, word))
    uncensored = response
    for pattern, replacement in patterns:
        uncensored = pattern.sub(
            lambda m: replacement if m.group(0).islower() else replacement.capitalize(),
            uncensored
        )
    return uncensored

def fix_social_media_links(text):
    """
    Fix social media links to use proper Discord embed services.
    Converts X.com, TikTok, and Instagram links to their embed-friendly versions.
    """
    if not text:
        return text, False
    
    original_text = text
    changed = False
    
    # Check if already has fixed links - if so, don't process
    fixed_patterns = [
        r'https?://fixupx\.com',
        r'https?://(?:www\.)?tnktok\.com',
        r'https?://eeinstagram\.com',
    ]
    
    for pattern in fixed_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return text, False
    
    # X.com / Twitter link patterns (various formats)
    x_patterns = [
        r'https?://(?:www\.)?(x\.com|twitter\.com)/([^?\s]+)(?:\?[^?\s]*)?',
        r'https?://(?:mobile\.)?(x\.com|twitter\.com)/([^?\s]+)(?:\?[^?\s]*)?'
    ]
    
    for pattern in x_patterns:
        def replace_x_link(match):
            nonlocal changed
            changed = True
            path = match.group(2)
            # Clean up path - remove trailing slashes and query params
            path = path.rstrip('/')
            return f"https://fixupx.com/{path}"
        
        text = re.sub(pattern, replace_x_link, text, flags=re.IGNORECASE)
    
    # TikTok link patterns (various regional formats)
    # Handle vt./vm. patterns first (they need special handling)
    tiktok_vm_patterns = [
        r'https?://(vm\.|vt\.)tiktok\.com/([^?\s]+)(?:\?[^?\s]*)?',
    ]
    
    for pattern in tiktok_vm_patterns:
        def replace_tiktok_vm_link(match):
            nonlocal changed
            changed = True
            path = match.group(2)
            # Clean up path
            path = path.rstrip('/')
            return f"https://www.tnktok.com/t/{path}"
        
        text = re.sub(pattern, replace_tiktok_vm_link, text, flags=re.IGNORECASE)
    
    # Handle regular TikTok patterns
    tiktok_patterns = [
        r'https?://(?:www\.|m\.)?tiktok\.com/([^?\s]+)(?:\?[^?\s]*)?',
        r'https?://(?:www\.)?tiktok\.com/(@[^/]+/video/\d+)',
    ]
    
    for pattern in tiktok_patterns:
        def replace_tiktok_link(match):
            nonlocal changed
            changed = True
            path = match.group(1)
            # Clean up path
            path = path.rstrip('/')
            return f"https://www.tnktok.com/{path}"
        
        text = re.sub(pattern, replace_tiktok_link, text, flags=re.IGNORECASE)
    
    # Instagram link patterns - only fix reel links, not profile links
    instagram_patterns = [
        r'https?://(?:www\.|m\.)?instagram\.com/((?:reel|p|tv)/[^?\s]+)(?:\?[^?\s]*)?',
    ]
    
    for pattern in instagram_patterns:
        def replace_instagram_link(match):
            nonlocal changed
            changed = True
            path = match.group(1)
            # Clean up path
            path = path.rstrip('/')
            return f"https://eeinstagram.com/{path}"
        
        text = re.sub(pattern, replace_instagram_link, text, flags=re.IGNORECASE)
    
    # Also fix other embed fixing services to use our preferred ones
    # Fix vx.com, fx.com, etc. for X/Twitter
    other_x_fixes = [
        r'https?://(?:vx|fx|twittpr|nitter)\.[\w.]+/([^?\s]+)(?:\?[^?\s]*)?'
    ]
    
    for pattern in other_x_fixes:
        def replace_other_x_fix(match):
            nonlocal changed
            changed = True
            path = match.group(1)
            path = path.rstrip('/')
            return f"https://fixupx.com/{path}"
        
        text = re.sub(pattern, replace_other_x_fix, text, flags=re.IGNORECASE)
    
    return text, changed

def contains_social_media_links(text):
    """
    Check if text contains social media links that might need fixing.
    """
    if not text:
        return False
    
    # Patterns to detect social media links that need fixing
    social_patterns = [
        r'https?://(?:www\.|mobile\.|m\.|vm\.|vt\.)?(?:x\.com|twitter\.com)',
        r'https?://(?:www\.|vm\.|vt\.)?tiktok\.com',
        r'https?://(?:www\.|m\.)?instagram\.com/(?:reel|p|tv)/',  # Only detect reel/post/tv links
        r'https?://(?:vx|fx|twittpr|nitter)\.[\w.]+',
    ]
    
    # Patterns to detect already-fixed links (don't process these)
    fixed_patterns = [
        r'https?://fixupx\.com',
        r'https?://(?:www\.)?tnktok\.com',
        r'https?://eeinstagram\.com',
        r'https?://(?:www\.)?ddinstagram\.com',  # Legacy support
    ]
    
    # Check if any fixed links exist - if so, don't process
    for pattern in fixed_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return False
    
    # Check for unfixed social media links
    for pattern in social_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    return False

def contains_user_mentions(text):
    """
    Check if text contains user mentions (@user or <@userid>).
    """
    if not text:
        return False
    
    # Pattern to detect Discord user mentions
    mention_patterns = [
        r'<@!?\d+>',  # Discord user mention format <@123456789> or <@!123456789>
        r'@\w+',      # Simple @username format
    ]
    
    for pattern in mention_patterns:
        if re.search(pattern, text):
            return True
    
    return False

def remove_mentions_from_text(text):
    """
    Temporarily remove mentions from text to prevent pings.
    Returns the text without mentions and the original text.
    """
    if not text:
        return text, text
    
    # Replace Discord user mentions with placeholder
    mention_pattern = r'<@!?\d+>'
    no_mentions_text = re.sub(mention_pattern, '[mention]', text)
    
    # Also replace @username style mentions
    username_pattern = r'@\w+'
    no_mentions_text = re.sub(username_pattern, '[mention]', no_mentions_text)
    
    return no_mentions_text, text
