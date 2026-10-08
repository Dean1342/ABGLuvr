import discord
from discord.ext import commands
from discord import app_commands


class HelpView(discord.ui.View):
    """Interactive view for model help with pagination buttons."""
    
    def __init__(self, model_family: str = "gpt-4.1"):
        super().__init__(timeout=300)
        self.model_family = model_family
        self.update_buttons()
    
    def update_buttons(self):
        """Update button states based on current model family."""
        self.clear_items()
        
        # Add model family buttons
        gpt41_button = discord.ui.Button(
            label="GPT-4.1",
            style=discord.ButtonStyle.primary if self.model_family == "gpt-4.1" else discord.ButtonStyle.secondary,
            custom_id="gpt-4.1"
        )
        gpt5_button = discord.ui.Button(
            label="GPT-5",
            style=discord.ButtonStyle.primary if self.model_family == "gpt-5" else discord.ButtonStyle.secondary,
            custom_id="gpt-5"
        )
        
        gpt41_button.callback = self.gpt41_callback
        gpt5_button.callback = self.gpt5_callback
        
        self.add_item(gpt41_button)
        self.add_item(gpt5_button)
        
        # Add model variant buttons for current family
        if self.model_family == "gpt-4.1":
            variants = ["GPT-4.1", "GPT-4.1 Mini", "GPT-4.1 Nano"]
        else:
            variants = ["GPT-5", "GPT-5 Mini", "GPT-5 Nano"]
        
        for variant in variants:
            button = discord.ui.Button(
                label=variant.split()[-1] if len(variant.split()) > 1 else variant,
                style=discord.ButtonStyle.success,
                custom_id=f"info_{variant}"
            )
            button.callback = lambda interaction, v=variant: self.variant_callback(interaction, v)
            self.add_item(button)
    
    async def gpt41_callback(self, interaction: discord.Interaction):
        """Switch to GPT-4.1 family."""
        self.model_family = "gpt-4.1"
        self.update_buttons()
        embed = self.create_gpt41_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def gpt5_callback(self, interaction: discord.Interaction):
        """Switch to GPT-5 family."""
        self.model_family = "gpt-5"
        self.update_buttons()
        embed = self.create_gpt5_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def variant_callback(self, interaction: discord.Interaction, variant: str):
        """Show detailed info for a specific model variant."""
        embed = self.create_variant_embed(variant)
        await interaction.response.edit_message(embed=embed, view=self)
    
    def create_gpt41_embed(self):
        """Create embed for GPT-4.1 family overview."""
        embed = discord.Embed(
            title="🤖 GPT-4.1 Models",
            description="The best model for coding and agentic tasks across domains",
            color=discord.Color.from_rgb(134, 159, 255)
        )
        
        embed.add_field(
            name="📊 **GPT-4.1**",
            value=(
                "**Intelligence:** ●●●●\n"
                "**Speed:** ●●●\n"
                "**Input:** $1.25 per 1M tokens\n"
                "**Output:** $10.00 per 1M tokens\n"
                "**Context:** 1,047,576 tokens"
            ),
            inline=True
        )
        
        embed.add_field(
            name="⚡ **GPT-4.1 Mini**",
            value=(
                "**Intelligence:** ●●●\n"
                "**Speed:** ●●●●\n"
                "**Input:** $0.25 per 1M tokens\n"
                "**Output:** $2.00 per 1M tokens\n"
                "**Context:** 1,047,576 tokens"
            ),
            inline=True
        )
        
        embed.add_field(
            name="🚀 **GPT-4.1 Nano**",
            value=(
                "**Intelligence:** ●●\n"
                "**Speed:** ●●●●●\n"
                "**Input:** $0.05 per 1M tokens\n"
                "**Output:** $0.40 per 1M tokens\n"
                "**Context:** 1,047,576 tokens"
            ),
            inline=True
        )
        
        embed.add_field(
            name="💡 **Best For**",
            value=(
                "• Complex coding tasks\n"
                "• Agentic workflows\n"
                "• Technical analysis\n"
                "• Problem solving"
            ),
            inline=False
        )
        
        embed.set_footer(text="Click the buttons below to explore specific models or switch to GPT-5")
        return embed
    
    def create_gpt5_embed(self):
        """Create embed for GPT-5 family overview."""
        embed = discord.Embed(
            title="🤖 GPT-5 Models",
            description="Fast, highly intelligent model with largest context window",
            color=discord.Color.from_rgb(255, 134, 159)
        )
        
        embed.add_field(
            name="🧠 **GPT-5**",
            value=(
                "**Intelligence:** ●●●●\n"
                "**Speed:** ●●●\n"
                "**Input:** $2.00 per 1M tokens\n"
                "**Output:** $8.00 per 1M tokens\n"
                "**Context:** 400,000 tokens"
            ),
            inline=True
        )
        
        embed.add_field(
            name="⚡ **GPT-5 Mini**",
            value=(
                "**Intelligence:** ●●●\n"
                "**Speed:** ●●●●\n"
                "**Input:** $0.40 per 1M tokens\n"
                "**Output:** $1.60 per 1M tokens\n"
                "**Context:** 400,000 tokens"
            ),
            inline=True
        )
        
        embed.add_field(
            name="🚀 **GPT-5 Nano**",
            value=(
                "**Intelligence:** ●●\n"
                "**Speed:** ●●●●●\n"
                "**Input:** $0.10 per 1M tokens\n"
                "**Output:** $0.40 per 1M tokens\n"
                "**Context:** 400,000 tokens"
            ),
            inline=True
        )
        
        embed.add_field(
            name="💡 **Best For**",
            value=(
                "• Large document analysis\n"
                "• Long conversations\n"
                "• Complex reasoning\n"
                "• High-context tasks"
            ),
            inline=False
        )
        
        embed.set_footer(text="Click the buttons below to explore specific models or switch to GPT-4.1")
        return embed
    
    def create_variant_embed(self, variant: str):
        """Create detailed embed for a specific model variant."""
        models_info = {
            "GPT-4.1": {
                "title": "GPT-4.1",
                "description": "The best model for coding and agentic tasks across domains",
                "intelligence": "●●●●",
                "speed": "●●●",
                "input_cost": "$1.25",
                "cached_input": "$0.13",
                "output_cost": "$10.00",
                "context": "1,047,576",
                "max_output": "32,768",
                "knowledge_cutoff": "May 31, 2024",
                "color": discord.Color.from_rgb(134, 159, 255)
            },
            "GPT-4.1 Mini": {
                "title": "GPT-4.1 Mini",
                "description": "A faster, cost-efficient version of GPT-4.1 for well-defined tasks",
                "intelligence": "●●●",
                "speed": "●●●●",
                "input_cost": "$0.25",
                "cached_input": "$0.03",
                "output_cost": "$2.00",
                "context": "1,047,576",
                "max_output": "32,768",
                "knowledge_cutoff": "May 31, 2024",
                "color": discord.Color.from_rgb(134, 159, 255)
            },
            "GPT-4.1 Nano": {
                "title": "GPT-4.1 Nano",
                "description": "Fastest, most cost-efficient version of GPT-4.1",
                "intelligence": "●●",
                "speed": "●●●●●",
                "input_cost": "$0.05",
                "cached_input": "$0.01",
                "output_cost": "$0.40",
                "context": "1,047,576",
                "max_output": "32,768",
                "knowledge_cutoff": "May 31, 2024",
                "color": discord.Color.from_rgb(134, 159, 255)
            },
            "GPT-5": {
                "title": "GPT-5",
                "description": "Fast, highly intelligent model with largest context window",
                "intelligence": "●●●●",
                "speed": "●●●",
                "input_cost": "$2.00",
                "cached_input": "$0.50",
                "output_cost": "$8.00",
                "context": "400,000",
                "max_output": "128,000",
                "knowledge_cutoff": "Sep 29, 2024",
                "color": discord.Color.from_rgb(255, 134, 159)
            },
            "GPT-5 Mini": {
                "title": "GPT-5 Mini",
                "description": "Balanced for intelligence, speed, and cost",
                "intelligence": "●●●",
                "speed": "●●●●",
                "input_cost": "$0.40",
                "cached_input": "$0.10",
                "output_cost": "$1.60",
                "context": "400,000",
                "max_output": "128,000",
                "knowledge_cutoff": "May 30, 2024",
                "color": discord.Color.from_rgb(255, 134, 159)
            },
            "GPT-5 Nano": {
                "title": "GPT-5 Nano",
                "description": "Fastest, most cost-effective GPT-5 model",
                "intelligence": "●●",
                "speed": "●●●●●",
                "input_cost": "$0.10",
                "cached_input": "$0.03",
                "output_cost": "$0.40",
                "context": "400,000",
                "max_output": "128,000",
                "knowledge_cutoff": "May 30, 2024",
                "color": discord.Color.from_rgb(255, 134, 159)
            }
        }
        
        info = models_info[variant]
        embed = discord.Embed(
            title=f"🤖 {info['title']}",
            description=info['description'],
            color=info['color']
        )
        
        embed.add_field(name="Intelligence", value=info['intelligence'], inline=True)
        embed.add_field(name="Speed", value=info['speed'], inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Spacer
        
        embed.add_field(name="Input Cost", value=f"{info['input_cost']} per 1M tokens", inline=True)
        embed.add_field(name="Cached Input", value=f"{info['cached_input']} per 1M tokens", inline=True)
        embed.add_field(name="Output Cost", value=f"{info['output_cost']} per 1M tokens", inline=True)
        
        embed.add_field(name="Context Window", value=f"{info['context']} tokens", inline=True)
        embed.add_field(name="Max Output", value=f"{info['max_output']} tokens", inline=True)
        embed.add_field(name="Knowledge Cutoff", value=info['knowledge_cutoff'], inline=True)
        
        embed.set_footer(text="Use /model options to switch to this model")
        return embed


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
                "A feature-rich Discord bot powered by OpenAI GPT with advanced conversational AI, "
                "persona switching, model selection, multimodal support, Spotify integration, movie/TV ratings, and intelligent web search."
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
                "`/help model` — Learn about AI model selection\n"
                "`/help spotify` — Learn about Spotify integration commands\n"
                "`/help rate` — Learn about movie/TV rating commands\n"
                "`/help persona` — Learn about persona switching\n\n"
                "`/model selected` — Show your current AI model\n"
                "`/model options` — Change your AI model\n"
                "`/model reset` — Start fresh: ignore earlier messages when answering you here\n"
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
            name="AI Models",
            value=(
                "Choose between GPT-4.1 and GPT-5 model families with different variants:\n"
                "• **Standard models** for complex tasks\n"
                "• **Mini models** for balanced performance\n"
                "• **Nano models** for fast, cost-effective responses\n"
                "Each user can select their preferred model per channel."
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="Personas",
            value=(
                "Switch between AI personalities like Gordon Ramsay, Albert Einstein, "
                "LeBron James, LTT, and more!\n"
                "Each channel remembers your last selected persona."
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="Key Features",
            value=(
                "• Conversational AI: Follows the recent conversation in the channel, everyone included\n"
                "• Model Selection: Choose from GPT-4.1 and GPT-5 variants\n"
                "• Persona Switching: 20 unique personalities to choose from\n"
                "• Image Support: Upload or reply to images for visual context\n"
                "• Web Search: Auto-searches and cites sources when needed\n"
                "• Spotify Integration: Link account, search music, get recommendations\n"
                "• Movie/TV Ratings: Get detailed info from TMDb database\n"
                "• Smart Responses: Automatically splits long messages"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=emb)

    @app_commands.command(name="model", description="Learn about AI model selection and available models.")
    @app_commands.describe(model="Optional: Select specific model family to view")
    @app_commands.choices(model=[
        app_commands.Choice(name="GPT-4.1", value="gpt-4.1"),
        app_commands.Choice(name="GPT-5", value="gpt-5")
    ])
    async def model_help(self, interaction: discord.Interaction, model: str = "gpt-4.1"):
        """Show detailed help for AI model selection with interactive pagination."""
        view = HelpView(model_family=model)
        
        if model == "gpt-5":
            embed = view.create_gpt5_embed()
        else:
            embed = view.create_gpt41_embed()
        
        await interaction.response.send_message(embed=embed, view=view)

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
                "😎 **Default** — ABGLuvr, a chill millennial who matches your energy\n"
                "🧠 **Albert Einstein** — Brilliant physicist with scientific insights\n"
                "✝️ **Jesus** — Kind, wise, and humble spiritual guidance\n"
                "🇺🇸 **Republican** — Passionate conservative political views\n"
                "👨‍🍳 **Chef** — Michelin Star culinary expertise"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎪 __More Personas__",
            value=(
                "💪 **Fitness Trainer** — Exercise and nutrition guidance\n"
                "🔥 **Gordon Ramsay** — Fiery, brutally honest celebrity chef\n"
                "🏀 **LeBron James** — Confident and charismatic basketball star\n"
                "💻 **LTT** — Tech-savvy Linus Sebastian personality\n"
                "💕 **Girlfriend** — Supportive, caring, and loving"
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
                "• The Default persona mirrors whatever energy you bring"
            ),
            inline=False
        )
        emb.add_field(name="\u200b", value="\u200b", inline=False)
        emb.add_field(
            name="🎯 __Tips__",
            value=(
                "• Try different personas for different types of conversations\n"
                "• Use specific personas for their expertise (Chef for cooking, etc.)\n"
                "• Switching persona starts a fresh conversation for you"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=emb)


async def setup(bot):
    """Setup function for the cog."""
    await bot.add_cog(Help(bot))
