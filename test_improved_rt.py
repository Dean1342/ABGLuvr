#!/usr/bin/env python3
"""
Test the improved Rotten Tomatoes function
"""
import asyncio
import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from utils.integrations.rottentomatoes import get_rotten_tomatoes_scores, format_rt_scores

async def test_improved_rt():
    """Test the improved RT function with problematic titles"""
    
    test_cases = [
        ("Superman & Lois", 2021, True),
        ("Invincible", 2021, True),
        ("Superman", 2025, False),
    ]
    
    for title, year, is_tv in test_cases:
        print(f"\n{'='*60}")
        print(f"Testing: {title} ({year}) - {'TV' if is_tv else 'Movie'}")
        print(f"{'='*60}")
        
        scores = await get_rotten_tomatoes_scores(title, year, is_tv)
        
        if scores:
            print(f"✅ SUCCESS!")
            print(f"URL: {scores.get('url', 'N/A')}")
            print(f"Formatted: {format_rt_scores(scores)}")
        else:
            print(f"❌ FAILED - No scores found")

if __name__ == "__main__":
    asyncio.run(test_improved_rt())
