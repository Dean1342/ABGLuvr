# ABGLuvr Discord Bot

A sophisticated Discord bot powered by OpenAI's GPT-4.1 with advanced conversational AI capabilities, persona switching, multimodal support, Spotify integration, movie/TV database integration, and intelligent web search functionality.

## Features

### Core AI Capabilities
- **Agentic Conversational AI**: OpenAI Responses API with a multi-round tool loop (search, currency, pings) and selectable models via `/model`
- **Channel Awareness**: Reads the recent conversation in the channel (last ~40 messages from the past 12 hours, everyone included), so it can follow group chats. Context comes from Discord itself, so it survives restarts
- **Multimodal Support**: Processes and analyzes images alongside text conversations

### Persona System
- **Personas**: Switch between personalities including Gordon Ramsay, Albert Einstein, LeBron James, and more
- **Per-Channel Memory**: Each channel remembers your selected persona and model, persisted across restarts
- **Dynamic Switching**: Change personas instantly with slash commands

### Integrations
- **Spotify Integration**: Complete music discovery platform with OAuth authentication
  - Link/unlink Spotify accounts
  - Search albums, artists, and tracks
  - View top music and recent listening history
  - Get personalized recommendations
  - Display rich music information with pagination

- **Movie/TV Database**: Comprehensive entertainment information via TMDb
  - Search movies and TV shows with filtering options
  - Display ratings, cast, crew, and detailed metadata
  - Support for year and cast-based search refinement
  - Rich embeds with posters and external links

- **Web Search**: OpenAI's native web search tool
  - The model decides when to search and can search multiple times per answer
  - Cited sources are appended as links

### User Experience
- **Smart Channel Management**: Configurable allowed channels with mention override
- **Message Handling**: Automatic message splitting for long responses
- **Interactive UI**: Pagination for large datasets
- **Context-Aware Replies**: Reply to messages for contextual conversations
- **Error Handling**: Comprehensive error handling with user-friendly messages

## Architecture

The bot follows a modular architecture with clear separation of concerns:

```
src/
├── bot.py                 # Main bot initialization and event handling
├── cogs/                  # Discord command groups
│   ├── help.py           # Help and information commands
│   ├── persona.py        # Persona switching commands  
│   ├── rate.py           # Movie/TV rating commands
│   └── spotify.py        # Spotify integration commands
└── utils/                # Utility modules
    ├── ai/               # AI and language model utilities
    ├── conversation/     # Channel context, model registry, saved settings
    ├── core/            # Core utility functions
    ├── integrations/    # External API integrations
    └── ui/              # Discord UI components
```

## Requirements

- Python 3.11 or higher
- Discord.py 2.0+
- OpenAI Python library
- Additional dependencies listed in requirements.txt

## Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Dean1342/ABGLuvr.git
   cd ABGLuvr
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Configuration:**
   Create a `.env` file in the root directory:
   ```env
   DISCORD_TOKEN=your_discord_bot_token
   OPENAI_API_KEY=your_openai_api_key
   CHANNEL_IDS=comma,separated,channel,ids
   SPOTIFY_CLIENT_ID=your_spotify_client_id
   SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
   SPOTIFY_REDIRECT_URI=your_spotify_redirect_uri
   TMDB_API_KEY=your_tmdb_api_key
   SUPABASE_URL=your_supabase_url
   SUPABASE_KEY=your_supabase_key
   GEMINI_API_KEY=your_gemini_api_key
   ```

4. **Run the bot:**
   ```bash
   python src/bot.py
   ```

## Usage

### Basic Interaction
- **Allowed Channels**: The bot responds in channels listed in `CHANNEL_IDS` environment variable
- **Mention Override**: Mention the bot in any channel for a response regardless of channel restrictions
- **Ignore Prefix**: Use "!" prefix before messages in allowed channels to be ignored by the bot
- **Context Replies**: Reply to messages and mention the bot for context-aware conversations

### Command Reference

#### Help Commands
- `/help general` - Show comprehensive bot information and usage guide
- `/help spotify` - Learn about Spotify integration features and commands
- `/help rate` - Learn about movie/TV rating commands and search options
- `/help persona` - Learn about persona switching and available personalities

#### Persona Commands
- `/persona selected` - Display your currently active persona
- `/persona options <persona>` - Switch to a different persona for this channel

#### Spotify Commands
- `/spotify link` - Link your Spotify account to the bot
- `/spotify unlink` - Unlink your Spotify account
- `/spotify registered` - Check account linkage status
- `/spotify search <type> <query>` - Search for albums, artists, or tracks
- `/spotify top <type> [time_range] [limit] [user]` - View top artists or tracks
- `/spotify recents [limit] [user]` - Display recently played tracks
- `/spotify recommend [limit] [user]` - Get personalized music recommendations

#### Movie/TV Commands
- `/rate movie <title> [year] [cast]` - Get movie ratings and detailed information
- `/rate tv <title> [year] [cast]` - Get TV show ratings and detailed information

### Advanced Features

#### Image Analysis
Upload images or reply to existing images while mentioning the bot to get AI-powered image analysis and contextual responses.

#### Web Search Integration
The bot automatically performs web searches when current information is needed and cites sources in responses.

#### Persona System
Choose from personalities including:
- **Characters**: Gordon Ramsay, LeBron James, Linus (LTT), Girlfriend
- **Historical Figures**: Albert Einstein, Jesus Christ
- **Professionals**: Michelin Star Chef, Fitness Trainer

Personas are defined in `src/utils/ai/prompts.py`. File-backed personas (the old real-member ones) are kept there disabled, ready for a future overhaul.

## Configuration

### Environment Variables
- `DISCORD_TOKEN` - Your Discord bot token (required)
- `OPENAI_API_KEY` - Your OpenAI API key (required)
- `CHANNEL_IDS` - Comma-separated list of allowed channel IDs
- `SPOTIFY_CLIENT_ID` - Spotify application client ID
- `SPOTIFY_CLIENT_SECRET` - Spotify application client secret
- `SPOTIFY_REDIRECT_URI` - Spotify OAuth redirect URI
- `TMDB_API_KEY` - The Movie Database API key

- `SUPABASE_URL` / `SUPABASE_KEY` - Supabase project (car builds, reminders, persona/model settings)
- `GEMINI_API_KEY` - Google Gemini key for YouTube `/tldr`

### Optional Configuration
- Default model: `DEFAULT_MODEL` in `src/utils/conversation/context.py` (GPT-6 Luna). It is used for chat (unless a user picks another with `/model`), `/tldr`, and `/build` helpers
- `AI_REASONING_EFFORT` - Reasoning effort for GPT-5 family models (default: `low`)
- `AI_LOG_TO_DB` - Set to `1` to also write per-turn agent logs to the Supabase `ai_logs` table
- `server_context.txt` (repo root, gitignored) - Optional hand-written server facts (members, nicknames, cars) added to every prompt

### Supabase Tables
Create these once; the DDL is in comments in `src/utils/integrations/supabase_client.py`:
`car_profiles`, `build_mods`, `build_labor`, `scheduled_reminders`, `user_settings`, and optionally `ai_logs`.

## Development

### Evals
`scripts/run_evals.py` runs the prompts in `scripts/evals.json` through the same router + agent path the bot uses (without Discord) and reports which tools each prompt triggered:
```bash
python scripts/run_evals.py --model "GPT-5.4 Mini"
python scripts/run_evals.py --only search,multi-tool --out results.json
```
Every bot turn also logs one `[ai] {...}` JSON line (tools, rounds, tokens, latency) to stdout / Heroku logs.

### Project Structure
The codebase follows modern Python practices with clear separation of concerns:
- **Cogs**: Command interfaces organized by functionality
- **Utils**: Reusable utilities organized by purpose (AI, integrations, UI)
- **Modular Design**: Easy to extend with new features and integrations


## Feature Logic & How Commands Work

### Persona Switching
- Use `/persona options <persona>` to change your persona for the current channel. The bot remembers your persona and conversation context per user per channel.
- `/persona selected` shows your current persona for the channel.

### Contextual Memory
- When someone talks to the bot, it reads the recent messages in that channel (everyone's, plus its own replies) as context. Older images and files show up as placeholders; only the current message's attachments are sent in full.
- `/model reset` and switching persona make the bot ignore earlier messages when answering you in that channel.

### Spotify Integration
- Link your Spotify account with `/spotify link` (OAuth).
- Use `/spotify search`, `/spotify top`, `/spotify recents`, `/spotify recommend`, etc., for music features.

### Movie/TV Ratings
- Use `/rate movie <title> [year] [cast]` or `/rate tv <title> [year] [cast]` to get ratings and info from Rotten Tomatoes and TMDb.
- The bot scrapes and formats results for Discord.

### Image Analysis
- Upload or reply to images and mention the bot to get AI-powered analysis and contextual responses.

### Web Search
- The model searches the web (OpenAI native web search) when it needs current information, can refine and repeat searches, and cites sources in its responses.

### Channel Management
- The bot only responds in allowed channels (set via `CHANNEL_IDS` in `.env`) or when mentioned. Messages starting with `!` in allowed channels are ignored.

### Error Handling
- User-friendly error messages are provided for missing API keys, command misuse, or integration issues.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
