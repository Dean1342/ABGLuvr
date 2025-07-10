# Loads persona prompt files for each persona

import os


def load_epoe_persona():
    prompt = os.getenv("EPOE_PERSONA_PROMPT")
    if prompt:
        return prompt.strip()
    # fallback to file for local dev
    EPOE_PERSONA_PATH = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        '..', '..', 'Custom Personas', 'Prompt Files', 'Epoe', 'Epoe_FULL_PERSONA.txt'
    ))
    try:
        with open(EPOE_PERSONA_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return "Adopt the persona of Epoe, a real member of this Discord server. Use their authentic style, slang, and opinions."


def load_jagbir_persona():
    prompt = os.getenv("JAGBIR_PERSONA_PROMPT")
    if prompt:
        return prompt.strip()
    JAGBIR_PERSONA_PATH = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        '..', '..', 'Custom Personas', 'Prompt Files', 'Jagbir', 'Main', 'jagbir_persona_prompt.txt'
    ))
    try:
        with open(JAGBIR_PERSONA_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return "Adopt the persona of Jagbir, a real member of this Discord server. Use his authentic style, slang, and opinions."


def load_lemon_persona():
    prompt = os.getenv("LEMON_PERSONA_PROMPT")
    if prompt:
        return prompt.strip()
    LEMON_PERSONA_PATH = os.path.abspath(os.path.join(
        os.path.dirname(__file__),
        '..', '..', 'Custom Personas', 'Prompt Files', 'Lemon', 'Lemon_FULL_PERSONA.txt'
    ))
    try:
        with open(LEMON_PERSONA_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return "Adopt the persona of Lemon, a real member of this Discord server. Use his authentic style, slang, and opinions."
