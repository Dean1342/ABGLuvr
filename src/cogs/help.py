import discord
from discord.ext import commands
from discord import app_commands


class Help(commands.GroupCog, name="help"):
    """Help and general bot information commands."""
    
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="general", description="Show general information about the bot, usage, and commands.")
    async def general_help(self, interaction: discord.Interaction):
        """Show comprehensive help information about the bot."""
        emb = discord.Embed(
            title="ABGLuvr Bot Help",
            description=(
                "A feature-rich Discord bot powered by OpenAI GPT-4.1 with advanced conversational AI, "
                "persona switching, multimodal support, Spotify integration, movie/TV ratings, and intelligent web search."
            ),
            color=discord.Color.purple()
        )
        emb.add_field(
            name="Getting Started",
            value=(
                "• Type in allowed channels to chat with the bot\n"
                "• Mention @ABGLuvr anywhere for a quick response\n"
                "• Reply to messages and mention the bot for context-aware answers\n"
                "• Upload or reply to images and mention the bot for image analysis\n"
                "• Upload files (PDF, DOCX, XLSX, TXT) and mention the bot for file analysis\n"
                "• Web search: The bot auto-searches when it needs current info\n"
                "• Prefix with ! to ignore your message in allowed channels"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="Command Groups",
            value=(
                "`/help general` — Show this help message\n"
                "`/help spotify` — Learn about Spotify integration commands\n"
                "`/help rate` — Learn about movie/TV rating commands\n"
                "`/help persona` — Learn about persona switching\n\n"
                "`/persona selected` — Show your current persona\n"
                "`/persona options` — Change your persona\n"
                "`/rate movie <title>` — Get movie ratings and info\n"
                "`/rate tv <title>` — Get TV show ratings and info\n"
                "`/spotify link` — Link your Spotify account\n"
                "`/spotify search` — Search Spotify for music"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="Personas",
            value=(
                "Switch between unique AI personalities like Yoda, Gordon Ramsay, "
                "Dwight Schrute, Jagbir, Lemon, and many more!\n"
                "Each channel remembers your last selected persona."
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="Key Features",
            value=(
                "• Conversational AI: Context-aware chat with memory per user/channel\n"
                "• Persona Switching: 20 unique personalities to choose from\n"
                "• Image Support: Upload or reply to images for visual context\n"
                "• Web Search: Auto-searches and cites sources when needed\n"
                "• Spotify Integration: Link account, search music, get recommendations\n"
                "• Movie/TV Ratings: Get detailed info from TMDb database\n"
                "• Smart Responses: Automatically splits long messages"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="AI Model",
            value="OpenAI GPT-4.1 with 100k+ token context window",
            inline=False
        )
        await interaction.response.send_message(embed=emb)

    @app_commands.command(name="spotify", description="Learn about Spotify integration commands and features.")
    async def spotify_help(self, interaction: discord.Interaction):
        """Show detailed help for Spotify commands."""
        emb = discord.Embed(
            title="🎵 Spotify Integration Help",
            description="Connect your Spotify account and discover music with ABGLuvr!",
            color=discord.Color.green()
        )
        emb.add_field(
            name="🔗 __Account Management__",
            value=(
                "`/spotify link` — Link your Spotify account to the bot\n"
                "`/spotify unlink` — Unlink your Spotify account\n"
                "`/spotify registered` — Check if your account is linked"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎶 __Music Discovery__",
            value=(
                "`/spotify search <type> <query>` — Search for albums, artists, or tracks\n"
                "`/spotify top <type> [time_range] [limit] [user]` — View top artists or tracks\n"
                "`/spotify recents [limit] [user]` — See recently played tracks\n"
                "`/spotify recommend [limit] [user]` — Get personalized recommendations"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="⚙️ __Command Details__",
            value=(
                "**Search types**: `album`, `artist`, `track`\n"
                "**Time ranges**: `1 year`, `6 months`, `4 weeks`\n"
                "**Limits**: 1-50 items (default: 5-10)\n"
                "**User**: Optional - view another user's stats (if they've linked their account)"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🔒 __Privacy & Security__",
            value=(
                "• Your Spotify data is stored securely and locally\n"
                "• Only basic profile and listening data is accessed\n"
                "• You can unlink your account anytime with `/spotify unlink`\n"
                "• Other users can only see your stats if you allow it"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="💡 __Tips__",
            value=(
                "• Link your account first with `/spotify link`\n"
                "• Use the search command to find specific songs or artists\n"
                "• Check out your music taste with `/spotify top`\n"
                "• Discover new music with `/spotify recommend`"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=emb)

    @app_commands.command(name="rate", description="Learn about movie and TV show rating commands.")
    async def rate_help(self, interaction: discord.Interaction):
        """Show detailed help for rating commands."""
        emb = discord.Embed(
            title="🎬 Movie & TV Rating Help",
            description="Get detailed ratings and information about movies and TV shows!",
            color=discord.Color.gold()
        )
        emb.add_field(
            name="📽️ __Movie Commands__",
            value=(
                "`/rate movie <title>` — Get movie ratings and details\n"
                "`/rate movie <title> <year>` — Search by title and year\n"
                "`/rate movie <title> <year> <cast>` — Search with cast member"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="📺 __TV Show Commands__",
            value=(
                "`/rate tv <title>` — Get TV show ratings and details\n"
                "`/rate tv <title> <year>` — Search by title and first air year\n"
                "`/rate tv <title> <year> <cast>` — Search with cast member"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="📊 __Information Provided__",
            value=(
                "• **TMDb Rating** — Community ratings out of 10\n"
                "• **Genres** — Movie/show categories\n"
                "• **Cast & Crew** — Directors, actors, and key personnel\n"
                "• **Release Info** — Dates, runtime, status\n"
                "• **Synopsis** — Plot overview and description\n"
                "• **Budget/Revenue** — Financial information (movies)\n"
                "• **Poster Images** — High-quality promotional images"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎯 __Search Tips__",
            value=(
                "• Use specific titles for better results\n"
                "• Add the year if there are multiple versions\n"
                "• Include a cast member's name for disambiguation\n"
                "• The bot will find the best match automatically"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="📖 __Examples__",
            value=(
                "`/rate movie Inception` — Get info about Inception\n"
                "`/rate movie Batman 2022` — Find the 2022 Batman movie\n"
                "`/rate tv Breaking Bad` — Get Breaking Bad details\n"
                "`/rate tv The Office 2005 Steve Carell` — US version with Steve Carell"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=emb)

    @app_commands.command(name="persona", description="Learn about persona switching and available personalities.")
    async def persona_help(self, interaction: discord.Interaction):
        """Show detailed help for persona commands."""
        emb = discord.Embed(
            title="🎭 Persona System Help",
            description="Switch between unique AI personalities for different conversation experiences!",
            color=discord.Color.blue()
        )
        emb.add_field(
            name="🔄 __Persona Commands__",
            value=(
                "`/persona selected` — See your current active persona\n"
                "`/persona options <persona>` — Switch to a different persona"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎪 __Available Personas__",
            value=(
                "**🧙‍♂️ Yoda** — Wise Jedi Master with unique speech patterns\n"
                "🧠 **Albert Einstein** — Brilliant physicist with scientific insights\n"
                "✝️ **Jesus** — Kind, wise, and humble spiritual guidance\n"
                "🇺🇸 **Republican** — Passionate conservative political views\n"
                "📄 **Dwight Schrute** — Eccentric, ambitious, and loyal (The Office)\n"
                "😤 **Ego** — Arrogant and superior personality\n"
                "👨‍🍳 **Chef** — Michelin Star culinary expertise\n"
                "💪 **Fitness Trainer** — Exercise and nutrition guidance\n"
                "🔥 **Gordon Ramsay** — Fiery, brutally honest celebrity chef"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎪 __More Personas__",
            value=(
                "🧬 **Eugene Porter** — Intelligent but socially awkward (Walking Dead)\n"
                "🏀 **LeBron James** — Confident and charismatic basketball star\n"
                "💻 **LTT** — Tech-savvy Linus Sebastian personality\n"
                "⚗️ **Jesse Pinkman** — Unique speech patterns (Breaking Bad)\n"
                "🧪 **Walter White** — Authoritative and intelligent (Breaking Bad)\n"
                "🦾 **Tony Stark** — Genius billionaire playboy philanthropist\n"
                "💕 **Girlfriend** — Supportive, caring, and loving\n"
                "👥 **Jagbir** — Real Discord member personality\n"
                "🍋 **Lemon** — Real Discord member personality\n"
                "🎮 **Epoe** — Real Discord member personality"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="💡 __How It Works__",
            value=(
                "• Each persona has unique speech patterns and knowledge\n"
                "• Your persona choice is **per-channel** — different channels remember different personas\n"
                "• The bot stays in character throughout the conversation\n"
                "• Switch anytime with `/persona options`\n"
                "• Default persona is friendly and helpful"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎯 __Tips__",
            value=(
                "• Try different personas for different types of conversations\n"
                "• Use specific personas for their expertise (Chef for cooking, etc.)\n"
                "• Real member personas (Jagbir, Lemon) are based on actual Discord users\n"
                "• Each persona remembers your conversation history"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=emb)


async def setup(bot):
    """Setup function for the cog."""
    await bot.add_cog(Help(bot))
