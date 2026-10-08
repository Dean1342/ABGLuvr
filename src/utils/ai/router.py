# Deterministic routing for requests that don't need the LLM.
#
# Right now that's a message that is ONLY a currency conversion ("50 usd to eur",
# "how much is £50 in USD"). Matching is deliberately strict, so the whole message must be
# the conversion. Anything fuzzier ("comparing a $70k car to a 60k euro car") goes to
# the agent, which still has the convert_currency tool.
import re

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₩": "KRW", "₹": "INR"}

_WORDS = {
    "dollar": "USD", "dollars": "USD", "buck": "USD", "bucks": "USD",
    "us dollars": "USD", "american dollars": "USD",
    "euro": "EUR", "euros": "EUR",
    "pound": "GBP", "pounds": "GBP", "quid": "GBP",
    "yen": "JPY", "yuan": "CNY", "rmb": "CNY", "won": "KRW",
    "rupee": "INR", "rupees": "INR", "peso": "MXN", "pesos": "MXN",
    "franc": "CHF", "francs": "CHF", "dong": "VND",
    "canadian dollars": "CAD", "canadian dollar": "CAD",
    "australian dollars": "AUD", "australian dollar": "AUD", "aussie dollars": "AUD",
}

_CODES = {
    "USD", "EUR", "GBP", "JPY", "CNY", "KRW", "INR", "MXN", "CHF", "CAD", "AUD", "NZD",
    "HKD", "SGD", "TWD", "THB", "VND", "PHP", "IDR", "MYR", "SEK", "NOK", "DKK", "PLN",
    "CZK", "HUF", "TRY", "BRL", "ARS", "CLP", "COP", "PEN", "ZAR", "AED", "SAR", "ILS",
    "RUB", "UAH", "EGP", "NGN", "KES", "PKR", "BDT",
}

_CURRENCY = r"(?:[a-z]{3}|" + "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True)) + r")"
_SYMBOL = "[" + "".join(re.escape(s) for s in _SYMBOLS) + "]"
_NUMBER = r"\d[\d,]*(?:\.\d+)?\s*[km]?"

_PATTERN = re.compile(
    r"^(?:(?:convert|change)\s+|(?:what(?:'s|\s+is)|whats|how\s+much\s+is)\s+)?"
    r"(?:"
    rf"(?P<sym>{_SYMBOL})\s*(?P<amt1>{_NUMBER})(?:\s+(?P<cur1>{_CURRENCY}))?"
    "|"
    rf"(?P<amt2>{_NUMBER})\s*(?P<cur2>{_CURRENCY}|{_SYMBOL})"
    r")"
    rf"\s+(?:to|in|into|->)\s+(?P<to>{_CURRENCY}|{_SYMBOL})"
    r"\s*(?:pls|please)?\s*[?.!]*$",
    re.IGNORECASE,
)

_MENTION_RE = re.compile(r"<@!?\d+>")


def _currency_code(token):
    if not token:
        return None
    token = token.strip().lower()
    if token in _SYMBOLS:
        return _SYMBOLS[token]
    if token in _WORDS:
        return _WORDS[token]
    code = token.upper()
    return code if code in _CODES else None


def _amount(raw):
    raw = raw.replace(",", "").replace(" ", "").lower()
    multiplier = 1
    if raw.endswith("k"):
        multiplier, raw = 1_000, raw[:-1]
    elif raw.endswith("m"):
        multiplier, raw = 1_000_000, raw[:-1]
    value = float(raw) * multiplier
    return int(value) if value.is_integer() else value


def match_currency_conversion(text):
    # Return (amount, from_code, to_code) if the whole message is a plain conversion, else None.
    cleaned = " ".join(_MENTION_RE.sub(" ", text or "").split())
    match = _PATTERN.match(cleaned)
    if not match:
        return None

    if match.group("sym"):
        amount_raw = match.group("amt1")
        # "$50 cad to eur": an explicit code after the number beats the symbol
        source = _currency_code(match.group("cur1")) if match.group("cur1") else _currency_code(match.group("sym"))
    else:
        amount_raw = match.group("amt2")
        source = _currency_code(match.group("cur2"))
    target = _currency_code(match.group("to"))

    if not source or not target or source == target:
        return None
    return _amount(amount_raw), source, target
