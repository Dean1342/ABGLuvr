# ABGLuvr Discord Bot

A sophisticated Discord bot powered by OpenAI's GPT-4.1 with advanced conversational AI capabilities, persona switching, multimodal support, Spotify integration, movie/TV database integration, and intelligent web search functionality.

## Features

### Core AI Capabilities
- **Advanced Conversational AI**: Powered by OpenAI GPT-4.1 with context-aware responses
- **Large Context Window**: Supports up to 100,000+ tokens for extended conversations
- **Memory Management**: Maintains conversation history per user/channel with intelligent trimming
- **Multimodal Support**: Processes and analyzes images alongside text conversations

### Persona System
- **20+ Unique Personas**: Switch between personalities including Yoda, Gordon Ramsay, Albert Einstein, and more
- **Custom Real-User Personas**: Authentic personalities based on real Discord server members
- **Per-Channel Memory**: Each channel remembers your selected persona
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

- **Web Search**: Intelligent web search with source citation
  - Automatic search triggering for current events
  - Source attribution and link formatting
  - Summarization of search results

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
    ├── conversation/     # Memory and persona management
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
   GOOGLE_API_KEY=your_google_api_key
   GOOGLE_CSE_ID=your_google_cse_id
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
Choose from 20+ unique personalities including:
- **Classic Characters**: Yoda, Dwight Schrute, Gordon Ramsay, Walter White
- **Historical Figures**: Albert Einstein, Jesus Christ
- **Professionals**: Michelin Star Chef, Fitness Trainer

## Configuration

### Environment Variables
- `DISCORD_TOKEN` - Your Discord bot token (required)
- `OPENAI_API_KEY` - Your OpenAI API key (required)
- `CHANNEL_IDS` - Comma-separated list of allowed channel IDs
- `SPOTIFY_CLIENT_ID` - Spotify application client ID
- `SPOTIFY_CLIENT_SECRET` - Spotify application client secret
- `SPOTIFY_REDIRECT_URI` - Spotify OAuth redirect URI
- `TMDB_API_KEY` - The Movie Database API key

### Optional Configuration
- `OPENAI_FINAL_MODEL` - Override default GPT model (default: gpt-4.1-2025-04-14)

## Development

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
- The bot keeps a conversation history for each user in each channel, allowing for context-aware and continuous conversations. This history is trimmed to fit a large token window (up to 100,000 tokens).

### Spotify Integration
- Link your Spotify account with `/spotify link` (OAuth).
- Use `/spotify search`, `/spotify top`, `/spotify recents`, `/spotify recommend`, etc., for music features.

### Movie/TV Ratings
- Use `/rate movie <title> [year] [cast]` or `/rate tv <title> [year] [cast]` to get ratings and info from Rotten Tomatoes and TMDb.
- The bot scrapes and formats results for Discord.

### Image Analysis
- Upload or reply to images and mention the bot to get AI-powered analysis and contextual responses.

### Web Search
- The bot automatically performs web searches for current events or when needed, citing sources in its responses.

### Channel Management
- The bot only responds in allowed channels (set via `CHANNEL_IDS` in `.env`) or when mentioned. Messages starting with `!` in allowed channels are ignored.

### Error Handling
- User-friendly error messages are provided for missing API keys, command misuse, or integration issues.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
