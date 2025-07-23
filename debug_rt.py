#!/usr/bin/env python3
"""
Debug script for Rotten Tomatoes URL formatting
"""
import asyncio
import re
import aiohttp
from bs4 import BeautifulSoup

async def debug_rt_urls(title, year=None, is_tv=True):
    """Debug RT URL construction for specific titles"""
    
    print(f"Debugging RT URLs for: '{title}' (year: {year}, is_tv: {is_tv})")
    print("=" * 60)
    
    # Current cleaning logic
    clean_title = re.sub(r'[^\w\s-]', '', title)
    clean_title = re.sub(r'\s+', '_', clean_title.strip())
    clean_title = clean_title.lower()
    
    print(f"Current cleaned title: '{clean_title}'")
    
    # Try different cleaning approaches
    alternatives = []
    
    # Alternative 1: Keep ampersands as 'and'
    alt1 = title.replace('&', 'and')
    alt1 = re.sub(r'[^\w\s-]', '', alt1)
    alt1 = re.sub(r'\s+', '_', alt1.strip()).lower()
    alternatives.append(("ampersand as 'and'", alt1))
    
    # Alternative 2: Remove ampersands completely
    alt2 = title.replace('&', '')
    alt2 = re.sub(r'[^\w\s-]', '', alt2)
    alt2 = re.sub(r'\s+', '_', alt2.strip()).lower()
    alternatives.append(("remove ampersand", alt2))
    
    # Alternative 3: Use hyphens instead of underscores
    alt3 = re.sub(r'[^\w\s-]', '', title)
    alt3 = re.sub(r'\s+', '-', alt3.strip()).lower()
    alternatives.append(("hyphens instead of underscores", alt3))
    
    # Alternative 4: Ampersand as 'and' with hyphens
    alt4 = title.replace('&', 'and')
    alt4 = re.sub(r'[^\w\s-]', '', alt4)
    alt4 = re.sub(r'\s+', '-', alt4.strip()).lower()
    alternatives.append(("ampersand as 'and' with hyphens", alt4))
    
    base_url = "https://www.rottentomatoes.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # Test all variations
    all_variations = [("current", clean_title)] + alternatives
    
    for desc, variant in all_variations:
        print(f"\nTesting variant: {desc} -> '{variant}'")
        
        if is_tv:
            test_urls = [
                f"{base_url}/tv/{variant}_{year}" if year else f"{base_url}/tv/{variant}",
                f"{base_url}/tv/{variant}",
            ]
        else:
            test_urls = [
                f"{base_url}/m/{variant}_{year}" if year else f"{base_url}/m/{variant}",
                f"{base_url}/m/{variant}",
            ]
        
        for url in test_urls:
            print(f"  Testing URL: {url}")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        print(f"    Status: {response.status}")
                        if response.status == 200:
                            html = await response.text()
                            soup = BeautifulSoup(html, 'html.parser')
                            
                            # Check for score-board
                            score_board = soup.find('score-board')
                            if score_board:
                                tomatometer = score_board.get('tomatometerscore')
                                audience = score_board.get('audiencescore')
                                print(f"    ✅ FOUND SCORES: Tomatometer: {tomatometer}, Audience: {audience}")
                                return url  # Return successful URL
                            else:
                                # Check title to see if we're on the right page
                                title_elem = soup.find('title')
                                page_title = title_elem.get_text() if title_elem else "No title"
                                print(f"    Page title: {page_title[:100]}...")
                        elif response.status == 404:
                            print(f"    ❌ Not found (404)")
                        else:
                            print(f"    ⚠️  Unexpected status")
            except Exception as e:
                print(f"    ❌ Error: {str(e)[:50]}...")
            
            await asyncio.sleep(0.5)  # Be respectful
    
    print("\n❌ No working URL found")
    return None

async def main():
    # Test the problematic title
    await debug_rt_urls("Superman & Lois", 2021, True)
    
    print("\n" + "="*60)
    print("For comparison, testing a working title:")
    await debug_rt_urls("Invincible", 2021, True)

if __name__ == "__main__":
    asyncio.run(main())
