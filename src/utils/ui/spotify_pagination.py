import discord
import requests
from ..integrations.spotify.spotify import get_app_access_token, get_full_album_details


class SpotifyAlbumPaginationView(discord.ui.View):
    def __init__(self, album_data, timeout=300):
        super().__init__(timeout=timeout)
        self.album_data = album_data
        self.current_page = "overview"  # "overview" or "tracks"
        self.tracks_data = None  # Cache for tracks data
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        
        # Show Overview button if we're on tracks page
        if self.current_page == "tracks":
            self.add_item(SpotifyPageButton(self, "overview", "💿 Overview", discord.ButtonStyle.secondary))
        
        # Show Tracks button if we have tracks and we're on overview
        total_tracks = self.album_data.get("total_tracks", 0)
        if total_tracks > 0 and self.current_page == "overview":
            self.add_item(SpotifyPageButton(self, "tracks", "🎵 Tracks", discord.ButtonStyle.primary))

    async def fetch_tracks_data(self):
        """Fetch full album data including tracks if not already cached."""
        if self.tracks_data is not None:
            return self.tracks_data
        
        album_id = self.album_data["id"]
        full_album = get_full_album_details(album_id)
        
        if full_album:
            self.tracks_data = full_album.get("tracks", {}).get("items", [])
            # Update album_data with full details if we have them
            self.album_data.update(full_album)
        else:
            self.tracks_data = []
        
        return self.tracks_data

    async def create_overview_embed(self):
        """Create the overview embed for the album."""
        album = self.album_data
        
        # Get artist names
        artists = ", ".join([a["name"] for a in album["artists"]])
        
        # Format title with release year if available
        title = album["name"]
        release_date = album.get("release_date", "")
        if release_date:
            year = release_date.split("-")[0]
            title += f" ({year})"
        
        # Create embed for album details
        emb = discord.Embed(
            title=title,
            description=f"By {artists}",
            color=discord.Color.purple(),
            url=album["external_urls"]["spotify"]
        )
        
        # Add album cover as thumbnail
        if album.get("images"):
            emb.set_thumbnail(url=album["images"][0]["url"])
        
        # Release Date
        if release_date:
            emb.add_field(name="Release Date", value=release_date, inline=True)
        
        # Album Type
        album_type = album.get("album_type", "Unknown").title()
        emb.add_field(name="Type", value=album_type, inline=True)
        
        # Total Tracks
        total_tracks = album.get("total_tracks", "Unknown")
        emb.add_field(name="Tracks", value=str(total_tracks), inline=True)
        
        # Popularity
        popularity = album.get("popularity")
        if popularity is not None:
            emb.add_field(name="Popularity", value=f"{popularity}/100", inline=True)
        
        # Genres (if available)
        genres = album.get("genres", [])
        if genres:
            genre_names = ", ".join(genres)
            emb.add_field(name="Genres", value=genre_names, inline=True)
        
        # Label (if available)
        label = album.get("label")
        if label:
            emb.add_field(name="Label", value=label, inline=True)
        
        # Markets/Availability
        available_markets = album.get("available_markets", [])
        if available_markets:
            market_count = len(available_markets)
            emb.add_field(name="Availability", value=f"{market_count} countries", inline=True)
        
        # Total duration (calculate from tracks if available)
        if hasattr(self, 'tracks_data') and self.tracks_data:
            total_duration_ms = sum(track.get("duration_ms", 0) for track in self.tracks_data)
            total_minutes = total_duration_ms // 60000
            total_seconds = (total_duration_ms % 60000) // 1000
            duration_str = f"{total_minutes}:{total_seconds:02d}"
            emb.add_field(name="Duration", value=duration_str, inline=True)
        
        # Copyright (if available)
        copyrights = album.get("copyrights", [])
        if copyrights:
            copyright_text = copyrights[0].get("text", "")
            if len(copyright_text) > 100:
                copyright_text = copyright_text[:100] + "..."
            emb.add_field(name="Copyright", value=copyright_text, inline=False)
        
        # Spotify link
        emb.add_field(name="Links", value=f"[🎵 Listen on Spotify]({album['external_urls']['spotify']})", inline=False)
        
        return emb

    async def create_tracks_embed(self):
        """Create the tracks overview embed with all tracks listed."""
        album = self.album_data
        
        # Get artist names for the title
        artists = ", ".join([a["name"] for a in album["artists"]])
        total_tracks = album.get("total_tracks", 0)
        
        emb = discord.Embed(
            title=f"{album['name']} - {total_tracks} Track{'s' if total_tracks != 1 else ''}",
            description=f"By {artists}",
            color=discord.Color.green(),
            url=album["external_urls"]["spotify"]
        )
        
        # Add album cover as thumbnail
        if album.get("images"):
            emb.set_thumbnail(url=album["images"][0]["url"])
        
        # Fetch tracks data
        tracks = await self.fetch_tracks_data()
        
        if tracks:
            # Group tracks into inline fields (5 tracks per field)
            for i in range(0, len(tracks), 5):
                group = tracks[i:i+5]
                start_track = i + 1
                end_track = min(i + 5, len(tracks))
                
                # Format tracks with track number, name, and duration
                track_list = []
                for track in group:
                    duration_ms = track.get("duration_ms", 0)
                    duration_min = duration_ms // 60000
                    duration_sec = (duration_ms % 60000) // 1000
                    duration_str = f"{duration_min}:{duration_sec:02d}"
                    
                    track_num = track.get("track_number", "?")
                    track_name = track["name"]
                    
                    # Add explicit indicator if needed
                    explicit = " 🅴" if track.get("explicit") else ""
                    
                    # Create clickable link to track
                    track_url = track.get("external_urls", {}).get("spotify", "")
                    if track_url:
                        track_display = f"[{track_name}]({track_url})"
                    else:
                        track_display = track_name
                    
                    track_list.append(f"**{track_num}.** {track_display} ({duration_str}){explicit}")
                
                field_name = f"Tracks {start_track}-{end_track}" if end_track > start_track else f"Track {start_track}"
                emb.add_field(
                    name=field_name,
                    value="\n".join(track_list),
                    inline=True
                )
        else:
            emb.add_field(name="Error", value="Could not load track information", inline=False)
        
        return emb

    async def get_current_embed(self):
        """Get the embed for the current page."""
        if self.current_page == "overview":
            return await self.create_overview_embed()
        elif self.current_page == "tracks":
            return await self.create_tracks_embed()
        else:
            # Default to overview for any unknown page
            return await self.create_overview_embed()

    async def update(self, interaction):
        """Update the message with the current page."""
        embed = await self.get_current_embed()
        self.update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)


class SpotifyPageButton(discord.ui.Button):
    def __init__(self, parent_view, page_type, label, style):
        super().__init__(style=style, label=label)
        self.parent_view = parent_view
        self.page_type = page_type

    async def callback(self, interaction: discord.Interaction):
        # For tracks page, defer the response first to prevent timeout
        if self.page_type == "tracks":
            await interaction.response.defer()
            
            # Show loading state
            loading_embed = discord.Embed(
                title="Loading Tracks...",
                description="Fetching track information from Spotify. Please wait...",
                color=discord.Color.yellow()
            )
            await interaction.followup.edit_message(interaction.message.id, embed=loading_embed, view=None)
            
            # Update to tracks page
            self.parent_view.current_page = self.page_type
            embed = await self.parent_view.get_current_embed()
            self.parent_view.update_buttons()
            
            # Edit with final result
            await interaction.followup.edit_message(interaction.message.id, embed=embed, view=self.parent_view)
        else:
            # For other pages, use normal flow
            self.parent_view.current_page = self.page_type
            await self.parent_view.update(interaction)
