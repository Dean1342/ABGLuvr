import os
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

_HEROKU_API = "https://api.heroku.com"


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="restart", description="Restart the bot (owner only)")
    @app_commands.default_permissions(administrator=True)
    async def restart(self, interaction: discord.Interaction):
        # Owner = owner of the bot application in the Discord Developer Portal
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("You can't use this command.", ephemeral=True)
            return

        # Only set on Heroku — the local test bot can never restart production
        api_key  = os.getenv("HEROKU_API_KEY", "").strip()
        app_name = os.getenv("HEROKU_APP_NAME", "").strip()
        if not api_key or not app_name:
            await interaction.response.send_message(
                "Heroku restart isn't configured here (HEROKU_API_KEY / HEROKU_APP_NAME not set).",
                ephemeral=True,
            )
            return

        # Reply before restarting — Heroku kills this process mid-command on success
        await interaction.response.send_message("♻️ Restarting — back in ~15–30 seconds.", ephemeral=True)
        print(f"[admin] restart requested by {interaction.user.id}")

        headers = {
            "Accept": "application/vnd.heroku+json; version=3",
            "Authorization": f"Bearer {api_key}",
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.delete(f"{_HEROKU_API}/apps/{app_name}/dynos", headers=headers) as resp:
                    status = resp.status
        except Exception as e:
            print(f"[admin] restart request failed: {type(e).__name__}")
            await interaction.edit_original_response(content="Restart failed — couldn't reach the Heroku API.")
            return

        if status != 202:
            print(f"[admin] restart failed: Heroku returned {status}")
            hint = {
                401: " Check HEROKU_API_KEY (expired or revoked token?).",
                403: " The token doesn't have permission to manage this app.",
                404: " Check HEROKU_APP_NAME, and that HEROKU_API_KEY is from the account that owns the app.",
            }.get(status, "")
            await interaction.edit_original_response(content=f"Restart failed — Heroku returned {status}.{hint}")


async def setup(bot):
    await bot.add_cog(Admin(bot))
