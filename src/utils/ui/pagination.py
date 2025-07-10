import discord


# Provides a generic pagination view for Discord embeds
class PaginationView(discord.ui.View):
    def __init__(self, make_embed_func, page_count, timeout=300):
        super().__init__(timeout=timeout)
        self.make_embed = make_embed_func
        self.page_count = page_count
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        if self.page_count > 1:
            self.add_item(PaginationButton(self, "prev", "◀️"))
            self.add_item(PaginationButton(self, "next", "▶️"))

    async def update(self, interaction):
        await interaction.response.edit_message(embed=self.make_embed(self.current_page), view=self)


class PaginationButton(discord.ui.Button):
    def __init__(self, parent_view, direction, emoji):
        super().__init__(style=discord.ButtonStyle.primary, emoji=emoji)
        self.parent_view = parent_view
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        if self.direction == "prev":
            self.parent_view.current_page = (self.parent_view.current_page - 1) % self.parent_view.page_count
        else:
            self.parent_view.current_page = (self.parent_view.current_page + 1) % self.parent_view.page_count
        await self.parent_view.update(interaction)


SpotifyPaginationView = PaginationView
SpotifyPaginationButton = PaginationButton
