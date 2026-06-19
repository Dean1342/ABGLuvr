import matplotlib
matplotlib.use('Agg')  # Must be before pyplot import — no display on Heroku
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
from datetime import datetime

_BG = '#2b2d31'
_FG = '#dcddde'
_GRID = '#3f4248'
_PALETTE = ['#5865f2', '#57f287', '#fee75c', '#ed4245', '#eb459e', '#ffa8b4', '#9b59b6', '#3498db']


def _close(fig) -> bytes:
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', facecolor=_BG, dpi=110)
    buf.seek(0)
    data = buf.read()
    plt.close(fig)
    return data


def _dark_ax(fig, ax):
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_FG, labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID)
    ax.title.set_color(_FG)
    ax.xaxis.label.set_color(_FG)
    ax.yaxis.label.set_color(_FG)


def generate_donut_chart(installed: int, planned: int, ordered: int = 0) -> bytes | None:
    data = [(v, l) for v, l in [(installed, 'Installed'), (planned, 'Planned'), (ordered, 'Ordered')] if v > 0]
    if not data:
        return None
    sizes, labels = zip(*data)
    colors = _PALETTE[:len(sizes)]

    fig, ax = plt.subplots(figsize=(5, 4.5), facecolor=_BG)
    fig.patch.set_facecolor(_BG)
    wedges, _ = ax.pie(sizes, colors=colors, startangle=90, wedgeprops=dict(width=0.52))
    total = sum(sizes)
    all_installed = (planned == 0 and ordered == 0 and installed > 0)
    center = f'{total}\nmods\nfully\ninstalled' if all_installed else f'{total}\nmods'
    ax.text(0, 0, center, ha='center', va='center',
            fontsize=10 if all_installed else 13, fontweight='bold', color=_FG)
    ax.legend(
        wedges, [f'{l}  ({s})' for l, s in zip(labels, sizes)],
        loc='lower center', bbox_to_anchor=(0.5, -0.08), ncol=len(sizes),
        frameon=False, labelcolor=_FG, fontsize=9
    )
    ax.set_title('Mod Status', color=_FG, pad=10, fontsize=11)
    return _close(fig)


def generate_budget_chart(total_paid: float, total_cost: float) -> bytes | None:
    if total_cost <= 0:
        return None

    pct = total_paid / total_cost
    overspent = pct > 1.0
    fill_color = '#ed4245' if overspent else _PALETTE[0]

    fig, ax = plt.subplots(figsize=(7, 2.2), facecolor=_BG)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    # Grey track = full projected cost
    ax.barh([0], [total_cost], height=0.52, color=_GRID, zorder=1)
    # Colored fill = amount paid (capped at track width visually)
    fill_w = min(total_paid, total_cost)
    ax.barh([0], [fill_w], height=0.52, color=fill_color, zorder=2)

    # Percentage label centred inside the fill
    if fill_w > total_cost * 0.12:  # only show inside if there's room
        ax.text(fill_w / 2, 0, f'{pct * 100:.1f}%',
                ha='center', va='center', fontsize=13, fontweight='bold',
                color='white', zorder=3)
    else:
        ax.text(fill_w + total_cost * 0.02, 0, f'{pct * 100:.1f}%',
                ha='left', va='center', fontsize=11, fontweight='bold',
                color=fill_color, zorder=3)

    # Dollar labels below the bar
    ax.text(0, -0.44, f'${total_paid:,.0f} spent', ha='left', va='top',
            color=fill_color, fontsize=9, fontweight='bold')
    ax.text(total_cost, -0.44, f'of ${total_cost:,.0f} projected', ha='right', va='top',
            color=_FG, fontsize=9)

    ax.set_xlim(0, total_cost * 1.02)
    ax.set_ylim(-0.85, 0.65)
    ax.axis('off')
    ax.set_title('Budget Progress', color=_FG, fontsize=11, pad=8)

    fig.tight_layout(pad=0.5)
    return _close(fig)


def generate_category_chart(category_totals: dict) -> bytes | None:
    cats = {k: v for k, v in category_totals.items() if v > 0}
    if not cats:
        return None
    labels = list(cats.keys())
    values = list(cats.values())
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(labels))]

    fig, ax = plt.subplots(figsize=(5.5, 4.5), facecolor=_BG)
    fig.patch.set_facecolor(_BG)
    wedges, _, autotexts = ax.pie(
        values, labels=None, colors=colors, autopct='%1.0f%%',
        startangle=90, pctdistance=0.75,
        wedgeprops=dict(linewidth=0.5, edgecolor=_BG)
    )
    for at in autotexts:
        at.set_color(_FG)
        at.set_fontsize(8)
    ax.legend(
        wedges, [f'{l}  (${v:,.0f})' for l, v in zip(labels, values)],
        loc='lower center', bbox_to_anchor=(0.5, -0.12),
        ncol=min(len(labels), 3), frameon=False, labelcolor=_FG, fontsize=8
    )
    ax.set_title('Spend by Category', color=_FG, pad=10, fontsize=11)
    return _close(fig)


def generate_timeline_chart(mods: list[dict]) -> bytes | None:
    dated = sorted(
        [m for m in mods if m.get('install_date')],
        key=lambda m: str(m['install_date'])
    )
    if len(dated) < 2:
        return None

    dates = []
    for m in dated:
        raw = m['install_date']
        try:
            dates.append(datetime.strptime(str(raw), '%Y-%m-%d'))
        except ValueError:
            continue
    if len(dates) < 2:
        return None

    names = [m['name'][:22] + ('…' if len(m['name']) > 22 else '') for m in dated[:len(dates)]]
    height = max(3.2, len(dates) * 0.42 + 1.2)

    fig, ax = plt.subplots(figsize=(8, height), facecolor=_BG)
    _dark_ax(fig, ax)
    y_pos = list(range(len(dates)))
    ax.scatter(dates, y_pos, color=_PALETTE[0], zorder=3, s=55)
    ax.hlines(y_pos, min(dates), max(dates), colors=_GRID, linewidth=0.6, zorder=1)
    for i, (d, name) in enumerate(zip(dates, names)):
        ax.annotate(name, (d, i), textcoords='offset points', xytext=(7, 0),
                    color=_FG, fontsize=8, va='center')
    ax.set_yticks([])
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30, ha='right')
    ax.set_title('Installation Timeline', color=_FG, fontsize=11)
    ax.spines['left'].set_visible(False)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    fig.tight_layout()
    return _close(fig)
