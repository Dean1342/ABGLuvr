import re
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import json


async def discover_available_seasons(title, year=None, max_seasons=10):
    """Discover how many seasons are available on Rotten Tomatoes by testing season URLs."""
    try:
        # Clean and format the title for URL
        clean_title = re.sub(r'[^\w\s-]', '', title)
        clean_title = re.sub(r'\s+', '_', clean_title.strip())
        clean_title = clean_title.lower()
        
        base_url = "https://www.rottentomatoes.com"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Get multiple title variations for better matching
        title_variations = get_title_variations(title, clean_title)
        
        available_seasons = []
        
        # Test up to max_seasons for each title variation
        for title_var in title_variations:
            found_seasons = []
            
            for season_num in range(1, max_seasons + 1):
                test_url = f"{base_url}/tv/{title_var}/s{season_num:02d}"
                
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(test_url, headers=headers) as response:
                            if response.status == 200:
                                # Check if this is actually a season page (not a redirect or error)
                                html = await response.text()
                                if f"Season {season_num}" in html or f"s{season_num:02d}" in html.lower():
                                    found_seasons.append(season_num)
                            elif response.status == 404:
                                # If we hit a 404, no more seasons for this title variation
                                break
                except:
                    # Skip failed requests
                    continue
                
                # Add small delay to be respectful
                await asyncio.sleep(0.1)
            
            # If we found seasons with this title variation, use it
            if found_seasons:
                available_seasons = found_seasons
                break
        
        return available_seasons
        
    except Exception as e:
        return []


def get_title_variations(original_title, clean_title):
    """Generate multiple title variations for better URL matching"""
    variations = [clean_title]
    
    # Handle ampersands specifically
    if '&' in original_title:
        # Convert & to 'and' with underscores
        ampersand_version = original_title.replace('&', 'and')
        ampersand_version = re.sub(r'[^\w\s-]', '', ampersand_version)
        ampersand_version = re.sub(r'\s+', '_', ampersand_version.strip()).lower()
        if ampersand_version not in variations:
            variations.append(ampersand_version)
        
        # Convert & to 'and' with hyphens
        ampersand_hyphen = original_title.replace('&', 'and')
        ampersand_hyphen = re.sub(r'[^\w\s-]', '', ampersand_hyphen)
        ampersand_hyphen = re.sub(r'\s+', '-', ampersand_hyphen.strip()).lower()
        if ampersand_hyphen not in variations:
            variations.append(ampersand_hyphen)
    
    # Try with hyphens instead of underscores
    hyphen_version = clean_title.replace('_', '-')
    if hyphen_version not in variations:
        variations.append(hyphen_version)
    
    # Try without spaces/separators
    no_separator_version = clean_title.replace('_', '').replace('-', '')
    if no_separator_version not in variations:
        variations.append(no_separator_version)
    
    # Try with "the" removed if it starts with "the_"
    if clean_title.startswith('the_'):
        no_the_version = clean_title[4:]  # Remove "the_"
        if no_the_version not in variations:
            variations.append(no_the_version)
    
    return variations


async def get_rotten_tomatoes_scores(title, year=None, is_tv=False, season=None):
    """Get Rotten Tomatoes scores for a movie, TV show, or specific season."""
    try:
        # Clean and format the title for URL
        clean_title = re.sub(r'[^\w\s-]', '', title)
        clean_title = re.sub(r'\s+', '_', clean_title.strip())
        clean_title = clean_title.lower()
        
        # Get multiple title variations for better matching
        title_variations = get_title_variations(title, clean_title)
        
        # Construct potential URLs
        base_url = "https://www.rottentomatoes.com"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Try different URL variations for each title variation
        url_variations = []
        
        for title_var in title_variations:
            if is_tv:
                if season:
                    # Season-specific URLs
                    urls = [
                        f"{base_url}/tv/{title_var}/s{season:02d}",
                        f"{base_url}/tv/{title_var}_{year}/s{season:02d}" if year else None,
                    ]
                    url_variations.extend([url for url in urls if url])
                else:
                    # Show-level URLs
                    urls = [
                        f"{base_url}/tv/{title_var}_{year}" if year else f"{base_url}/tv/{title_var}",
                        f"{base_url}/tv/{title_var}",
                    ]
                    url_variations.extend(urls)
            else:
                urls = [
                    f"{base_url}/m/{title_var}_{year}" if year else f"{base_url}/m/{title_var}",
                    f"{base_url}/m/{title_var}",
                ]
                url_variations.extend(urls)
        
        # Remove duplicates while preserving order
        seen = set()
        url_variations = [url for url in url_variations if url not in seen and not seen.add(url)]
        
        # Try each URL variation
        for rt_url in url_variations:
            scores = await _scrape_scores_from_url(rt_url, headers, title)
            if scores:
                scores['url'] = rt_url  # Add the successful URL to the result
                return scores
        
        return None
        
    except Exception as e:
        # Silently handle errors to avoid console spam
        return None


async def _scrape_scores_from_url(rt_url, headers, title):
    """Extract Tomatometer and Popcorn Meter scores from a Rotten Tomatoes page."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(rt_url, headers=headers) as response:
                if response.status != 200:
                    return None
                
                html = await response.text()
                soup = BeautifulSoup(html, 'html.parser')
                
                scores = {}
                
                # Method 1: Look for score-board web component (most reliable for newer pages)
                score_board = soup.find('score-board')
                if score_board:
                    tomatometer = score_board.get('tomatometerscore')
                    audience = score_board.get('audiencescore')
                    
                    if tomatometer and tomatometer.isdigit():
                        scores['tomatometer'] = int(tomatometer)
                    if audience and audience.isdigit():
                        scores['popcornmeter'] = int(audience)
                    
                    if scores:
                        return scores
                
                # Method 1.5: Look for JSON data structure (reliable for current RT pages)
                json_pattern = r'"audienceScore":\s*({[^}]+})[^"]*"criticsScore":\s*({[^}]+})'
                json_match = re.search(json_pattern, html)
                if json_match:
                    try:
                        audience_data = json_match.group(1)
                        critics_data = json_match.group(2)
                        
                        # Extract critics score
                        critics_score_match = re.search(r'"score":\s*"(\d+)"', critics_data)
                        if critics_score_match:
                            scores['tomatometer'] = int(critics_score_match.group(1))
                        
                        # Extract audience score - be more permissive for TV shows
                        audience_score_patterns = [
                            r'"score":\s*"(\d+)"',
                            r'"scorePercent":\s*"(\d+)%"'
                        ]
                        for pattern in audience_score_patterns:
                            audience_score_match = re.search(pattern, audience_data)
                            if audience_score_match:
                                scores['popcornmeter'] = int(audience_score_match.group(1))
                                break
                        
                        if scores:
                            return scores
                    except (ValueError, IndexError):
                        pass
                
                # Method 2: Look for main movie scores vs recommendation scores
                # Key insight: Main movie scores are in rt-text elements WITHOUT 'critics-score' class
                # Recommendation scores have the 'critics-score' class
                rt_texts = soup.find_all('rt-text')
                
                main_movie_scores = []
                
                for rt in rt_texts:
                    text = rt.get_text(strip=True)
                    if text.endswith('%') and text[:-1].isdigit():
                        score = int(text[:-1])
                        if 30 <= score <= 100:  # Valid score range
                            classes = rt.get('class', [])
                            
                            # Main movie scores typically don't have 'critics-score' class
                            if 'critics-score' not in classes:
                                main_movie_scores.append(score)
                
                if main_movie_scores:
                    # Typically first score is critics/tomatometer, second is audience/popcornmeter
                    if len(main_movie_scores) >= 1:
                        scores['tomatometer'] = main_movie_scores[0]
                    if len(main_movie_scores) >= 2:
                        scores['popcornmeter'] = main_movie_scores[1]
                    
                    if scores:
                        return scores
                
                # Method 3: Split HTML to avoid recommendations sections (fallback)
                html_parts = html.split('data-track="more_like_this"')
                main_html = html_parts[0] if html_parts else html
                
                # Also split on other recommendation indicators
                recommendation_markers = [
                    'More Like This',
                    'You might also like',
                    'data-module="MoreLikeThis"',
                    'similar-movies',
                    'recommendations',
                    'related-content'
                ]
                
                for marker in recommendation_markers:
                    main_html = main_html.split(marker)[0]
                
                # Look for critics score in the main content only
                critics_patterns = [
                    r'"criticsScore"[^}]*"score"[^"]*"(\d+)"',
                    r'"title"[^"]*"Tomatometer"[^}]*"score"[^"]*"(\d+)"',
                    r'"scoreText"[^"]*"(\d+)%"[^}]*"Tomatometer"',
                    r'"tomatometer"[^}]*"score"[^"]*"(\d+)"',
                    r'"tomatometer":(\d+)',
                ]
                
                audience_patterns = [
                    r'"audienceScore"[^}]*"score"[^"]*"(\d+)"',
                    r'"title"[^"]*"Popcornmeter"[^}]*"score"[^"]*"(\d+)"',
                    r'"scoreText"[^"]*"(\d+)%"[^}]*"Popcornmeter"',
                    r'"popcornmeter"[^}]*"score"[^"]*"(\d+)"',
                    r'"audienceScore":(\d+)',
                ]
                
                # Try to find critics score (first match only from main content)
                for pattern in critics_patterns:
                    match = re.search(pattern, main_html)
                    if match:
                        score = int(match.group(1))
                        if 0 <= score <= 100:
                            scores['tomatometer'] = score
                            break
                
                # Try to find audience score (first match only from main content)
                # For TV shows, be less restrictive about review count requirements
                audience_reviews_available = True  # Default to True for more permissive extraction
                
                # Check if audience reviews exist in the JSON data
                audience_json_pattern = r'"audienceScore":[^}]*"reviewCount":\s*(\d+)'
                audience_reviews_match = re.search(audience_json_pattern, main_html)
                if audience_reviews_match:
                    review_count = int(audience_reviews_match.group(1))
                    audience_reviews_available = review_count > 0
                
                # Look for audience score (more permissive for TV shows)
                if audience_reviews_available:
                    for pattern in audience_patterns:
                        match = re.search(pattern, main_html)
                        if match:
                            score = int(match.group(1))
                            if 0 <= score <= 100:
                                scores['popcornmeter'] = score
                                break
                
                if scores:
                    return scores
                
                return None
                
    except Exception as e:
        # Silently handle errors to avoid console spam
        return None


def format_rt_scores(scores):
    """Format Rotten Tomatoes scores for display."""
    if not scores:
        return "🍅 N/A | 🍿 N/A"
    
    tomatometer = scores.get('tomatometer', 'N/A')
    popcornmeter = scores.get('popcornmeter', 'N/A')
    
    # Format with emojis
    tomato_emoji = "🍅"
    popcorn_emoji = "🍿"
    
    tomato_text = f"{tomatometer}%" if tomatometer != 'N/A' else 'N/A'
    popcorn_text = f"{popcornmeter}%" if popcornmeter != 'N/A' else 'N/A'
    
    return f"{tomato_emoji} {tomato_text} | {popcorn_emoji} {popcorn_text}"


# Test function
async def test_rt_scraper():
    """Test the Rotten Tomatoes scraper."""
    test_cases = [
        ("Ballerina", 2025, False),
        ("Inception", 2010, False),
        ("The Dark Knight", 2008, False),
    ]
    
    print("Testing Rotten Tomatoes scraper...")
    
    for title, year, is_tv in test_cases:
        print(f"\n{'='*50}")
        print(f"Testing: {title} ({year}) - {'TV' if is_tv else 'Movie'}")
        print(f"{'='*50}")
        scores = await get_rotten_tomatoes_scores(title, year, is_tv)
        if scores:
            print(f"Final scores: {scores}")
            print(f"Formatted: {format_rt_scores(scores)}")
        else:
            print("No scores found")


if __name__ == "__main__":
    asyncio.run(test_rt_scraper())
