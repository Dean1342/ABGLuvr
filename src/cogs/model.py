import discord
from discord.ext import commands
from discord import app_commands
from utils.conversation.context import user_models, MODELS, resolve_model_name
from utils.conversation.channel_context import reset_context
from utils.conversation.settings import save_user_setting

class Model(commands.GroupCog, name="model"):
    # Handles model switching commands
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="selected", description="Show your currently selected AI model")
    async def selected(self, interaction: discord.Interaction):
        # Show the user's current model
        key = (interaction.user.id, interaction.channel_id)
        model = resolve_model_name(user_models.get(key))
        model_info = MODELS[model]
        
        # Create color based on model type
        if not model.startswith("GPT-4.1"):
            color = discord.Color.from_rgb(255, 134, 159)  # Pink gradient color from screenshot
        else:  # GPT-4.1
            color = discord.Color.from_rgb(134, 159, 255)  # Blue gradient color from screenshot
        
        embed = discord.Embed(
            title=f"🤖 Current Model: {model_info['name']}",
            description=model_info['description'],
            color=color
        )
        
        # Add reasoning and speed indicators
        embed.add_field(name="Reasoning", value=model_info['reasoning'], inline=True)
        embed.add_field(name="Speed", value=model_info['speed'], inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for spacing
        
        # Pricing information
        embed.add_field(name="Input Cost", value=model_info['input_cost'], inline=True)
        embed.add_field(name="Cached Input", value=model_info['cached_input_cost'], inline=True)
        embed.add_field(name="Output Cost", value=model_info['output_cost'], inline=True)
        
        # Context and technical details
        embed.add_field(name="Context Window", value=f"{model_info['context_window']} tokens", inline=True)
        embed.add_field(name="Max Output", value=f"{model_info['max_output']} tokens", inline=True)
        embed.add_field(name="Knowledge Cutoff", value=model_info['knowledge_cutoff'], inline=True)
        
        embed.add_field(name="Model ID", value=f"`{model_info['id']}`", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="options", description="Change your current AI model")
    @app_commands.describe(model="AI model to switch to")
    @app_commands.choices(model=[
        app_commands.Choice(name=label, value=label) for label in MODELS.keys()
    ])
    async def options(self, interaction: discord.Interaction, model: str):
        # Change the user's model
        if model not in MODELS:
            await interaction.response.send_message(
                f"Invalid model. Available: {', '.join(MODELS.keys())}"
            )
            return
        
        key = (interaction.user.id, interaction.channel_id)
        user_models[key] = model
        
        model_info = MODELS[model]
        
        # Create color based on model type
        if not model.startswith("GPT-4.1"):
            color = discord.Color.from_rgb(255, 134, 159)  # Pink gradient color
        else:  # GPT-4.1
            color = discord.Color.from_rgb(134, 159, 255)  # Blue gradient color
        
        embed = discord.Embed(
            title=f"✅ Model Changed",
            description=f"Successfully switched to **{model_info['name']}**",
            color=color
        )
        embed.add_field(name="Description", value=model_info['description'], inline=False)
        embed.add_field(name="Reasoning", value=model_info['reasoning'], inline=True)
        embed.add_field(name="Speed", value=model_info['speed'], inline=True)
        embed.add_field(name="\u200b", value="\u200b", inline=True)  # Empty field for spacing
        embed.add_field(name="Model ID", value=f"`{model_info['id']}`", inline=False)
        
        await interaction.response.send_message(embed=embed)
        await save_user_setting(interaction.user.id, interaction.channel_id, model=model)

    @app_commands.command(name="reset", description="Reset your conversation history to start fresh")
    async def reset(self, interaction: discord.Interaction):
        # Context comes from the channel itself, so "reset" means: when answering this
        # user here, ignore everything said before now.
        reset_context(interaction.user.id, interaction.channel_id)
        embed = discord.Embed(
            title="🔄 Conversation Reset",
            description="Starting fresh! When you talk to me in this channel, I'll ignore everything said before now.",
            color=discord.Color.green()
        )
        embed.add_field(
            name="What was kept:",
            value="• Your selected persona\n• Your selected AI model",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Model(bot))
