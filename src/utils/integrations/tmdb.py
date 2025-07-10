import requests
import os
from dotenv import load_dotenv

load_dotenv()

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY must be set in the environment!")

def search_movie_or_tv(query, media_type="multi"):
    """Search for movies or TV shows"""
    url = f"{TMDB_BASE_URL}/search/{media_type}"
    params = {
        "api_key": TMDB_API_KEY,
        "query": query,
        "language": "en-US"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
    except Exception as e:
        print(f"TMDb search error: {e}")
        return []

def get_movie_details(movie_id):
    """Get detailed movie information"""
    url = f"{TMDB_BASE_URL}/movie/{movie_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "credits,external_ids"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"TMDb movie details error: {e}")
        return None

def get_tv_details(tv_id):
    """Get detailed TV show information"""
    url = f"{TMDB_BASE_URL}/tv/{tv_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "credits,external_ids"
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"TMDb TV details error: {e}")
        return None

def format_runtime(runtime_minutes):
    """Convert runtime from minutes to hours and minutes"""
    if not runtime_minutes:
        return "Unknown"
    hours = runtime_minutes // 60
    minutes = runtime_minutes % 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

def get_poster_url(poster_path):
    """Get full poster URL"""
    if poster_path:
        return f"{TMDB_IMAGE_BASE_URL}{poster_path}"
    return None

def get_imdb_url(imdb_id):
    """Get IMDb URL from IMDb ID"""
    if imdb_id:
        return f"https://www.imdb.com/title/{imdb_id}/"
    return None

def get_tmdb_url(media_type, tmdb_id):
    """Get TMDb URL"""
    if media_type == "movie":
        return f"https://www.themoviedb.org/movie/{tmdb_id}"
    elif media_type == "tv":
        return f"https://www.themoviedb.org/tv/{tmdb_id}"
    return None
