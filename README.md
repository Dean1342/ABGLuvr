# ABGLuvr Discord GPT Bot

A Discord bot powered by OpenAI's GPT-4.1 (or GPT-4o) with persona switching, multimodal (image) support, and per-user/channel context. Built with Python and discord.py.

## Features

- **Conversational AI**: Chat with the bot in any allowed channel or by mentioning it in any channel.
- **Persona Switching**: Use `/persona options` to change the bot's persona per user per channel. Each channel remembers your last selected persona.
- **Contextual Memory**: The bot remembers your conversation history per channel, with a large context window (up to 100,000 tokens).
- **Image Support**: Upload images and ask about them, or reply to images and mention the bot for context-aware responses.
- **Slash Commands**: `/help`, `/persona selected`, `/persona options`.

## Setup

1. **Clone the repository:**
   ```sh
   git clone https://github.com/Dean1342/ABGLuvr.git
   cd ABGLuvr
   ```

2. **Install the requirements:**
   ```sh
   pip install -r requirements.txt
   ```

3. **Create a `.env` file and add your tokens:**
   ```
   DISCORD_TOKEN=your_discord_token
   OPENAI_API_KEY=your_openai_api_key
   CHANNEL_IDS=comma,separated,channel,ids
   ```

4. **Run the bot:**
   ```sh
   python src/bot.py
   ```

## Usage

- **Allowed Channels**: The bot will reply in channels listed in `CHANNEL_IDS` or when mentioned in any channel. Use "!" prefix before sending a message in an allowed channel to be ignored by the bot.
- **Persona Switching**: Use `/persona options` to change persona for your user in the current channel.
- **Image & Reply Support**: Upload images or reply to messages and mention the bot for context-aware answers.
- **Help**: Use `/help` for a summary of commands and features.

## Requirements

- Python 3.11+
- discord.py
- openai
- python-dotenv
- tiktoken
