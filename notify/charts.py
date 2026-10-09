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


def _wrap(text: str, width: int = 62) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width))


def schedule_chart(view: dict) -> bytes:
    """Who plays when: a start, a game with no free slot, or no game; the best
    streamer per position as he'd slot in (outlined); then the open starting
    slots by position each day (where a streamer adds points)."""
    days, rows, streams = view["days"], view["rows"], view.get("streamers", [])
    n_rows, n_days = len(rows), len(days)
    stream_top = n_rows + 1 if streams else n_rows
    positions = list(view["open"][0]) if view["open"] else []
    summary = [f"Open {pos}" for pos in positions] + ["Your lineup games", "Their lineup games"]
    first_summary = stream_top + len(streams) + 1
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
    stream_labels = [f"+ {st['name']}  {st['positions']}" for st in streams]
    ax.set_yticks(list(range(n_rows)) + [stream_top + i for i in range(len(streams))]
                  + [first_summary + i for i in range(len(summary))],
                  [f"{r['name']}  {r['positions']}" for r in rows] + stream_labels + summary, fontsize=8.5)
    fig.text(0.02, 1 - 0.3 / height, "Games by day: starts, benched games, open slots, streamers",
             fontsize=12, fontweight="bold", va="center")

    for i, d in enumerate(days):
        ax.text(i, -0.75, f"{d['label']}\n{d['day']}", ha="center", va="center", fontsize=8, color=INK_2)
    breaks = [i for i in range(1, n_days) if days[i]["week"] != days[i - 1]["week"]]
    starts = [0] + breaks
    for k, s0 in enumerate(starts):
        end = (starts + [n_days])[k + 1] - 1
        w = view["weeks"][k] if k < len(view["weeks"]) else {"week": days[s0]["week"], "opponent": ""}
        ax.text((s0 + end) / 2, -1.45, f"Week {w['week']} vs {w['opponent']}", ha="center", va="center",
                fontsize=9, fontweight="bold")
    for b in breaks:
        ax.axvline(b - 0.5, color=MUTED, linewidth=1)
    for y in list(range(n_rows)) + [stream_top + i for i in range(len(streams))]:
        ax.axhline(y, color=GRID, linewidth=0.6, zorder=0)

    def cell(x, y, colour, filled=True):
        ax.add_patch(FancyBboxPatch((x - 0.36, y - 0.32), 0.72, 0.64, boxstyle="round,pad=0,rounding_size=0.12",
                                    facecolor=colour if filled else SURFACE, edgecolor=colour if not filled else SURFACE,
                                    linewidth=1.5 if filled else 2, zorder=2))

    for y, row in enumerate(rows):
        for x, (state, prob) in enumerate(zip(row["cells"], row["probs"])):
            if state is None:
                continue
            cell(x, y, BLUE if state == "start" else ORANGE)
            if prob is not None:
                ax.text(x, y, f"{prob * 100:.0f}", ha="center", va="center", fontsize=7, zorder=3,
                        color="white" if state == "start" else INK)
    for k, st in enumerate(streams):
        for x, state in enumerate(st["cells"]):
            if state is not None:
                cell(x, stream_top + k, BLUE if state == "start" else ORANGE, filled=False)
    if streams:
        ax.axhline(stream_top - 0.6, color=GRID, linewidth=0.8)

    for x in range(n_days):
        for k, pos in enumerate(positions):
            free = view["open"][x][pos]
            ax.text(x, first_summary + k, str(free) if free else "·", ha="center", va="center", fontsize=8.5,
                    color=INK if free else MUTED, fontweight="bold" if free else "normal")
        ax.text(x, first_summary + len(positions), str(view["my_games"][x]), ha="center", va="center", fontsize=8.5)
        ax.text(x, first_summary + len(positions) + 1, str(view["their_games"][x]), ha="center", va="center",
                fontsize=8.5, color=INK_2)
    ax.axhline(first_summary - 0.6, color=MUTED, linewidth=1)
    ax.axhline(first_summary + len(positions) - 0.5, color=GRID, linewidth=0.8)

    handles = [Patch(color=BLUE, label="Starts"), Patch(color=ORANGE, label="Plays, no slot free")]
    if streams:
        handles.append(Patch(facecolor=SURFACE, edgecolor=BLUE, linewidth=2, label="+ Streamer fills a slot"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.4, -0.005), ncol=3, frameon=False, fontsize=8.5)
    fig.text(0.98, 0.18 / height, "Goalie cells: % chance to start. Empty: no game", ha="right", fontsize=7.5,
             color=INK_2)
    return _png(fig)


def result_chart(view: dict) -> bytes:
    """The week's score race: both teams' running totals by day, and where
    the week's first plan expected them to finish (dashed)."""
    import datetime as dt
    fig, ax = plt.subplots(figsize=(WIDTH_IN, 4.2))
    me, them = view["final"]
    ax.set_title(f"Week {view['week']} vs {view['opponent']}: {me:.0f} - {them:.0f}")
    _clean(ax, "y")
    labels = [dt.date.fromisoformat(d).strftime("%a\n%d") for d in view["days"]]
    x = range(len(view["mine"]))

    def running(points):
        total, out = 0.0, []
        for p in points:
            total += p
            out.append(total)
        return out

    for points, colour, name in ((view["mine"], BLUE, "You"), (view["theirs"], ORANGE, "Them")):
        ys = running(points)
        ax.plot(x, ys, color=colour, linewidth=2.2, marker="o", markersize=4, label=f"{name} {ys[-1]:.0f}" if ys else name)
    top = max([*running(view["mine"]), *running(view["theirs"]), 1.0])
    if view["plan"]:
        expected = view["plan"]["expected"]
        top = max(top, *expected)
        for value, colour, name, va in zip(expected, (BLUE, ORANGE), ("your", "their"),
                                           ("bottom", "top") if expected[0] >= expected[1] else ("top", "bottom")):
            ax.axhline(value, color=colour, linewidth=1, linestyle="--", alpha=0.8)
            ax.text(-0.15, value, f" plan: {name} {value:.0f}", fontsize=8, color=colour, va=va, ha="left")
    ax.set_ylim(0, top * 1.12)
    ax.set_xlim(-0.2, max(len(view["mine"]) - 1, 1) + 0.2)
    ax.set_xticks(list(x), labels[:len(view["mine"])], fontsize=8.5)
    ax.set_ylabel("Points")
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.88, bottom=0.16)
    return _png(fig)
