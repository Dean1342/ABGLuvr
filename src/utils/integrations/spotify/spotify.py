import os
import requests
import json
import time
from dotenv import load_dotenv
load_dotenv()
from urllib.parse import urlencode

# Spotify API integration and token management

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "your_spotify_client_id")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "your_spotify_client_secret")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
USERS_FILE = os.path.join(os.path.dirname(__file__), "spotify_users.json")

SCOPES = [
    "user-read-currently-playing",
    "user-follow-read",
    "user-top-read",
    "user-read-recently-played",
    "user-library-read"
]

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def store_spotify_tokens(user_id, tokens, username=None):
    users = load_users()
    entry = tokens.copy()
    if username:
        entry["username"] = username
    users[str(user_id)] = entry
    save_users(users)

def get_user_tokens(user_id):
    users = load_users()
    return users.get(str(user_id))

def remove_spotify_tokens(user_id):
    users = load_users()
    if str(user_id) in users:
        del users[str(user_id)]
        save_users(users)

def get_spotify_auth_url(user_id):
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": str(user_id)
    }
    return f"https://accounts.spotify.com/authorize?{urlencode(params)}"

def exchange_code_for_token(code):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET
    }
    resp = requests.post("https://accounts.spotify.com/api/token", data=data)
    if resp.status_code == 200:
        d = resp.json()
        return {
            "access_token": d["access_token"],
            "refresh_token": d["refresh_token"],
            "expires_at": int(time.time()) + d["expires_in"]
        }
    return None

def refresh_user_tokens(user_id, tokens):
    if tokens["expires_at"] > int(time.time()) + 60:
        return tokens
    data = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET
    }
    resp = requests.post("https://accounts.spotify.com/api/token", data=data)
    if resp.status_code == 200:
        d = resp.json()
        tokens["access_token"] = d["access_token"]
        tokens["expires_at"] = int(time.time()) + d["expires_in"]
        store_spotify_tokens(user_id, tokens)
        return tokens
    return None

def spotify_search(query, type_):
    url = f"https://api.spotify.com/v1/search?q={requests.utils.quote(query)}&type={type_}&limit=5"
    token = get_app_access_token()
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 200:
        return resp.json()
    return {"error": resp.text}

def get_app_access_token():
    data = {
        "grant_type": "client_credentials",
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET
    }
    resp = requests.post("https://accounts.spotify.com/api/token", data=data)
    if resp.status_code == 200:
        return resp.json()["access_token"]
    return None

def spotify_user_top(tokens, type_, time_range="long_term", limit=5):
    url = f"https://api.spotify.com/v1/me/top/{type_}?time_range={time_range}&limit={limit}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if resp.status_code == 200:
        return resp.json()
    return {"error": resp.text}

def spotify_user_recent(tokens, limit=5):
    url = f"https://api.spotify.com/v1/me/player/recently-played?limit={limit}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if resp.status_code == 200:
        return resp.json()
    return {"error": resp.text}

def spotify_user_profile(tokens):
    url = "https://api.spotify.com/v1/me"
    resp = requests.get(url, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if resp.status_code == 200:
        return resp.json()
    return {"error": resp.text}

def spotify_user_recommend(tokens):
    top = spotify_user_top(tokens, "artists")
    if "items" in top and top["items"]:
        seed = top["items"][0]["id"]
        url = f"https://api.spotify.com/v1/recommendations?seed_artists={seed}&limit=5"
        resp = requests.get(url, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if resp.status_code == 200:
            return resp.json()
        return {"error": resp.text}
    return {"error": "No top artists found for recommendations."}

def format_spotify_search_result(result, type_):
    plural_type = type_ + "s"
    if not result or plural_type not in result or "items" not in result[plural_type] or not result[plural_type]["items"]:
        return "No results found."
    items = result[plural_type]["items"]
    if type_ == "artist":
        main = items[0]
        msg = f"**{main['name']}**\n"
        if main.get("genres"):
            msg += f"Genres: {', '.join(main['genres'])}\n"
        msg += f"Popularity: {main.get('popularity', 'N/A')}/100\n"
        msg += f"Followers: {main.get('followers', {}).get('total', 'N/A')}\n"
        msg += f"[Spotify Profile]({main['external_urls']['spotify']})\n"
        if len(items) > 1:
            msg += "\nSimilar artists:\n"
            for a in items[1:4]:
                msg += f"- [{a['name']}]({a['external_urls']['spotify']})\n"
        return msg.strip()
    elif type_ == "track":
        main = items[0]
        artists = ", ".join([a["name"] for a in main["artists"]])
        msg = f"**{main['name']}** by {artists}\n"
        msg += f"Album: {main['album']['name']}\n"
        msg += f"Popularity: {main.get('popularity', 'N/A')}/100\n"
        msg += f"[Spotify Track]({main['external_urls']['spotify']})\n"
        return msg.strip()
    elif type_ == "album":
        main = items[0]
        artists = ", ".join([a["name"] for a in main["artists"]])
        msg = f"**{main['name']}** by {artists}\n"
        msg += f"Release Date: {main.get('release_date', 'N/A')}\n"
        msg += f"Total Tracks: {main.get('total_tracks', 'N/A')}\n"
        msg += f"[Spotify Album]({main['external_urls']['spotify']})\n"
        return msg.strip()
    else:
        return str(items[0])

def format_json_response(data, indent=0):
    INDENT_STR = "    "
    if isinstance(data, dict):
        lines = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{INDENT_STR * indent}**{key}:**")
                lines.append(format_json_response(value, indent + 1))
            else:
                lines.append(f"{INDENT_STR * indent}**{key}:** {value}")
        return "\n".join(lines)
    elif isinstance(data, list):
        lines = []
        for idx, item in enumerate(data, 1):
            if isinstance(item, (dict, list)):
                lines.append(f"{INDENT_STR * indent}- {format_json_response(item, indent + 1)}")
            else:
                lines.append(f"{INDENT_STR * indent}- {item}")
        return "\n".join(lines)
    else:
        return f"{INDENT_STR * indent}{data}"

def spotify_artist_top_tracks(artist_id, market="US"):
    url = f"https://api.spotify.com/v1/artists/{artist_id}/top-tracks?market={market}"
    token = get_app_access_token()
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 200:
        return resp.json().get("tracks", [])
    return []
