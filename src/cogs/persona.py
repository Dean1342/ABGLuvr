import discord
from discord.ext import commands
from discord import app_commands
from utils.conversation.context import user_personas
from utils.conversation.channel_context import reset_context
from utils.conversation.settings import save_user_setting, save_context_reset
from utils.ai.prompts import enabled_personas, resolve_persona

class Persona(commands.GroupCog, name="persona"):
    # Handles persona switching commands
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="selected", description="Show your currently selected persona")
    async def selected(self, interaction: discord.Interaction):
        # Show the user's current persona
        key = (interaction.user.id, interaction.channel_id)
        persona = resolve_persona(user_personas.get(key))
        await interaction.response.send_message(f"Active persona: **{persona}**", ephemeral=True)

    @app_commands.command(name="options", description="Change your current persona")
    @app_commands.describe(persona="Persona to switch to")
    @app_commands.choices(persona=[
        app_commands.Choice(name=label, value=label) for label in enabled_personas()
    ])
    async def options(self, interaction: discord.Interaction, persona: str):
        # Change the user's persona
        match = next((p for p in enabled_personas() if p.lower() == persona.lower()), None)
        if match is None:
            await interaction.response.send_message(
                f"Invalid persona. Available: {', '.join(enabled_personas())}"
            )
            return
        key = (interaction.user.id, interaction.channel_id)
        user_personas[key] = match
        # Start fresh so replies in the old persona's voice don't bleed into the new one.
        reset_at = reset_context(interaction.user.id, interaction.channel_id)
        await interaction.response.send_message(f"Persona changed to **{match}**.")
        await save_user_setting(interaction.user.id, interaction.channel_id, persona=match)
        await save_context_reset(interaction.user.id, interaction.channel_id, reset_at)

async def setup(bot):
    await bot.add_cog(Persona(bot))
