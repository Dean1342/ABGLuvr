import re
import asyncio
import aiohttp
from bs4 import BeautifulSoup
import json


async def get_rotten_tomatoes_scores(title, year=None, is_tv=False, season=None):
    """Get Rotten Tomatoes scores for a movie, TV show, or specific season."""
    try:
        # Clean and format the title for URL
        clean_title = re.sub(r'[^\w\s-]', '', title)
        clean_title = re.sub(r'\s+', '_', clean_title.strip())
        clean_title = clean_title.lower()
        
        # Construct potential URLs
        base_url = "https://www.rottentomatoes.com"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Try different URL variations (prioritize year-specific URLs)
        url_variations = []
        
        if is_tv:
            if season:
                # Season-specific URLs
                url_variations = [
                    f"{base_url}/tv/{clean_title}/s{season:02d}",
                    f"{base_url}/tv/{clean_title}_{year}/s{season:02d}" if year else None,
                    f"{base_url}/tv/{clean_title.replace('_', '-')}/s{season:02d}",
                    f"{base_url}/tv/{clean_title.replace('_', '')}/s{season:02d}",
                ]
                # Remove None values
                url_variations = [url for url in url_variations if url]
            else:
                # Show-level URLs
                url_variations = [
                    f"{base_url}/tv/{clean_title}_{year}" if year else f"{base_url}/tv/{clean_title}",
                    f"{base_url}/tv/{clean_title}",
                    f"{base_url}/tv/{clean_title.replace('_', '-')}",
                    f"{base_url}/tv/{clean_title.replace('_', '')}",
                ]
        else:
            url_variations = [
                f"{base_url}/m/{clean_title}_{year}" if year else f"{base_url}/m/{clean_title}",
                f"{base_url}/m/{clean_title}",
                f"{base_url}/m/{clean_title.replace('_', '-')}",
                f"{base_url}/m/{clean_title.replace('_', '')}",
            ]
        
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
