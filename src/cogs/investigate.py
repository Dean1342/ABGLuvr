import discord
from discord import app_commands
from discord.ext import commands

from utils.ai.turn import answer, prepare_turn

# /investigate: a deep investigation straight away (typing the command is the yes; the
# Deep/Quick buttons are only for when the bot suggests one in normal chat). The question is
# posted as a visible message while it works, and the findings then replace that message.
# Links in the question are read like links in a message.


class _QuestionMessage:
    # Stands in for a Discord message in the normal pipeline (utils/ai/turn.py): the posted
    # question's id, channel and time, with the person who ran the command as its author.
    def __init__(self, posted, user, question):
        self.id, self.channel, self.guild, self.created_at = posted.id, posted.channel, posted.guild, posted.created_at
        self.author = user
        self.content = self.clean_content = question
        self.reference = None
        self.attachments, self.embeds, self.stickers, self.mentions = [], [], [], []
        self.webhook_id = None
        self.interaction_metadata = None

    async def reply(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)


class _AnswerInPlace:
    # Where answer() puts its reply: into the "asked for a deep investigation" message (an
    # edit), not a second message; Discord shows the command and question above it anyway.
    # An answer too long for one message continues in new messages after it.
    def __init__(self, posted):
        self.posted = posted
        self.channel = self
        self._filled = False

    async def reply(self, content, **kwargs):
        return await self.send(content, **kwargs)

    async def send(self, content, **kwargs):
        if self._filled:
            return await self.posted.channel.send(content, **kwargs)
        self._filled = True
        kwargs.pop("mention_author", None)
        await self.posted.edit(content=content, **kwargs)
        return self.posted

    def typing(self):
        return self.posted.channel.typing()


class Investigate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="investigate",
                          description="Deep investigation: finds the original sources and cross-checks a claim, post or link")
    @app_commands.describe(question="What to look into. Paste the link if it's about a post, video or article.")
    async def investigate(self, interaction: discord.Interaction, question: str):
        if interaction.guild is None:
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**{interaction.user.display_name}** asked for a deep investigation:\n> {question[:1800]}",
            allowed_mentions=discord.AllowedMentions.none())
        posted = await interaction.original_response()
        print(f"[investigate] /investigate by {interaction.user.id}: {question[:120]}")
        turn = await prepare_turn(_QuestionMessage(posted, interaction.user, question), self.bot)
        await answer(turn, _AnswerInPlace(posted), profile="investigate", offers_allowed=False)


async def setup(bot):
    await bot.add_cog(Investigate(bot))
