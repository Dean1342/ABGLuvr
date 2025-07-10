import discord
from ..integrations.rottentomatoes import get_rotten_tomatoes_scores, format_rt_scores
from ..integrations.tmdb import get_poster_url, get_tmdb_url, get_imdb_url


class TVPaginationView(discord.ui.View):
    def __init__(self, tv_details, timeout=300):
        super().__init__(timeout=timeout)
        self.tv_details = tv_details
        self.current_page = "overview"  # "overview" or "seasons"
        self.season_data = {}  # Cache for season-specific data
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        
        # Show Overview button if we're on seasons page
        if self.current_page == "seasons":
            self.add_item(TVPageButton(self, "overview", "📺 Overview", discord.ButtonStyle.secondary))
        
        # Show Seasons button if we have seasons and we're on overview
        num_seasons = self.tv_details.get("number_of_seasons", 0)
        if num_seasons > 0 and self.current_page == "overview":
            self.add_item(TVPageButton(self, "seasons", "📋 View All Seasons", discord.ButtonStyle.primary))

    async def create_overview_embed(self):
        """Create the overview embed for the TV show."""
        details = self.tv_details
        
        # Format title with year
        title_with_year = details["name"]
        if details.get("first_air_date"):
            year = details["first_air_date"][:4]
            title_with_year += f" ({year})"
        
        # Create embed for TV show details
        emb = discord.Embed(
            title=title_with_year,
            description=details.get("overview", "No overview available."),
            color=discord.Color.purple()
        )
        
        # Add poster as thumbnail
        poster_url = get_poster_url(details.get("poster_path"))
        if poster_url:
            emb.set_thumbnail(url=poster_url)
        
        # Get Rotten Tomatoes scores for the overall show
        first_air_year = None
        if details.get("first_air_date"):
            first_air_year = int(details["first_air_date"][:4])
        
        rt_scores = await get_rotten_tomatoes_scores(details["name"], first_air_year, is_tv=True)
        
        # Rotten Tomatoes Rating
        if rt_scores:
            rt_display = format_rt_scores(rt_scores)
            emb.add_field(name="🍅 Rotten Tomatoes", value=rt_display, inline=True)
        else:
            emb.add_field(name="🍅 Rotten Tomatoes", value="Scores not available", inline=True)
        
        # Genres
        genres = details.get("genres", [])
        if genres:
            genre_names = ", ".join([g["name"] for g in genres])
            emb.add_field(name="Genres", value=genre_names, inline=True)
        
        # Status
        status = details.get("status")
        if status:
            emb.add_field(name="Status", value=status, inline=True)
        
        # First Air Date
        first_air_date = details.get("first_air_date")
        if first_air_date:
            emb.add_field(name="First Aired", value=first_air_date, inline=True)
        
        # Last Air Date
        last_air_date = details.get("last_air_date")
        if last_air_date and last_air_date != first_air_date:
            emb.add_field(name="Last Aired", value=last_air_date, inline=True)
        
        # Number of Seasons and Episodes
        num_seasons = details.get("number_of_seasons")
        num_episodes = details.get("number_of_episodes")
        if num_seasons:
            season_text = f"{num_seasons} season{'s' if num_seasons != 1 else ''}"
            if num_episodes:
                season_text += f", {num_episodes} episodes"
            emb.add_field(name="Seasons/Episodes", value=season_text, inline=True)
        
        # Episode Runtime
        episode_runtimes = details.get("episode_run_time", [])
        if episode_runtimes:
            runtime_text = f"~{episode_runtimes[0]} min"
            emb.add_field(name="Episode Runtime", value=runtime_text, inline=True)
        
        # Creators
        creators = details.get("created_by", [])
        if creators:
            creator_names = ", ".join([c["name"] for c in creators[:2]])
            emb.add_field(name="Created By", value=creator_names, inline=True)
        
        # Top Cast
        credits = details.get("credits", {})
        cast = credits.get("cast", [])
        if cast:
            top_cast = [actor["name"] for actor in cast[:5]]
            emb.add_field(name="Cast", value=", ".join(top_cast), inline=False)
        
        # Networks
        networks = details.get("networks", [])
        if networks:
            network_names = ", ".join([n["name"] for n in networks[:3]])
            emb.add_field(name="Networks", value=network_names, inline=False)
        
        # Links
        links = []
        
        # Add Rotten Tomatoes link if we have scores
        if rt_scores and rt_scores.get('url'):
            links.append(f"[🍅 Rotten Tomatoes]({rt_scores['url']})")
        
        tv_id = details["id"]
        tmdb_url = get_tmdb_url("tv", tv_id)
        if tmdb_url:
            links.append(f"[TMDb]({tmdb_url})")
        
        external_ids = details.get("external_ids", {})
        imdb_id = external_ids.get("imdb_id")
        if imdb_id:
            imdb_url = get_imdb_url(imdb_id)
            links.append(f"[IMDb]({imdb_url})")
        
        if links:
            emb.add_field(name="Links", value=" • ".join(links), inline=False)
        
        return emb

    async def create_seasons_embed(self):
        """Create the seasons overview embed with all season ratings."""
        details = self.tv_details
        
        title_with_year = details["name"]
        if details.get("first_air_date"):
            year = details["first_air_date"][:4]
            title_with_year += f" ({year})"
        
        num_seasons = details.get("number_of_seasons", 0)
        
        emb = discord.Embed(
            title=f"{title_with_year} - {num_seasons} Season{'s' if num_seasons != 1 else ''}",
            description="Rotten Tomatoes ratings for each season:",
            color=discord.Color.blue()
        )
        
        # Add poster as thumbnail
        poster_url = get_poster_url(details.get("poster_path"))
        if poster_url:
            emb.set_thumbnail(url=poster_url)
        
        # Get ratings for each season
        first_air_year = None
        if details.get("first_air_date"):
            first_air_year = int(details["first_air_date"][:4])
        
        # Fetch all season ratings
        season_ratings = []
        for season_num in range(1, num_seasons + 1):
            if f"season_{season_num}" not in self.season_data:
                rt_scores = await get_rotten_tomatoes_scores(
                    details["name"], 
                    first_air_year, 
                    is_tv=True, 
                    season=season_num
                )
                self.season_data[f"season_{season_num}"] = rt_scores
            else:
                rt_scores = self.season_data[f"season_{season_num}"]
            
            if rt_scores:
                rating_text = format_rt_scores(rt_scores)
            else:
                rating_text = "🍅 N/A | 🍿 N/A"
            
            season_ratings.append(f"**Season {season_num}**: {rating_text}")
        
        # Add seasons in groups of 5 for better inline display
        if season_ratings:
            # Split seasons into groups of 5 for inline fields
            for i in range(0, len(season_ratings), 5):
                group = season_ratings[i:i+5]
                start_season = i + 1
                end_season = min(i + 5, len(season_ratings))
                
                field_name = f"Seasons {start_season}-{end_season}" if end_season > start_season else f"Season {start_season}"
                emb.add_field(
                    name=field_name,
                    value="\n".join(group),
                    inline=True
                )
        
        return emb

    async def get_current_embed(self):
        """Get the embed for the current page."""
        if self.current_page == "overview":
            return await self.create_overview_embed()
        elif self.current_page == "seasons":
            return await self.create_seasons_embed()
        else:
            # Default to overview for any unknown page
            return await self.create_overview_embed()

    async def update(self, interaction):
        """Update the message with the current page."""
        embed = await self.get_current_embed()
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)


class TVPageButton(discord.ui.Button):
    def __init__(self, parent_view, page_id, label, style=discord.ButtonStyle.primary):
        super().__init__(style=style, label=label)
        self.parent_view = parent_view
        self.page_id = page_id

    async def callback(self, interaction: discord.Interaction):
        # For seasons page, defer the response first to prevent timeout
        if self.page_id == "seasons":
            await interaction.response.defer()
            
            # Show loading state
            loading_embed = discord.Embed(
                title="Loading Seasons...",
                description="Fetching Rotten Tomatoes ratings for all seasons. Please wait...",
                color=discord.Color.yellow()
            )
            await interaction.followup.edit_message(interaction.message.id, embed=loading_embed, view=None)
            
            # Update to seasons page
            self.parent_view.current_page = self.page_id
            embed = await self.parent_view.get_current_embed()
            self.parent_view.update_buttons()
            
            # Edit with final result
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self.parent_view)
        else:
            # For other pages, use normal flow
            self.parent_view.current_page = self.page_id
            await self.parent_view.update(interaction)
