import discord
from discord.ext import commands
from discord import app_commands
from utils.conversation.context import user_personas, user_conversations, GLOBAL_BEHAVIOR, PERSONAS
from utils.conversation.persona_loaders import load_jagbir_persona, load_lemon_persona, load_epoe_persona

class Persona(commands.GroupCog, name="persona"):
    # Handles persona switching commands
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="selected", description="Show your currently selected persona")
    async def selected(self, interaction: discord.Interaction):
        # Show the user's current persona
        key = (interaction.user.id, interaction.channel_id)
        persona = user_personas.get(key, "Default")
        await interaction.response.send_message(f"Active persona: **{persona}**", ephemeral=True)

    @app_commands.command(name="options", description="Change your current persona")
    @app_commands.describe(persona="Persona to switch to")
    @app_commands.choices(persona=[
        app_commands.Choice(name=label, value=label) for label in PERSONAS
    ])
    async def options(self, interaction: discord.Interaction, persona: str):
        # Change the user's persona
        persona_names = [p.lower() for p in PERSONAS.keys()]
        if persona.lower() not in persona_names:
            await interaction.response.send_message(
                f"Invalid persona. Available: {', '.join(PERSONAS.keys())}"
            )
            return
        for key_name in PERSONAS:
            if key_name.lower() == persona.lower():
                key = (interaction.user.id, interaction.channel_id)
                user_personas[key] = key_name
                if key_name == "Jagbir":
                    user_conversations[key] = [{"role": "system", "content": GLOBAL_BEHAVIOR + " " + load_jagbir_persona()}]
                elif key_name == "Lemon":
                    user_conversations[key] = [{"role": "system", "content": GLOBAL_BEHAVIOR + " " + load_lemon_persona()}]
                elif key_name == "Epoe":
                    user_conversations[key] = [{"role": "system", "content": GLOBAL_BEHAVIOR + " " + load_epoe_persona()}]
                else:
                    user_conversations[key] = [{"role": "system", "content": GLOBAL_BEHAVIOR}]
                await interaction.response.send_message(f"Persona changed to **{key_name}**.")
                return

async def setup(bot):
    await bot.add_cog(Persona(bot))
