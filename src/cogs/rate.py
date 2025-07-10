import discord
from discord.ext import commands
from discord import app_commands

from utils.integrations.tmdb import (
    search_movie_or_tv,
    get_movie_details,
    get_tv_details,
    format_runtime,
    get_poster_url,
    get_imdb_url,
    get_tmdb_url
)
from utils.integrations.rottentomatoes import get_rotten_tomatoes_scores, format_rt_scores


class Rate(commands.GroupCog, name="rate"):
    """Handles movie and TV rating commands."""
    
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="movie", description="Get ratings and information for a movie.")
    @app_commands.describe(
        title="Movie title to search for",
        year="Release year to help narrow down results (optional)",
        cast="Actor name to help find the right movie (optional)"
    )
    async def movie(self, interaction: discord.Interaction, title: str, year: int = None, cast: str = None):
        """Get ratings/info for a movie."""
        await interaction.response.defer()
        
        # Search for the movie
        results = search_movie_or_tv(title, "movie")
        if not results:
            # No results found
            await interaction.followup.send("No movies found with that title.", ephemeral=True)
            return
        
        # Filter results based on optional parameters
        filtered_results = results
        
        # Filter by year if provided
        if year:
            year_filtered = []
            for movie in filtered_results:
                release_date = movie.get("release_date", "")
                if release_date and release_date.startswith(str(year)):
                    year_filtered.append(movie)
            if year_filtered:
                filtered_results = year_filtered
            else:
                # No movies found from the specified year
                await interaction.followup.send(f"No movies found with that title from {year}.", ephemeral=True)
                return
        
        # Filter by cast if provided (need to get detailed info for each to check cast)
        if cast:
            cast_filtered = []
            for movie in filtered_results[:5]:  # Limit to first 5 to avoid too many API calls
                movie_details = get_movie_details(movie["id"])
                if movie_details:
                    credits = movie_details.get("credits", {})
                    cast_list = credits.get("cast", [])
                    for actor in cast_list:
                        if cast.lower() in actor.get("name", "").lower():
                            cast_filtered.append(movie)
                            break
            if cast_filtered:
                filtered_results = cast_filtered
            else:
                # No movies found with the specified cast
                await interaction.followup.send(f"No movies found with that title featuring '{cast}'.", ephemeral=True)
                return
        
        # Get the first result from filtered results
        movie = filtered_results[0]
        movie_id = movie["id"]
        
        # Get detailed information
        details = get_movie_details(movie_id)
        if not details:
            # No details found for the movie
            await interaction.followup.send("Could not retrieve movie details.", ephemeral=True)
            return
        
        # Format title with year
        title_with_year = details["title"]
        if details.get("release_date"):
            year = details["release_date"][:4]
            title_with_year += f" ({year})"
        
        # Create embed for movie details
        emb = discord.Embed(
            title=title_with_year,
            description=details.get("overview", "No overview available."),
            color=discord.Color.purple()
        )
        
        # Add poster as thumbnail
        poster_url = get_poster_url(details.get("poster_path"))
        if poster_url:
            emb.set_thumbnail(url=poster_url)
        
        # Get Rotten Tomatoes scores
        release_year = None
        if details.get("release_date"):
            release_year = int(details["release_date"][:4])
        
        rt_scores = await get_rotten_tomatoes_scores(details["title"], release_year, is_tv=False)
        
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
        
        # Runtime
        runtime = details.get("runtime")
        if runtime:
            emb.add_field(name="Runtime", value=format_runtime(runtime), inline=True)
        
        # Release Date
        release_date = details.get("release_date")
        if release_date:
            emb.add_field(name="Release Date", value=release_date, inline=True)
        
        # Status
        status = details.get("status")
        if status:
            emb.add_field(name="Status", value=status, inline=True)
        
        # Budget and Revenue
        budget = details.get("budget")
        revenue = details.get("revenue")
        if budget and budget > 0:
            emb.add_field(name="Budget", value=f"${budget:,}", inline=True)
        if revenue and revenue > 0:
            emb.add_field(name="Revenue", value=f"${revenue:,}", inline=True)
        
        # Director
        credits = details.get("credits", {})
        crew = credits.get("crew", [])
        directors = [person["name"] for person in crew if person["job"] == "Director"]
        if directors:
            emb.add_field(name="Director", value=", ".join(directors[:2]), inline=True)
        
        # Top Cast
        cast = credits.get("cast", [])
        if cast:
            top_cast = [actor["name"] for actor in cast[:5]]
            emb.add_field(name="Cast", value=", ".join(top_cast), inline=False)
        
        # Production Companies
        companies = details.get("production_companies", [])
        if companies:
            company_names = ", ".join([c["name"] for c in companies[:3]])
            emb.add_field(name="Production", value=company_names, inline=False)
        
        # Links
        links = []
        
        # Add Rotten Tomatoes link if we have scores
        if rt_scores and rt_scores.get('url'):
            links.append(f"[🍅 Rotten Tomatoes]({rt_scores['url']})")
        
        tmdb_url = get_tmdb_url("movie", movie_id)
        if tmdb_url:
            links.append(f"[TMDb]({tmdb_url})")
        
        external_ids = details.get("external_ids", {})
        imdb_id = external_ids.get("imdb_id")
        if imdb_id:
            imdb_url = get_imdb_url(imdb_id)
            links.append(f"[IMDb]({imdb_url})")
        
        if links:
            emb.add_field(name="Links", value=" • ".join(links), inline=False)
        
        await interaction.followup.send(embed=emb)

    @app_commands.command(name="tv", description="Get ratings and information for a TV show.")
    @app_commands.describe(
        title="TV show title to search for",
        year="First air year to help narrow down results (optional)",
        cast="Actor name to help find the right show (optional)"
    )
    async def tv(self, interaction: discord.Interaction, title: str, year: int = None, cast: str = None):
        """Get ratings/info for a TV show."""
        await interaction.response.defer()
        
        # Search for the TV show
        results = search_movie_or_tv(title, "tv")
        if not results:
            # No results found
            await interaction.followup.send("No TV shows found with that title.", ephemeral=True)
            return
        
        # Filter results based on optional parameters
        filtered_results = results
        
        # Filter by year if provided
        if year:
            year_filtered = []
            for show in filtered_results:
                first_air_date = show.get("first_air_date", "")
                if first_air_date and first_air_date.startswith(str(year)):
                    year_filtered.append(show)
            if year_filtered:
                filtered_results = year_filtered
            else:
                # No TV shows found from the specified year
                await interaction.followup.send(f"No TV shows found with that title from {year}.", ephemeral=True)
                return
        
        # Filter by cast if provided (need to get detailed info for each to check cast)
        if cast:
            cast_filtered = []
            for show in filtered_results[:5]:  # Limit to first 5 to avoid too many API calls
                show_details = get_tv_details(show["id"])
                if show_details:
                    credits = show_details.get("credits", {})
                    cast_list = credits.get("cast", [])
                    for actor in cast_list:
                        if cast.lower() in actor.get("name", "").lower():
                            cast_filtered.append(show)
                            break
            if cast_filtered:
                filtered_results = cast_filtered
            else:
                # No TV shows found with the specified cast
                await interaction.followup.send(f"No TV shows found with that title featuring '{cast}'.", ephemeral=True)
                return
        
        # Get the first result from filtered results
        tv_show = filtered_results[0]
        tv_id = tv_show["id"]
        
        # Get detailed information
        details = get_tv_details(tv_id)
        if not details:
            # No details found for the TV show
            await interaction.followup.send("Could not retrieve TV show details.", ephemeral=True)
            return
        
        # Format title with year
        title_with_year = details["name"]
        if details.get("first_air_date"):
            year = details["first_air_date"][:4]
            title_with_year += f" ({year})"
        
        # Create TV pagination view with overview and season pages
        from utils.ui.tv_pagination import TVPaginationView
        
        tv_view = TVPaginationView(details)
        initial_embed = await tv_view.get_current_embed()
        
        await interaction.followup.send(embed=initial_embed, view=tv_view)


async def setup(bot):
    """Setup function for the cog."""
    await bot.add_cog(Rate(bot))
