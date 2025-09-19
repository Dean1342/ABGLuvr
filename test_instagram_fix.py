#!/usr/bin/env python3
"""Test script for the updated Instagram link fixing functionality."""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from utils.core.text_formatting import fix_social_media_links, contains_social_media_links

def test_instagram_fixes():
    """Test the Instagram link fixing functionality."""
    
    # Test cases
    test_cases = [
        # Should be fixed (reel links)
        ("https://www.instagram.com/reel/DOxUQo6EVkw", "https://eeinstagram.com/reel/DOxUQo6EVkw", True),
        ("https://instagram.com/reel/DOxUQo6EVkw", "https://eeinstagram.com/reel/DOxUQo6EVkw", True),
        ("https://m.instagram.com/reel/DOxUQo6EVkw", "https://eeinstagram.com/reel/DOxUQo6EVkw", True),
        ("https://www.instagram.com/p/DOxUQo6EVkw", "https://eeinstagram.com/p/DOxUQo6EVkw", True),
        ("https://www.instagram.com/tv/DOxUQo6EVkw", "https://eeinstagram.com/tv/DOxUQo6EVkw", True),
        
        # Should NOT be fixed (profile links)
        ("https://www.instagram.com/username", "https://www.instagram.com/username", False),
        ("https://instagram.com/some_user", "https://instagram.com/some_user", False),
        
        # Already fixed links should not be processed
        ("https://eeinstagram.com/reel/DOxUQo6EVkw", "https://eeinstagram.com/reel/DOxUQo6EVkw", False),
    ]
    
    print("Testing Instagram link fixing...")
    print("=" * 50)
    
    for i, (input_text, expected_output, should_change) in enumerate(test_cases, 1):
        print(f"\nTest {i}:")
        print(f"Input:    {input_text}")
        print(f"Expected: {expected_output}")
        
        # Test detection
        detected = contains_social_media_links(input_text)
        print(f"Detected: {detected} (should be {should_change})")
        
        # Test fixing
        result, changed = fix_social_media_links(input_text)
        print(f"Result:   {result}")
        print(f"Changed:  {changed} (should be {should_change})")
        
        # Verify results
        if result == expected_output and changed == should_change:
            print("✅ PASS")
        else:
            print("❌ FAIL")
            if result != expected_output:
                print(f"   Expected output: {expected_output}")
                print(f"   Actual output:   {result}")
            if changed != should_change:
                print(f"   Expected changed: {should_change}")
                print(f"   Actual changed:   {changed}")

if __name__ == "__main__":
    test_instagram_fixes()
