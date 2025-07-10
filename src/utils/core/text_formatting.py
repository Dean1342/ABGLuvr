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
