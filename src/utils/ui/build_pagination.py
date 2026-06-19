import io
import discord

STATUS_EMOJI = {
    'installed': '✅',
    'planned': '📋',
    'ordered': '📦',
    'in_progress': '🔧',
}
MODS_PER_PAGE = 12


def _effective_cost(m: dict) -> float:
    paid = m.get('paid') or 0
    cost = m.get('cost') or 0
    return float(paid) if paid > 0 else float(cost)


class BuildPaginationView(discord.ui.View):
    def __init__(
        self,
        profile: dict,
        mods: list[dict],
        labor: list[dict],
        target_user: discord.User | discord.Member,
        chart_donut: bytes | None = None,
        chart_budget: bytes | None = None,
        chart_timeline: bytes | None = None,
        chart_categories: bytes | None = None,
        timeout: int = 300,
    ):
        super().__init__(timeout=timeout)
        self.profile = profile
        self.mods = mods
        self.labor = labor
        self.target_user = target_user
        self.chart_donut = chart_donut
        self.chart_budget = chart_budget
        self.chart_timeline = chart_timeline
        self.chart_categories = chart_categories

        self.installed_mods = [m for m in mods if m.get('status') == 'installed']
        self.ordered_mods   = [m for m in mods if m.get('status') == 'ordered']
        self.planned_mods   = [m for m in mods if m.get('status') not in ('installed', 'ordered')]

        self.pages = self._build_page_list()
        self.current_idx = 0
        self._update_buttons()

    def _build_page_list(self) -> list[str]:
        pages = ['profile']
        if self.installed_mods:
            n = -(-len(self.installed_mods) // MODS_PER_PAGE)
            for i in range(n):
                pages.append(f'installed_{i}')
        if self.ordered_mods:
            n = -(-len(self.ordered_mods) // MODS_PER_PAGE)
            for i in range(n):
                pages.append(f'ordered_{i}')
        if self.planned_mods:
            n = -(-len(self.planned_mods) // MODS_PER_PAGE)
            for i in range(n):
                pages.append(f'planned_{i}')
        if self.chart_donut:
            pages.append('chart_donut')
        if self.chart_budget:
            pages.append('chart_budget')
        if self.chart_timeline:
            pages.append('chart_timeline')
        if self.chart_categories:
            pages.append('chart_categories')
        return pages

    def _update_buttons(self):
        self.clear_items()
        total = len(self.pages)

        # Row 0: prev / home / next
        if total > 1:
            self.add_item(BuildNavButton(self, 'prev', '◀️', row=0))
            self.add_item(JumpButton(self, 0, '🏠', discord.ButtonStyle.secondary, row=0))
            self.add_item(BuildNavButton(self, 'next', '▶️', row=0))

        # Row 1: section jump buttons
        inst_idx = next((i for i, p in enumerate(self.pages) if p.startswith('installed_')), None)
        ord_idx  = next((i for i, p in enumerate(self.pages) if p.startswith('ordered_')), None)
        plan_idx = next((i for i, p in enumerate(self.pages) if p.startswith('planned_')), None)
        if inst_idx is not None:
            self.add_item(JumpButton(
                self, inst_idx,
                f'✅ Installed ({len(self.installed_mods)})',
                discord.ButtonStyle.success, row=1,
            ))
        if ord_idx is not None:
            self.add_item(JumpButton(
                self, ord_idx,
                f'📦 Ordered ({len(self.ordered_mods)})',
                discord.ButtonStyle.primary, row=1,
            ))
        if plan_idx is not None:
            self.add_item(JumpButton(
                self, plan_idx,
                f'📋 Planned ({len(self.planned_mods)})',
                discord.ButtonStyle.secondary, row=1,
            ))

        # Row 2: chart jump buttons
        for page_key, label, style in [
            ('chart_donut', '📊 Status', discord.ButtonStyle.primary),
            ('chart_budget', '💰 Budget', discord.ButtonStyle.primary),
            ('chart_categories', '🏷️ Categories', discord.ButtonStyle.primary),
        ]:
            idx = next((i for i, p in enumerate(self.pages) if p == page_key), None)
            if idx is not None:
                self.add_item(JumpButton(self, idx, label, style, row=2))

    @property
    def current_page(self) -> str:
        return self.pages[self.current_idx]

    @property
    def _color(self) -> discord.Color:
        raw = self.profile.get('embed_color')
        if raw:
            try:
                return discord.Color(int(raw))
            except Exception:
                pass
        return discord.Color.blurple()

    def _make_profile_embed(self) -> discord.Embed:
        p = self.profile
        purchased = self.installed_mods + self.ordered_mods
        current_cost = sum(_effective_cost(m) for m in purchased)
        planned_cost = sum(_effective_cost(m) for m in self.planned_mods)
        projected = current_cost + planned_cost
        labor_cost = sum(float(l.get('cost') or 0) for l in self.labor)
        truly_installed = sum(1 for m in self.mods if m.get('status') == 'installed')
        completion = (truly_installed / len(self.mods) * 100) if self.mods else 0

        title = f"🚗  {p.get('year', '?')} {p.get('make', '?')} {p.get('model', '?')}"
        emb = discord.Embed(title=title, description=p.get('bio') or '', color=self._color)
        if p.get('thumbnail_url'):
            emb.set_thumbnail(url=p['thumbnail_url'])
        if p.get('car_image_url'):
            emb.set_image(url=p['car_image_url'])

        emb.add_field(name='Current Cost', value=f"${current_cost:,.2f}", inline=True)
        emb.add_field(name='Planned Cost', value=f"${planned_cost:,.2f}", inline=True)
        emb.add_field(name='Projected', value=f"${projected:,.2f}", inline=True)
        emb.add_field(name='Labor', value=f"${labor_cost:,.2f}", inline=True)
        emb.add_field(name='Completion', value=f"{completion:.1f}%", inline=True)
        emb.add_field(name='​', value='​', inline=True)
        emb.set_footer(text=f"Build by {self.target_user.display_name} • Page 1/{len(self.pages)}")
        return emb

    def _make_section_embed(self, section: str, page_num: int) -> discord.Embed:
        p = self.profile
        if section == 'installed':
            mods_list = self.installed_mods
            section_label = '✅ Installed'
        elif section == 'ordered':
            mods_list = self.ordered_mods
            section_label = '📦 Ordered'
        else:
            mods_list = self.planned_mods
            section_label = '📋 Planned'

        start = page_num * MODS_PER_PAGE
        chunk = mods_list[start:start + MODS_PER_PAGE]
        emb = discord.Embed(
            title=f"{section_label} — {p.get('year')} {p.get('make')} {p.get('model')}",
            color=self._color,
        )
        if p.get('thumbnail_url'):
            emb.set_thumbnail(url=p['thumbnail_url'])
        lines = []
        for m in chunk:
            cost = _effective_cost(m)
            name_md = f"[{m['name']}]({m['link']})" if m.get('link') else m['name']
            date_part = f"  _{m['install_date']}_" if m.get('install_date') else ''
            lines.append(
                f"**{name_md}** `[{m.get('category', 'Misc')}]`"
                f" — **${cost:,.2f}**{date_part}"
            )
        emb.description = '\n'.join(lines) if lines else 'No mods in this section.'
        end = min(start + MODS_PER_PAGE, len(mods_list))
        emb.set_footer(
            text=f"Page {self.current_idx + 1}/{len(self.pages)} • "
                 f"Showing {start + 1}–{end} of {len(mods_list)}"
        )
        return emb

    def _make_chart_embed(self, title: str, filename: str) -> discord.Embed:
        emb = discord.Embed(title=title, color=self._color)
        emb.set_image(url=f"attachment://{filename}")
        emb.set_footer(text=f"Page {self.current_idx + 1}/{len(self.pages)}")
        return emb

    def get_current_embed_and_file(self) -> tuple[discord.Embed, discord.File | None]:
        page = self.current_page
        if page == 'profile':
            return self._make_profile_embed(), None
        if page.startswith('installed_'):
            n = int(page.split('_')[1])
            return self._make_section_embed('installed', n), None
        if page.startswith('ordered_'):
            n = int(page.split('_')[1])
            return self._make_section_embed('ordered', n), None
        if page.startswith('planned_'):
            n = int(page.split('_')[1])
            return self._make_section_embed('planned', n), None
        chart_map = {
            'chart_donut': (self.chart_donut, 'chart_donut.png', 'Mod Status Breakdown'),
            'chart_budget': (self.chart_budget, 'chart_budget.png', 'Budget Progress'),
            'chart_timeline': (self.chart_timeline, 'chart_timeline.png', 'Installation Timeline'),
            'chart_categories': (self.chart_categories, 'chart_categories.png', 'Spend by Category'),
        }
        if page in chart_map:
            data, fname, title = chart_map[page]
            file = discord.File(io.BytesIO(data), filename=fname) if data else None
            return self._make_chart_embed(title, fname), file
        return self._make_profile_embed(), None

    async def update(self, interaction: discord.Interaction):
        self._update_buttons()
        embed, file = self.get_current_embed_and_file()
        if file:
            await interaction.response.edit_message(embed=embed, view=self, attachments=[file])
        else:
            await interaction.response.edit_message(embed=embed, view=self, attachments=[])


class BuildNavButton(discord.ui.Button):
    def __init__(self, pag_view: 'BuildPaginationView', direction: str, emoji: str, row: int = 0):
        super().__init__(style=discord.ButtonStyle.primary, emoji=emoji, row=row)
        self.pag_view = pag_view
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        total = len(self.pag_view.pages)
        if self.direction == 'prev':
            self.pag_view.current_idx = (self.pag_view.current_idx - 1) % total
        else:
            self.pag_view.current_idx = (self.pag_view.current_idx + 1) % total
        await self.pag_view.update(interaction)


class JumpButton(discord.ui.Button):
    def __init__(
        self,
        pag_view: 'BuildPaginationView',
        target_idx: int,
        label: str,
        style: discord.ButtonStyle,
        row: int,
    ):
        super().__init__(label=label, style=style, row=row)
        self.pag_view = pag_view
        self.target_idx = target_idx

    async def callback(self, interaction: discord.Interaction):
        self.pag_view.current_idx = self.target_idx
        await self.pag_view.update(interaction)
