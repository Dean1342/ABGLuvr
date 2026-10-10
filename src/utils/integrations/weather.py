# Weather from Open-Meteo (open-meteo.com): geocoding + forecast, no API key.
#
# Free tier terms (checked 2026-10-09): non-commercial use, which covers a private bot
# with no ads or subscriptions; under 10,000 calls a day; data is CC BY 4.0, so answers
# credit Open-Meteo (the get_weather tool makes it a cited source).
#
# The model only explains and picks from what's returned here; every number comes from
# the provider. No member locations are stored: the place is whatever the question names.
import unicodedata

import httpx

from utils.links.cache import TTLCache

GEOCODE_API = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION_URL = "https://open-meteo.com/"
MAX_DAYS = 7
_geo_cache = TTLCache(ttl=24 * 3600, failure_ttl=5 * 60)
_forecast_cache = TTLCache(ttl=15 * 60, failure_ttl=2 * 60)

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan", "YT": "Yukon",
}
REGION_CODES = {**CA_PROVINCES, **US_STATES}  # "CA" is California (US abbreviations win)
# Countries that use °F / mph / inches by default.
IMPERIAL = {"US", "LR", "MM", "PR", "GU", "VI", "AS", "MP"}

# WMO weather interpretation codes (Open-Meteo docs).
WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "light snow showers", 86: "snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


class WeatherError(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code, self.detail = code, detail


async def _get(url, params):
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            resp = await client.get(url, params=params)
    except httpx.HTTPError as e:
        raise WeatherError("failed", f"Couldn't reach the weather service ({type(e).__name__}).")
    if resp.status_code == 429:
        raise WeatherError("rate_limited", "The weather service is rate-limiting the bot right now.")
    if resp.status_code != 200:
        raise WeatherError("failed", f"The weather service returned an error (HTTP {resp.status_code}).")
    return resp.json()


def _plain(text):
    # Compare place names without accents or case: "Québec" == "quebec".
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def _in_region(place, region):
    # Whether a "City, <region>" part (state, province, country, or their abbreviations) fits.
    wanted = _plain(region)
    named = {_plain(place.get(k)) for k in ("admin1", "admin2", "country", "country_code")}
    full = REGION_CODES.get(region.strip().upper())
    return wanted in named or (full is not None and _plain(full) in named)


async def _search(name):
    async def fetch():
        data = await _get(GEOCODE_API, {"name": name, "count": 10, "language": "en", "format": "json"})
        return data.get("results") or []
    return await _geo_cache.get(_plain(name), fetch)


async def find_place(query: str) -> tuple[dict, list[dict]]:
    # (the chosen place, other places with that name). "Sacramento", "Paris, TX", "Paris, France",
    # "Quebec City, Quebec, Canada".
    name, *regions = [part.strip() for part in query.split(",") if part.strip()] or [""]
    if not name:
        raise WeatherError("no_place", "No place was given.")
    results = await _search(name)
    if not results and _plain(name).endswith(" city"):
        results = await _search(name[:-5])  # the geocoder knows "Québec", not "Quebec City"
    for region in regions:
        results = [r for r in results if _in_region(r, region)]
    if not results:
        raise WeatherError("not_found", f"Couldn't find a place called \"{query}\".")
    # Biggest first: "Paris" is Paris, France, not Paris, Texas, unless they say so.
    results = sorted(results, key=lambda r: r.get("population") or 0, reverse=True)
    if str(results[0].get("feature_code", "")).startswith(("PCL", "ADM1")):
        # A whole country or state has no one forecast; the model sometimes fills in "United
        # States" when no place was given.
        raise WeatherError("too_broad", f"\"{query}\" is a whole country or state, not a place with one forecast. "
                                        f"Ask which city they mean.")
    return results[0], results[1:4]


def place_label(place):
    parts = [place.get("name"), place.get("admin1"), place.get("country")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def default_units(place):
    return "imperial" if place.get("country_code") in IMPERIAL else "metric"


async def forecast(place, units: str, days: int) -> dict:
    days = max(1, min(int(days or 3), MAX_DAYS))
    imperial = units == "imperial"
    params = {
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
                   "wind_speed_10m,wind_gusts_10m,is_day",
        "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,"
                 "sunrise,sunset,uv_index_max,wind_speed_10m_max",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
        "precipitation_unit": "inch" if imperial else "mm",
    }
    key = (round(place["latitude"], 3), round(place["longitude"], 3), units, days)
    data = await _forecast_cache.get(key, lambda: _get(FORECAST_API, params))
    print(f"[weather] {place_label(place)}: {days}-day forecast ({units})")
    return data


def describe_code(code):
    return WMO.get(code, f"weather code {code}")


def clear_cache():
    _geo_cache.clear()
    _forecast_cache.clear()

