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


def decision_chart(view: dict) -> bytes:
    """Each add in win-points: this week's (x) and later (y). Above the
    diagonal x + y = the add's price, a move is worth an add. Dots are
    numbered (labels would collide in clusters); a key lists them."""
    pts = sorted(view["points"], key=lambda pt: (not pt["recommended"], -pt["x"]))
    bar = view["bar"]
    key_h = 0.22 * len(pts) + 0.3
    height = 6.2 + key_h
    fig = plt.figure(figsize=(WIDTH_IN, height))
    ax = fig.add_axes((0.13, (key_h + 0.75) / height, 0.83, 4.3 / height))
    fig.text(0.02, 1 - 0.25 / height, _wrap(view["headline"], 52), fontsize=12, fontweight="bold", va="top")
    if view["detail"]:
        fig.text(0.02, 1 - 0.75 / height, _wrap(view["detail"], 70), fontsize=9.5, color=INK_2, va="top")
    _clean(ax)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(GRID)
    xs = [pt["x"] for pt in pts] + [0] + ([bar] if bar is not None else [])
    ys = [pt["y"] for pt in pts] + [0] + ([bar] if bar is not None else [])
    x_pad = max(1.0, (max(xs) - min(xs)) * 0.12)
    y_pad = max(1.0, (max(ys) - min(ys)) * 0.12)
    ax.set_xlim(min(xs) - x_pad, max(xs) + x_pad)
    ax.set_ylim(min(ys) - y_pad, max(ys) + y_pad)
    ax.axvline(0, color=GRID, linewidth=1, zorder=0)
    ax.axhline(0, color=GRID, linewidth=1, zorder=0)
    if bar is not None:
        (x0, x1) = ax.get_xlim()
        ax.plot([x0, x1], [bar - x0, bar - x1], color=MUTED, linewidth=1, zorder=1)
        ax.set_xlim(x0, x1)
        ax.text(x1, bar - x1, f"an add costs {bar:.1f} win-pts ", fontsize=7.5, color=MUTED, ha="right", va="bottom")
    corner = {"fontsize": 8.5, "color": MUTED, "zorder": 1}
    ax.text(0.99, 0.98, "Helps now and later", transform=ax.transAxes, ha="right", va="top", **corner)
    ax.text(0.99, 0.02, "This week only (streamer)", transform=ax.transAxes, ha="right", va="bottom", **corner)
    ax.text(0.01, 0.98, "Later only (keeper)", transform=ax.transAxes, ha="left", va="top", **corner)
    ax.text(0.01, 0.06, "Neither", transform=ax.transAxes, ha="left", va="bottom", **corner)
    spots: dict[tuple[float, float], list[int]] = {}  # adds within ~3% of the axes share one label: "7,8"
    near_x = 0.03 * (ax.get_xlim()[1] - ax.get_xlim()[0])
    near_y = 0.03 * (ax.get_ylim()[1] - ax.get_ylim()[0])
    for i, pt in enumerate(pts, 1):
        colour = BLUE if pt["recommended"] else MUTED
        ax.scatter([pt["x"]], [pt["y"]], s=80 if pt["recommended"] else 55, color=colour, edgecolors=SURFACE,
                   linewidths=2, zorder=3)
        spot = next((xy for xy in spots if abs(xy[0] - pt["x"]) < near_x and abs(xy[1] - pt["y"]) < near_y),
                    (pt["x"], pt["y"]))
        spots.setdefault(spot, []).append(i)
    for (x, y), numbers in spots.items():
        lead = pts[numbers[0] - 1]
        ax.annotate(",".join(map(str, numbers)), (x, y), xytext=(6, 5), textcoords="offset points", fontsize=8.5,
                    fontweight="bold", color=INK if lead["recommended"] else INK_2, zorder=4)
    ax.set_xlabel("This week: change in win odds (win-pts)")
    ax.set_ylabel("Later: worth in win-pts")
    for i, pt in enumerate(pts, 1):
        y = (key_h - 0.15 - 0.22 * i) / height
        fig.text(0.04, y, str(i), fontsize=8.5, fontweight="bold", color=INK if pt["recommended"] else INK_2)
        fig.text(0.08, y, f"{pt['label']} ({pt['games']} gm): {pt['x']:+.1f} now, {pt['y']:+.1f} later "
                 f"({pt['later_pts']:+.0f} pts)"
                 + ("  - recommended" if pt["recommended"] else ""), fontsize=8.5,
                 color=INK if pt["recommended"] else INK_2)
    return _png(fig)


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
