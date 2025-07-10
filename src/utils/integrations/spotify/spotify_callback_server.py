from dotenv import load_dotenv
from flask import Flask, request
import requests
import time
# Handles Spotify OAuth callback and token exchange
from utils.integrations.spotify.spotify import (
    exchange_code_for_token, store_spotify_tokens,
    SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REDIRECT_URI
)

app = Flask(__name__)

@app.route('/callback')
def spotify_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if code and state:
        tokens = exchange_code_for_token(code)
        if tokens:
            store_spotify_tokens(state, tokens)
            return "Spotify account linked! You can close this page."
        else:
            return "Failed to exchange code for tokens."
    return "Missing code or state."

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

if __name__ == '__main__':
    app.run(port=8080)