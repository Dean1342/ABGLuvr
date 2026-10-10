# The Deep / Quick buttons on the bot's "worth a proper look?" reply (utils/ai/turn.py).
#
# Only the person who asked can pick: anyone else gets a private "not yours" note and
# nothing happens. The first pick wins (a lock, so a double click or both buttons at once
# can't start two runs); the buttons are then disabled. No pick within TIMEOUT counts as
# Quick, so the question still gets its normal answer.
#
# Views live in memory: after a restart old buttons stop working (Discord shows
# "This interaction failed"). The question can just be asked again.
import asyncio

import discord

TIMEOUT = 60  # seconds

_running = set()  # strong refs to launched answers so they aren't garbage-collected mid-run


class InvestigationChoice(discord.ui.View):
    def __init__(self, requester, on_choice, timeout=TIMEOUT):
        # on_choice(choice) is awaited once, in the background: "deep", "quick" or "timeout".
        super().__init__(timeout=timeout)
        self.requester = requester
        self.on_choice = on_choice
        self.message = None  # set by the sender, for editing on timeout
        self.chosen = None
        self._lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester.id:
            return True
        await interaction.response.send_message(
            f"Only {self.requester.display_name} can pick this one. Ask the bot yourself to get your own.",
            ephemeral=True)
        print(f"[investigate] ignored a click from {interaction.user.id} (requester is {self.requester.id})")
        return False

    @discord.ui.button(label="Deep investigation", emoji="✅", style=discord.ButtonStyle.success)
    async def deep(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "deep", button)

    @discord.ui.button(label="Quick answer", emoji="⚡", style=discord.ButtonStyle.secondary)
    async def quick(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._pick(interaction, "quick", button)

    async def _pick(self, interaction, choice, button):
        async with self._lock:
            if self.chosen is not None:
                await interaction.response.defer()  # already decided; just acknowledge the click
                return
            self.chosen = choice
        self.stop()
        self._disable(picked=button)
        try:
            await interaction.response.edit_message(view=self)  # acknowledges within Discord's 3s
        except discord.HTTPException:
            pass
        self._launch(choice)

    async def on_timeout(self):
        async with self._lock:
            if self.chosen is not None:
                return
            self.chosen = "timeout"
        self._disable()
        if self.message is not None:
            try:
                await self.message.edit(content=self.message.content + "\n-# No pick, so here's the quick answer.",
                                        view=self)
            except discord.HTTPException:
                pass
        self._launch("timeout")

    def _disable(self, picked=None):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                if item is not picked:
                    item.style = discord.ButtonStyle.secondary

    def _launch(self, choice):
        task = asyncio.create_task(self._run(choice))
        _running.add(task)
        task.add_done_callback(_running.discard)

    async def _run(self, choice):
        try:
            await self.on_choice(choice)
        except Exception as e:
            print(f"[investigate] {choice} run failed: {type(e).__name__}: {e}")
