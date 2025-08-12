#!/usr/bin/env python3
"""Test script to verify all imports work correctly."""

import sys
import os

# Add src to path
current_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(current_dir, 'src')
sys.path.insert(0, src_dir)

try:
    # Test imports
    from utils.conversation.context import MODELS, user_models
    from utils.ai.message_processing import get_system_prompt, get_function_schemas
    from cogs.model import Model
    from cogs.help import Help, HelpView
    
    print("✅ All imports successful!")
    
    # Test model data
    print(f"✅ Found {len(MODELS)} models:")
    for name, info in MODELS.items():
        print(f"  - {name}: {info['description'][:50]}...")
    
    print("✅ Model system setup complete!")
    
except Exception as e:
    print(f"❌ Import error: {e}")
    import traceback
    traceback.print_exc()
