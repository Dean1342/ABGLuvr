import asyncio
import re
import requests
import discord
from discord.ext import commands
from discord import app_commands
from typing import Literal

from utils.integrations.spotify.spotify import (
    get_spotify_auth_url,
    store_spotify_tokens,
    remove_spotify_tokens,
    get_user_tokens,
    refresh_user_tokens,
    spotify_search,
    spotify_user_top,
    spotify_user_recent,
    spotify_user_profile,
    spotify_user_recommend,
    get_app_access_token,
    spotify_artist_top_tracks,
    get_full_album_details
)
from utils.ui.pagination import PaginationView
from utils.ui.spotify_pagination import SpotifyAlbumPaginationView


class Spotify(commands.GroupCog, name="spotify"):
    # Handles Spotify integration commands
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="link", description="Link your Spotify account to the bot.")
    async def link(self, interaction: discord.Interaction):
        # Link Spotify account
        url = get_spotify_auth_url(str(interaction.user.id))
        await interaction.response.send_message(
            f"[Click here to link your Spotify account]({url})\nAfter authorizing, your Spotify will be linked.",
            ephemeral=True
        )
        await asyncio.sleep(3)
        tokens = get_user_tokens(str(interaction.user.id))
        if tokens:
            store_spotify_tokens(str(interaction.user.id), tokens, interaction.user.name)

    @app_commands.command(name="unlink", description="Unlink your Spotify account from the bot.")
    async def unlink(self, interaction: discord.Interaction):
        # Unlink Spotify account
        remove_spotify_tokens(str(interaction.user.id))
        await interaction.response.send_message(
            "Your Spotify account has been unlinked.", ephemeral=True
        )

    @app_commands.command(name="registered", description="Check if your Spotify account is linked.")
    async def registered(self, interaction: discord.Interaction):
        # Check if Spotify is linked
        tokens = get_user_tokens(str(interaction.user.id))
        if tokens:
            tokens = refresh_user_tokens(str(interaction.user.id), tokens)
            profile = spotify_user_profile(tokens)
            if profile and "display_name" in profile and "external_urls" in profile:
                discord_mention = interaction.user.mention
                profile_url = profile["external_urls"].get("spotify", "")
                match = re.search(r"/user/([^/?]+)", profile_url)
                spotify_username = match.group(1) if match else profile_url
                image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
                
                emb = discord.Embed(
                    title=f"Spotify account registered for {discord_mention} as: {spotify_username}",
                    description=f"View profile in Spotify here:\n{profile_url}",
                    color=discord.Color.purple()
                )
                if image_url:
                    emb.set_thumbnail(url=image_url)
                await interaction.response.send_message(embed=emb)
            else:
                await interaction.response.send_message(
                    "Your Spotify account is linked, but profile info could not be retrieved.",
                    ephemeral=False
                )
        else:
            await interaction.response.send_message(
                "No Spotify account found. Use `/spotify link` to connect your account.",
                ephemeral=False
            )

    def _make_single_field_embed(self, title, items, item_type, image_url=None, page_idx=0, total_pages=1):
        # Create a paginated embed for Spotify results
        emb = discord.Embed(title=title, color=discord.Color.purple())
        if image_url:
            emb.set_thumbnail(url=image_url)
        value = "\n".join(
            f"{page_idx*5+j+1}. {item['name']}" + 
            (f" by {', '.join(a['name'] for a in item['artists'])}" if item_type == "tracks" else "") + 
            f" ([Spotify]({item['external_urls']['spotify']}))"
            for j, item in enumerate(items)
        )
        emb.add_field(name=f"{item_type.title()} {page_idx*5+1}-{page_idx*5+len(items)}", value=value, inline=False)
        emb.set_footer(text=f"Page {page_idx+1}/{total_pages}")
        return emb

    def _paginate_items(self, items, per_page=5):
        # Paginate a list of items
        return [items[i:i+per_page] for i in range(0, len(items), per_page)]

    @app_commands.command(name="top", description="Show your top Spotify artists or tracks.")
    @app_commands.describe(
        type="Choose between artists or tracks.",
        data_range="Data range: 1 year, 6 months, 4 weeks",
        limit="How many to list (up to 50)",
        user="Select a user (optional, defaults to yourself)"
    )
    async def top(
        self,
        interaction: discord.Interaction,
        type: Literal["artists", "tracks"],
        data_range: Literal["1 year", "6 months", "4 weeks"] = "1 year",
        limit: app_commands.Range[int, 1, 50] = 10,
        user: discord.User = None
    ):
        # Show top artists or tracks
        user = user or interaction.user
        range_map = {"1 year": "long_term", "6 months": "medium_term", "4 weeks": "short_term"}
        spotify_range = range_map[data_range]
        tokens = get_user_tokens(str(user.id))
        if not tokens:
            await interaction.response.send_message(
                f"No Spotify account registered for {user.mention}. They need to use `/spotify link`.", 
                ephemeral=True
            )
            return
        
        tokens = refresh_user_tokens(str(user.id), tokens)
        data = spotify_user_top(tokens, type, spotify_range, limit)
        profile = spotify_user_profile(tokens)
        image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
        items = data.get("items", [])
        
        if not items:
            await interaction.response.send_message("No data found.", ephemeral=True)
            return
        
        pages = self._paginate_items(items, 5)
        
        def make_embed(page_idx):
            emb = self._make_single_field_embed(
                f"Top {type.title()} for {user.display_name} ({data_range})",
                pages[page_idx],
                type,
                image_url=image_url,
                page_idx=page_idx,
                total_pages=len(pages)
            )
            emb.add_field(name="Data Range", value=data_range, inline=False)
            return emb
        
        view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
        if view:
            await interaction.response.send_message(embed=make_embed(0), view=view)
        else:
            await interaction.response.send_message(embed=make_embed(0))

    @app_commands.command(name="recents", description="Show your recently played Spotify tracks.")
    @app_commands.describe(
        limit="How many to retrieve (up to 50, default 5)", 
        user="Select a user (optional, defaults to yourself)"
    )
    async def recents(self, interaction: discord.Interaction, limit: int = 5, user: discord.User = None):
        # Show recently played tracks
        user = user or interaction.user
        tokens = get_user_tokens(str(user.id))
        if not tokens:
            await interaction.response.send_message(
                f"No Spotify account registered for {user.mention}. They need to use `/spotify link`.", 
                ephemeral=True
            )
            return
        
        tokens = refresh_user_tokens(str(user.id), tokens)
        data = spotify_user_recent(tokens, limit)
        profile = spotify_user_profile(tokens)
        image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
        items = data.get("items", [])
        
        if not items:
            await interaction.response.send_message("No recent tracks found.", ephemeral=True)
            return
        
        tracks = [item["track"] for item in items]
        pages = self._paginate_items(tracks, 5)
        
        def make_embed(page_idx):
            return self._make_single_field_embed(
                f"Recent Tracks for {user.display_name}",
                pages[page_idx],
                "tracks",
                image_url=image_url,
                page_idx=page_idx,
                total_pages=len(pages)
            )
        
        view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
        if view:
            await interaction.response.send_message(embed=make_embed(0), view=view)
        else:
            await interaction.response.send_message(embed=make_embed(0))

    @app_commands.command(name="recommend", description="Get Spotify recommendations based on your habits.")
    @app_commands.describe(
        limit="How many to recommend (up to 50, default 5)", 
        user="Select a user (optional, defaults to yourself)"
    )
    async def recommend(self, interaction: discord.Interaction, limit: int = 5, user: discord.User = None):
        # Get Spotify recommendations
        user = user or interaction.user
        tokens = get_user_tokens(str(user.id))
        if not tokens:
            await interaction.response.send_message(
                f"No Spotify account registered for {user.mention}. They need to use `/spotify link`.", 
                ephemeral=True
            )
            return
        
        tokens = refresh_user_tokens(str(user.id), tokens)
        data = spotify_user_recommend(tokens)
        profile = spotify_user_profile(tokens)
        image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
        tracks = data.get("tracks", [])
        
        if not tracks:
            await interaction.response.send_message("No recommendations found.", ephemeral=True)
            return
        
        pages = self._paginate_items(tracks, 5)
        
        def make_embed(page_idx):
            return self._make_single_field_embed(
                f"Recommended Tracks for {user.display_name}",
                pages[page_idx],
                "tracks",
                image_url=image_url,
                page_idx=page_idx,
                total_pages=len(pages)
            )
        
        view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
        if view:
            await interaction.response.send_message(embed=make_embed(0), view=view)
        else:
            await interaction.response.send_message(embed=make_embed(0))

    @app_commands.command(name="search", description="Search Spotify for album, artist, or track.")
    @app_commands.describe(
        type="What to search (album, artist, track)", 
        query="Search term (track name, album title, or artist name)",
        artist="Artist name (optional, for more specific track/album searches)"
    )
    async def search(self, interaction: discord.Interaction, type: Literal["album", "artist", "track"], query: str, artist: str = None):
        """Search Spotify for content."""
        # Build the search query with artist if provided
        if artist and type in ["track", "album"]:
            search_query = f"{query} artist:{artist}"
        else:
            search_query = query
        
        data = spotify_search(search_query, type)
        if type == "album":
            items = data.get("albums", {}).get("items", [])
        elif type == "artist":
            items = data.get("artists", {}).get("items", [])
        else:
            items = data.get("tracks", {}).get("items", [])
        
        if not items:
            await interaction.response.send_message("No results found.", ephemeral=True)
            return
        
        item = items[0]
        
        # Handle album search with tracks
        if type == "album":
            # Use the new SpotifyAlbumPaginationView
            view = SpotifyAlbumPaginationView(item)
            initial_embed = await view.get_current_embed()
            await interaction.response.send_message(embed=initial_embed, view=view)
            return
        
        # Handle artist/track search
        emb = discord.Embed(
            title=item["name"],
            color=discord.Color.purple(),
            url=item["external_urls"]["spotify"]
        )
        
        if type == "artist" and item.get("images"):
            emb.set_thumbnail(url=item["images"][0]["url"])
        elif type == "track" and item["album"].get("images"):
            emb.set_thumbnail(url=item["album"]["images"][0]["url"])
        
        if type == "artist":
            followers = item.get('followers', {}).get('total', 0)
            genres = ', '.join(item.get('genres', []))
            popularity = item.get('popularity', None)
            emb.description = f"Followers: {followers:,}\nGenres: {genres}"
            if popularity is not None:
                emb.add_field(name="Popularity (0-100)", value=str(popularity), inline=True)
            
            # Get top tracks for artist
            top_tracks = spotify_artist_top_tracks(item['id'])[:5]
            if top_tracks:
                top_tracks_str = "\n".join([
                    f"{i+1}. {track['name']} ([Spotify]({track['external_urls']['spotify']}))" 
                    for i, track in enumerate(top_tracks)
                ])
                emb.add_field(name="Top Tracks", value=top_tracks_str, inline=False)
        
        elif type == "track":
            artists = ', '.join(a['name'] for a in item['artists'])
            album = item['album']['name']
            popularity = item.get('popularity', None)
            release_date = item['album'].get('release_date', 'Unknown')
            explicit = 'Yes' if item.get('explicit') else 'No'
            track_number = item.get('track_number', 'Unknown')
            duration_ms = item.get('duration_ms', 0)
            duration_min = duration_ms // 60000
            duration_sec = (duration_ms % 60000) // 1000
            duration_str = f"{duration_min}:{duration_sec:02d}"
            
            emb.description = f"By {artists} | Album: {album}"
            emb.add_field(name="Duration", value=duration_str, inline=True)
            emb.add_field(name="Release Date", value=release_date, inline=True)
            emb.add_field(name="Explicit", value=explicit, inline=True)
            emb.add_field(name="Track Number", value=str(track_number), inline=True)
            if popularity is not None:
                emb.add_field(name="Popularity (0-100)", value=str(popularity), inline=True)
        
        await interaction.response.send_message(embed=emb)


async def setup(bot):
    """Setup function for the cog."""
    await bot.add_cog(Spotify(bot))
    """Spotify integration commands."""
    
    def __init__(self, bot):
        self.bot = bot

    def _make_single_field_embed(self, title, items, item_type, image_url=None, page_idx=0, total_pages=1):
        """Create a single field embed for Spotify items."""
        emb = discord.Embed(title=title, color=discord.Color.purple())
        if image_url:
            emb.set_thumbnail(url=image_url)
        value = "\n".join(
            f"{page_idx*5+j+1}. {item['name']}" + 
            (f" by {', '.join(a['name'] for a in item['artists'])}" if item_type == "tracks" else "") + 
            f" ([Spotify]({item['external_urls']['spotify']}))"
            for j, item in enumerate(items)
        )
        emb.add_field(name=f"{item_type.title()} {page_idx*5+1}-{page_idx*5+len(items)}", value=value, inline=False)
        emb.set_footer(text=f"Page {page_idx+1}/{total_pages}")
        return emb

    def _paginate_items(self, items, per_page=5):
        """Paginate items into chunks."""
        return [items[i:i+per_page] for i in range(0, len(items), per_page)]

    @app_commands.command(name="link", description="Link your Spotify account to the bot.")
    async def link(self, interaction: discord.Interaction):
        """Link user's Spotify account."""
        url = get_spotify_auth_url(str(interaction.user.id))
        await interaction.response.send_message(
            f"[Click here to link your Spotify account]({url})\nAfter authorizing, your Spotify will be linked.",
            ephemeral=True
        )
        await asyncio.sleep(3)
        tokens = get_user_tokens(str(interaction.user.id))
        if tokens:
            store_spotify_tokens(str(interaction.user.id), tokens, interaction.user.name)

    @app_commands.command(name="unlink", description="Unlink your Spotify account from the bot.")
    async def unlink(self, interaction: discord.Interaction):
        """Unlink user's Spotify account."""
        remove_spotify_tokens(str(interaction.user.id))
        await interaction.response.send_message(
            "Your Spotify account has been unlinked.", ephemeral=True
        )

    @app_commands.command(name="registered", description="Check if your Spotify account is linked.")
    async def registered(self, interaction: discord.Interaction):
        """Check if user's Spotify account is registered."""
        tokens = get_user_tokens(str(interaction.user.id))
        if tokens:
            tokens = refresh_user_tokens(str(interaction.user.id), tokens)
            profile = spotify_user_profile(tokens)
            if profile and "display_name" in profile and "external_urls" in profile:
                discord_mention = interaction.user.mention
                profile_url = profile["external_urls"].get("spotify", "")
                
                # Extract Spotify username from the profile URL
                import re
                match = re.search(r"/user/([^/?]+)", profile_url)
                spotify_username = match.group(1) if match else profile_url
                image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
                
                emb = discord.Embed(
                    title=f"Spotify account registered for {discord_mention} as: {spotify_username}",
                    description=f"View profile in Spotify here:\n{profile_url}",
                    color=discord.Color.purple()
                )
                if image_url:
                    emb.set_thumbnail(url=image_url)
                await interaction.response.send_message(embed=emb)
            else:
                await interaction.response.send_message(
                    "Your Spotify account is linked, but profile info could not be retrieved.",
                    ephemeral=False
                )
        else:
            await interaction.response.send_message(
                "No Spotify account found. Use `/spotify link` to connect your account.",
                ephemeral=False
            )

    @app_commands.command(name="top", description="Show your top Spotify artists or tracks.")
    @app_commands.describe(
        type="Choose between artists or tracks.",
        data_range="Data range: 1 year, 6 months, 4 weeks",
        limit="How many to list (up to 50)",
        user="Select a user (optional, defaults to yourself)"
    )
    async def top(
        self,
        interaction: discord.Interaction,
        type: Literal["artists", "tracks"],
        data_range: Literal["1 year", "6 months", "4 weeks"] = "1 year",
        limit: app_commands.Range[int, 1, 50] = 10,
        user: discord.User = None
    ):
        """Show user's top Spotify content."""
        user = user or interaction.user
        range_map = {"1 year": "long_term", "6 months": "medium_term", "4 weeks": "short_term"}
        spotify_range = range_map[data_range]
        tokens = get_user_tokens(str(user.id))
        if not tokens:
            await interaction.response.send_message(
                f"No Spotify account registered for {user.mention}. They need to use `/spotify link`.", 
                ephemeral=True
            )
            return
        
        tokens = refresh_user_tokens(str(user.id), tokens)
        data = spotify_user_top(tokens, type, spotify_range, limit)
        profile = spotify_user_profile(tokens)
        image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
        items = data.get("items", [])
        
        if not items:
            await interaction.response.send_message("No data found.", ephemeral=True)
            return
        
        pages = self._paginate_items(items, 5)
        
        def make_embed(page_idx):
            emb = self._make_single_field_embed(
                f"Top {type.title()} for {user.display_name} ({data_range})",
                pages[page_idx],
                type,
                image_url=image_url,
                page_idx=page_idx,
                total_pages=len(pages)
            )
            emb.add_field(name="Data Range", value=data_range, inline=False)
            return emb
        
        view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
        if view:
            await interaction.response.send_message(embed=make_embed(0), view=view)
        else:
            await interaction.response.send_message(embed=make_embed(0))

    @app_commands.command(name="recents", description="Show your recently played Spotify tracks.")
    @app_commands.describe(
        limit="How many to retrieve (up to 50, default 5)", 
        user="Select a user (optional, defaults to yourself)"
    )
    async def recents(self, interaction: discord.Interaction, limit: int = 5, user: discord.User = None):
        """Show user's recent Spotify tracks."""
        user = user or interaction.user
        tokens = get_user_tokens(str(user.id))
        if not tokens:
            await interaction.response.send_message(
                f"No Spotify account registered for {user.mention}. They need to use `/spotify link`.", 
                ephemeral=True
            )
            return
        
        tokens = refresh_user_tokens(str(user.id), tokens)
        data = spotify_user_recent(tokens, limit)
        profile = spotify_user_profile(tokens)
        image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
        items = data.get("items", [])
        
        if not items:
            await interaction.response.send_message("No recent tracks found.", ephemeral=True)
            return
        
        tracks = [item["track"] for item in items]
        pages = self._paginate_items(tracks, 5)
        
        def make_embed(page_idx):
            return self._make_single_field_embed(
                f"Recent Tracks for {user.display_name}",
                pages[page_idx],
                "tracks",
                image_url=image_url,
                page_idx=page_idx,
                total_pages=len(pages)
            )
        
        view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
        if view:
            await interaction.response.send_message(embed=make_embed(0), view=view)
        else:
            await interaction.response.send_message(embed=make_embed(0))

    @app_commands.command(name="recommend", description="Get Spotify recommendations based on your habits.")
    @app_commands.describe(
        limit="How many to recommend (up to 50, default 5)", 
        user="Select a user (optional, defaults to yourself)"
    )
    async def recommend(self, interaction: discord.Interaction, limit: int = 5, user: discord.User = None):
        """Get Spotify recommendations for user."""
        user = user or interaction.user
        tokens = get_user_tokens(str(user.id))
        if not tokens:
            await interaction.response.send_message(
                f"No Spotify account registered for {user.mention}. They need to use `/spotify link`.", 
                ephemeral=True
            )
            return
        
        tokens = refresh_user_tokens(str(user.id), tokens)
        data = spotify_user_recommend(tokens)
        profile = spotify_user_profile(tokens)
        image_url = profile["images"][0]["url"] if profile.get("images") and len(profile["images"]) > 0 else None
        tracks = data.get("tracks", [])
        
        if not tracks:
            await interaction.response.send_message("No recommendations found.", ephemeral=True)
            return
        
        pages = self._paginate_items(tracks, 5)
        
        def make_embed(page_idx):
            return self._make_single_field_embed(
                f"Recommended Tracks for {user.display_name}",
                pages[page_idx],
                "tracks",
                image_url=image_url,
                page_idx=page_idx,
                total_pages=len(pages)
            )
        
        view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
        if view:
            await interaction.response.send_message(embed=make_embed(0), view=view)
        else:
            await interaction.response.send_message(embed=make_embed(0))

    @app_commands.command(name="search", description="Search Spotify for album, artist, or track.")
    @app_commands.describe(
        type="What to search (album, artist, track)", 
        query="Search term (track name, album title, or artist name)",
        artist="Artist name (optional, for more specific track/album searches)"
    )
    async def search(self, interaction: discord.Interaction, type: Literal["album", "artist", "track"], query: str, artist: str = None):
        """Search Spotify for content."""
        # Build the search query with artist if provided
        if artist and type in ["track", "album"]:
            search_query = f"{query} artist:{artist}"
        else:
            search_query = query
        
        data = spotify_search(search_query, type)
        if type == "album":
            items = data.get("albums", {}).get("items", [])
        elif type == "artist":
            items = data.get("artists", {}).get("items", [])
        else:
            items = data.get("tracks", {}).get("items", [])
        
        if not items:
            await interaction.response.send_message("No results found.", ephemeral=True)
            return
        
        item = items[0]
        
        # For albums, fetch full album info (with tracks)
        if type == "album":
            import requests
            album_id = item["id"]
            token = get_app_access_token()
            album_url = f"https://api.spotify.com/v1/albums/{album_id}"
            resp = requests.get(album_url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                album_full = resp.json()
                tracks = album_full.get("tracks", {}).get("items", [])
            else:
                album_full = item
                tracks = []
            
            # Build pages: first page is album info, rest are track pages
            pages = []
            
            # Album info page
            emb = discord.Embed(
                title=item["name"],
                color=discord.Color.purple(),
                url=item["external_urls"]["spotify"]
            )
            if item.get("images"):
                emb.set_thumbnail(url=item["images"][0]["url"])
            
            artists = ', '.join(a['name'] for a in item['artists'])
            release_date = item.get('release_date', 'Unknown')
            total_tracks = item.get('total_tracks', 'Unknown')
            album_type = item.get('album_type', 'Unknown').title()
            popularity = item.get('popularity', None)
            genres = ', '.join(item.get('genres', [])) if item.get('genres') else None
            label = item.get('label') or album_full.get('label')
            
            emb.description = f"By {artists}"
            emb.add_field(name="Release Date", value=release_date, inline=True)
            emb.add_field(name="Total Tracks", value=str(total_tracks), inline=True)
            emb.add_field(name="Album Type", value=album_type, inline=True)
            if popularity is not None:
                emb.add_field(name="Popularity (0-100)", value=str(popularity), inline=True)
            if genres:
                emb.add_field(name="Genres", value=genres, inline=True)
            if label:
                emb.add_field(name="Label", value=label, inline=True)
            pages.append(emb)
            
            # Track pages
            track_names = [f"{i+1}. {t['name']}" for i, t in enumerate(tracks)]
            for i in range(0, len(track_names), 10):
                emb = discord.Embed(
                    title=f"{item['name']} — Tracks {i+1}-{min(i+10, len(track_names))}",
                    color=discord.Color.purple(),
                    url=item["external_urls"]["spotify"]
                )
                left = track_names[i:i+5]
                right = track_names[i+5:i+10]
                if left:
                    emb.add_field(name="Tracks", value="\n".join(left), inline=True)
                if right:
                    emb.add_field(name="Tracks", value="\n".join(right), inline=True)
                pages.append(emb)
            
            def make_embed(page_idx):
                return pages[page_idx]
            
            view = PaginationView(make_embed, len(pages)) if len(pages) > 1 else None
            if view:
                await interaction.response.send_message(embed=make_embed(0), view=view)
            else:
                await interaction.response.send_message(embed=make_embed(0))
            return
        
        # Handle artist/track results
        emb = discord.Embed(
            title=item["name"],
            color=discord.Color.purple(),
            url=item["external_urls"]["spotify"]
        )
        
        if type == "artist" and item.get("images"):
            emb.set_thumbnail(url=item["images"][0]["url"])
        elif type == "track" and item["album"].get("images"):
            emb.set_thumbnail(url=item["album"]["images"][0]["url"])
        
        if type == "artist":
            followers = item.get('followers', {}).get('total', 0)
            genres = ', '.join(item.get('genres', []))
            popularity = item.get('popularity', None)
            emb.description = f"Followers: {followers:,}\nGenres: {genres}"
            if popularity is not None:
                emb.add_field(name="Popularity (0-100)", value=str(popularity), inline=True)
            
            # Get top tracks for artist
            top_tracks = spotify_artist_top_tracks(item['id'])[:5]
            if top_tracks:
                top_tracks_str = "\n".join([
                    f"{i+1}. {track['name']} ([Spotify]({track['external_urls']['spotify']}))" 
                    for i, track in enumerate(top_tracks)
                ])
                emb.add_field(name="Top Tracks", value=top_tracks_str, inline=False)
        
        elif type == "track":
            artists = ', '.join(a['name'] for a in item['artists'])
            album = item['album']['name']
            popularity = item.get('popularity', None)
            release_date = item['album'].get('release_date', 'Unknown')
            explicit = 'Yes' if item.get('explicit') else 'No'
            track_number = item.get('track_number', 'Unknown')
            duration_ms = item.get('duration_ms', 0)
            duration_min = duration_ms // 60000
            duration_sec = (duration_ms % 60000) // 1000
            duration_str = f"{duration_min}:{duration_sec:02d}"
            
            emb.description = f"By {artists} | Album: {album}"
            emb.add_field(name="Duration", value=duration_str, inline=True)
            emb.add_field(name="Release Date", value=release_date, inline=True)
            emb.add_field(name="Explicit", value=explicit, inline=True)
            emb.add_field(name="Track Number", value=str(track_number), inline=True)
            if popularity is not None:
                emb.add_field(name="Popularity (0-100)", value=str(popularity), inline=True)
        
        await interaction.response.send_message(embed=emb)


async def setup(bot):
    await bot.add_cog(Spotify(bot))
