import discord
from discord.ext import commands
from discord import app_commands
from utils.conversation import memory

# /memory: see and delete what the bot remembers (utils/conversation/memory.py) without
# going through the AI. Anyone in the server can forget any fact; deletions are logged.

_EMBED_LIMIT = 4000
_UNAVAILABLE = "Memory storage isn't available right now. Try again in a bit."


def _clip(lines, limit=_EMBED_LIMIT):
    out, used = [], 0
    for line in lines:
        if used + len(line) + 1 > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


class Memory(commands.GroupCog, name="memory"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="show", description="Show what the bot remembers about you (or someone) and this server")
    @app_commands.describe(user="Whose facts to show (defaults to you)")
    async def show(self, interaction: discord.Interaction, user: discord.Member | None = None):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        facts = await memory.get_facts(interaction.guild_id)
        if facts is None:
            await interaction.followup.send(_UNAVAILABLE, ephemeral=True)
            return
        facts = memory.visible_facts(facts, interaction.channel_id)

        def name_of(user_id):
            member = interaction.guild.get_member(user_id)
            return memory.member_label(member) if member else f"user {user_id}"

        about = [f for f in facts if target.id in memory.subjects(f)]
        shared = [f for f in facts if f["scope"] != "user"]
        embed = discord.Embed(title="🧠 Memory", color=discord.Color.purple())
        embed.add_field(
            name=f"About {target.display_name}",
            value=_clip([memory.fact_line(f, name_of) for f in about], 1000) or "Nothing saved.",
            inline=False,
        )
        embed.add_field(
            name="Server and this channel",
            value=_clip([memory.fact_line(f, name_of) for f in shared], 1000) or "Nothing saved.",
            inline=False,
        )
        others = len(facts) - len(about) - len(shared)
        embed.set_footer(text=f"{others} fact(s) about other people • /memory forget <id> deletes one")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="forget", description="Delete a remembered fact by its id")
    @app_commands.describe(fact_id="The number shown as [#id] in /memory show")
    async def forget(self, interaction: discord.Interaction, fact_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await memory.forget(interaction.guild_id, [fact_id])
        except memory.MemoryUnavailable:
            await interaction.followup.send(_UNAVAILABLE, ephemeral=True)
            return
        if not deleted:
            await interaction.followup.send(f"There's no fact #{fact_id} in this server.", ephemeral=True)
            return
        print(f"[memory] guild {interaction.guild_id}: user {interaction.user.id} forgot #{fact_id} via /memory")
        await interaction.followup.send(f"Forgot fact #{fact_id}.", ephemeral=True)

    @app_commands.command(name="summary", description="Show the bot's summary of older conversation in this channel")
    async def summary(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            row = await memory.get_summary(interaction.channel_id)
        except memory.MemoryUnavailable:
            await interaction.followup.send(_UNAVAILABLE, ephemeral=True)
            return
        if not row:
            await interaction.followup.send("No summary for this channel yet. One builds up as older messages scroll out of my recent context.", ephemeral=True)
            return
        text = row["summary"]
        embed = discord.Embed(
            title="📜 Channel summary",
            description=text if len(text) <= _EMBED_LIMIT else text[:_EMBED_LIMIT] + "…",
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"Covers messages through {memory.db.parse_timestamp(row['up_to_at']):%b %d, %Y %H:%M} UTC")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Memory(bot))
