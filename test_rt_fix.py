#!/usr/bin/env python3
import asyncio
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from utils.integrations.rottentomatoes import get_rotten_tomatoes_scores, format_rt_scores

async def test_tv_shows():
    """Test TV shows that should have audience scores."""
    test_shows = [
        ("The Office", 2005, True),
        ("Money Heist", 2017, True),
        ("Breaking Bad", 2008, True)
    ]
    
    print("Testing TV show audience score extraction...")
    
    for title, year, is_tv in test_shows:
        print(f"\n{'='*60}")
        print(f"Testing: {title} ({year})")
        print(f"{'='*60}")
        
        scores = await get_rotten_tomatoes_scores(title, year, is_tv)
        if scores:
            print(f"Raw scores: {scores}")
            print(f"Formatted: {format_rt_scores(scores)}")
            
            # Check if we got both scores
            has_critics = 'tomatometer' in scores and scores['tomatometer'] != 'N/A'
            has_audience = 'popcornmeter' in scores and scores['popcornmeter'] != 'N/A'
            
            print(f"Critics score found: {has_critics}")
            print(f"Audience score found: {has_audience}")
            
            if has_audience:
                print(f"✅ SUCCESS: Found audience score for {title}")
            else:
                print(f"❌ FAILED: No audience score for {title}")
        else:
            print(f"❌ FAILED: No scores found for {title}")

if __name__ == "__main__":
    asyncio.run(test_tv_shows())
