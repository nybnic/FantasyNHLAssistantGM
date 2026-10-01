"""PNG charts for Telegram, drawn from engine/report.py views.

Phone-first: 1080 px wide, light surface, one blue (you / a start) and one
orange (them / a benched game), the pair checked for colour-blind
separation with the dataviz validator (2026-10-01). Numbers ride in labels
and legends, so nothing depends on colour alone.
"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")  # no display on the runner
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Patch  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e1"
BLUE = "#2a78d6"
BLUE_LIGHT = "#86b6ef"  # same hue, lighter step: less certain
ORANGE = "#eb6834"
NEUTRAL = "#c9c8c3"
WIDTH_IN, DPI = 7.2, 150

plt.rcParams.update({
    "font.size": 10, "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "axes.edgecolor": GRID, "axes.facecolor": SURFACE, "figure.facecolor": SURFACE,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.titlepad": 10,
})


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def _clean(ax, grid_axis: str | None = None) -> None:
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)
    if grid_axis:
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)


def week_chart(view: dict) -> bytes:
    """Win odds with each add (from now to after), and the projected race."""
    adds = view["adds"]
    n = max(len(adds), 1)
    height = 4.6 + 0.42 * n
    fig = plt.figure(figsize=(WIDTH_IN, height))
    odds_h = (0.42 * n + 0.2) / height
    odds = fig.add_axes((0.36, 1 - 0.75 / height - odds_h, 0.6, odds_h))
    race = fig.add_axes((0.09, 0.45 / height, 0.88, 2.6 / height))
    now = view["win_now"]
    fig.text(0.02, 1 - 0.3 / height, f"Week {view['week']}: win odds with each add (now {now:.0%})",
             fontsize=12, fontweight="bold", va="center")
    _clean(odds, "x")
    for i, a in enumerate(adds):
        y = len(adds) - 1 - i
        colour = BLUE if a["recommended"] else NEUTRAL
        odds.plot([now, a["win"]], [y, y], color=colour, linewidth=2, solid_capstyle="round", zorder=2)
        odds.scatter([a["win"]], [y], s=70, color=colour, edgecolors=SURFACE, linewidths=2, zorder=3)
        tag = "  recommended" if a["recommended"] else ""
        odds.annotate(f"{a['win']:.0%}{tag}", (a["win"], y), xytext=(8, 0), textcoords="offset points",
                      va="center", fontsize=9, color=INK if a["recommended"] else INK_2)
    odds.axvline(now, color=MUTED, linewidth=1, zorder=1)
    odds.set_yticks(range(len(adds)), [f"{a['label']} ({a['games']} gm)" for a in reversed(adds)], fontsize=9)
    odds.set_ylim(-0.6, n - 0.4)
    values = [now] + [a["win"] for a in adds]
    odds.set_xlim(max(0.0, min(values) - 0.05), min(1.0, max(values) + 0.2))
    odds.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    if not adds:
        odds.text(0.5, 0.5, "No free agent changes the odds", transform=odds.transAxes, ha="center", color=INK_2)
        odds.set_yticks([])

    r = view["race"]
    x = range(len(r["labels"]))
    me_final, them_final = r["expected"]
    race.set_title("Projected running total, best lineup every day")
    _clean(race, "y")
    race.plot(x, r["me"], color=BLUE, linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE,
              label=f"You: {me_final:.0f} projected")
    race.plot(x, r["them"], color=ORANGE, linewidth=2, marker="o", markersize=5, markeredgecolor=SURFACE,
              label=f"Them: {them_final:.0f} projected")
    race.set_xticks(list(x), r["labels"])
    race.set_ylim(bottom=0)
    race.legend(loc="upper left", frameon=False)
    return _png(fig)


def schedule_chart(view: dict) -> bytes:
    """Who plays when: a start, a game with no free slot, or no game; then the
    open starting slots by position each day (where a streamer adds points)."""
    days, rows = view["days"], view["rows"]
    n_rows, n_days = len(rows), len(days)
    positions = list(view["open"][0]) if view["open"] else []
    summary = [f"Open {pos}" for pos in positions] + ["Your lineup games", "Their lineup games"]
    first_summary = n_rows + 1
    total = first_summary + len(summary)
    height = 1.9 + 0.3 * total
    fig = plt.figure(figsize=(WIDTH_IN, height))
    ax = fig.add_axes((0.27, 0.75 / height, 0.71, 1 - 1.45 / height))
    ax.set_xlim(-0.5, n_days - 0.5)
    ax.set_ylim(total - 0.5, -1.6)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(length=0)
    ax.set_xticks([])
    ax.set_yticks(list(range(n_rows)) + [first_summary + i for i in range(len(summary))],
                  [f"{r['name']}  {r['positions']}" for r in rows] + summary, fontsize=8.5)
    fig.text(0.02, 1 - 0.3 / height, "Games by day: starts, benched games, open slots",
             fontsize=12, fontweight="bold", va="center")

    for i, d in enumerate(days):
        ax.text(i, -0.75, f"{d['label']}\n{d['day']}", ha="center", va="center", fontsize=8, color=INK_2)
    breaks = [i for i in range(1, n_days) if days[i]["week"] != days[i - 1]["week"]]
    starts = [0] + breaks
    for k, s in enumerate(starts):
        end = (starts + [n_days])[k + 1] - 1
        w = view["weeks"][k] if k < len(view["weeks"]) else {"week": days[s]["week"], "opponent": ""}
        ax.text((s + end) / 2, -1.45, f"Week {w['week']} vs {w['opponent']}", ha="center", va="center",
                fontsize=9, fontweight="bold")
    for b in breaks:
        ax.axvline(b - 0.5, color=MUTED, linewidth=1)
    for y in range(n_rows):
        ax.axhline(y, color=GRID, linewidth=0.6, zorder=0)

    for y, row in enumerate(rows):
        for x, (state, prob) in enumerate(zip(row["cells"], row["probs"])):
            if state is None:
                continue
            colour = BLUE if state == "start" else ORANGE
            ax.add_patch(FancyBboxPatch((x - 0.36, y - 0.32), 0.72, 0.64, boxstyle="round,pad=0,rounding_size=0.12",
                                        facecolor=colour, edgecolor=SURFACE, linewidth=1.5, zorder=2))
            if prob is not None:
                ax.text(x, y, f"{prob * 100:.0f}", ha="center", va="center", fontsize=7, zorder=3,
                        color="white" if state == "start" else INK)

    for x in range(n_days):
        for k, pos in enumerate(positions):
            free = view["open"][x][pos]
            ax.text(x, first_summary + k, str(free) if free else "·", ha="center", va="center", fontsize=8.5,
                    color=INK if free else MUTED, fontweight="bold" if free else "normal")
        ax.text(x, first_summary + len(positions), str(view["my_games"][x]), ha="center", va="center", fontsize=8.5)
        ax.text(x, first_summary + len(positions) + 1, str(view["their_games"][x]), ha="center", va="center",
                fontsize=8.5, color=INK_2)
    ax.axhline(n_rows + 0.4, color=MUTED, linewidth=1)
    ax.axhline(first_summary + len(positions) - 0.5, color=GRID, linewidth=0.8)

    ax.legend(handles=[Patch(color=BLUE, label="Starts"), Patch(color=ORANGE, label="Plays, but no slot free"),
                       Patch(facecolor=SURFACE, edgecolor=GRID, label="No game")],
              loc="upper center", bbox_to_anchor=(0.4, -0.005), ncol=3, frameon=False, fontsize=8.5)
    fig.text(0.98, 0.18 / height, "Goalie cells: % chance to start", ha="right", fontsize=7.5, color=INK_2)
    return _png(fig)


def add_chart(view: dict) -> bytes:
    """One add: its points gain per week, and where it leaves the add budget."""
    fig, (gain, budget) = plt.subplots(2, 1, figsize=(WIDTH_IN, 6.4),
                                       gridspec_kw={"height_ratios": [1, 1], "hspace": 0.6})
    rows = view["weeks"]
    gain.set_title(f"{view['label']}: points gained per week")
    _clean(gain, "y")
    x = range(len(rows))
    bars = gain.bar(x, [r["gain"] for r in rows], width=0.55,
                    color=[BLUE if r["confident"] else BLUE_LIGHT for r in rows], zorder=2)
    gain.axhline(0, color=MUTED, linewidth=1, zorder=1)
    for bar, r in zip(bars, rows):
        h = bar.get_height()
        gain.annotate(f"{h:+.1f}", (bar.get_x() + bar.get_width() / 2, h), xytext=(0, 4 if h >= 0 else -12),
                      textcoords="offset points", ha="center", fontsize=8.5)
    gain.set_xticks(list(x), [f"Wk {r['week']}" + (" (now)" if i == 0 else "") for i, r in enumerate(rows)])
    gain.legend(handles=[Patch(color=BLUE, label="Judged by the add rule"),
                         Patch(color=BLUE_LIGHT, label="Later weeks: less certain")],
                loc="upper right", frameon=False, fontsize=8)
    lo, hi = min(0, *(r["gain"] for r in rows)), max(0, *(r["gain"] for r in rows))
    pad = max(1.0, (hi - lo) * 0.25)
    gain.set_ylim(lo - (pad if lo < 0 else 0), hi + pad * 1.6)

    b = view["budget"]
    weeks = range(1, len(b["pace"]) + 1)
    budget.set_title(f"Adds: {b['used'][-1] if b['used'] else 0} of {b['cap']} used")
    _clean(budget, "y")
    budget.axvspan(b["playoffs_from"] - 0.5, len(b["pace"]) + 0.5, color=GRID, alpha=0.6, zorder=0, linewidth=0)
    budget.text(b["playoffs_from"] + 0.9, b["cap"] * 0.08, f"playoffs:\n{b['reserve']} kept",
                fontsize=8, color=INK_2, ha="center")
    budget.plot(weeks, b["pace"], color=MUTED, linewidth=2, label="Even pace")
    used_x = list(range(1, len(b["used"]) + 1))
    budget.step(used_x, b["used"], where="post", color=BLUE, linewidth=2, marker="o", markersize=5,
                markeredgecolor=SURFACE, label="Used")
    now_week, now_used = b["week"], (b["used"][-1] if b["used"] else 0)
    budget.scatter([now_week], [now_used + 1], s=70, color=ORANGE, edgecolors=SURFACE, linewidths=2, zorder=3,
                   label="After this add")
    budget.set_xlim(0.5, len(b["pace"]) + 0.5)
    budget.set_ylim(0, b["cap"] + 2)
    budget.set_xlabel("Week")
    budget.legend(loc="upper left", frameon=False, fontsize=8.5)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.93, bottom=0.09)
    return _png(fig)
