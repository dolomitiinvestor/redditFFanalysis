"""Render lineup recommendations as Markdown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .optimize import Lineup
from .projections import OUT_STATUSES, Projection

used = lambda p: p.used
sleeper_val = lambda p: 0.0 if p.injury in OUT_STATUSES else (p.sleeper or 0.0)


@dataclass
class TeamResult:
    name: str
    current: Lineup
    best: Lineup          # optimized on odds (Sleeper where no props)
    best_sleeper: Lineup  # optimized on Sleeper only

    @property
    def gain(self) -> float:
        return round(self.best.total(used) - self.current.total(used), 2)


def _f(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}"


def _ids(lineup: Lineup) -> set[str]:
    return {p.player_id for p in lineup.players if p}


def _names(ps) -> str:
    return ", ".join(f"{p.name} ({p.used:.1f})" for p in sorted(ps, key=lambda p: -p.used)) or "—"


def team_section(t: TeamResult) -> list[str]:
    cur_ids, best_ids = _ids(t.current), _ids(t.best)
    out = [
        f"### {t.name} — optimal {t.best.total(used):.1f} vs current {t.current.total(used):.1f} "
        f"({'+' if t.gain >= 0 else ''}{t.gain:.1f})",
        "",
        "| Slot | Player | Odds | Sleeper | Note |",
        "|---|---|---:|---:|---|",
    ]
    for slot, p, lock in zip(t.best.slots, t.best.players, t.best.locked):
        if not p:
            out.append(f"| {slot} | _empty_ | | | |")
            continue
        notes = [n for n in (
            "🔒 locked" if lock else "",
            "**start (benched now)**" if p.player_id not in cur_ids else "",
            p.injury,
            "no props" if p.odds is None and p.position not in ("K", "DEF") else "",
        ) if n]
        out.append(f"| {slot} | {p.name} ({p.position}, {p.team}) | {_f(p.odds)} | {_f(p.sleeper)} | {', '.join(notes)} |")
    ins = [p for p in t.best.players if p and p.player_id not in cur_ids]
    outs = [p for p in t.current.players if p and p.player_id not in best_ids]
    if ins or outs:
        out += ["", f"- **Start:** {_names(ins)}", f"- **Bench:** {_names(outs)}"]
    else:
        out += ["", "- Current lineup is already optimal."]
    sl_ids = _ids(t.best_sleeper)
    if sl_ids != best_ids:
        s_in = [p for p in t.best_sleeper.players if p and p.player_id not in best_ids]
        s_out = [p for p in t.best.players if p and p.player_id not in sl_ids]
        out.append(f"- _Sleeper-only would differ:_ start {', '.join(p.name for p in s_in)} "
                   f"over {', '.join(p.name for p in s_out)}")
    return out + [""]


def disagreements(teams: list[TeamResult], projs: dict[str, Projection], owner: dict[str, str], n: int = 15) -> list[str]:
    rows = [p for p in projs.values() if p.odds is not None and p.sleeper is not None and max(p.odds, p.sleeper) >= 5]
    rows.sort(key=lambda p: -abs(p.odds - p.sleeper))
    out = ["| Player | Fantasy team | Odds | Sleeper | Odds − Sleeper |", "|---|---|---:|---:|---:|"]
    for p in rows[:n]:
        out.append(f"| {p.name} ({p.position}, {p.team}) | {owner.get(p.player_id, '')} | {p.odds:.1f} | "
                   f"{p.sleeper:.1f} | {p.odds - p.sleeper:+.1f} |")
    return out


def to_markdown(league: str, season, week: int, teams: list[TeamResult], projs: dict[str, Projection],
                owner: dict[str, str], meta: dict, generated: datetime) -> str:
    leaks = sorted([t for t in teams if t.gain > 0.05], key=lambda t: -t.gain)
    with_odds = sum(1 for p in projs.values() if p.odds is not None)
    out = [
        f"# Lineup Optimizer — {league}, {season} Week {week}",
        f"_Generated {generated:%Y-%m-%d %H:%M %Z} · {meta['games_cached']} games with props "
        f"({meta['slate']} slate run) · {with_odds}/{len(projs)} rostered players have props · "
        f"odds credits left: {meta.get('credits_left', '?')}_",
        "",
        "## Bottom line",
        f"- **{len(leaks)} of {len(teams)} teams** are not starting their best lineup."
        + (" Biggest gains: " + ", ".join(f"{t.name} +{t.gain:.1f}" for t in leaks[:5]) + "." if leaks else ""),
        "- Projections: odds-implied where props exist (sportsbook consensus, scored with league settings); "
        "Sleeper's projection fills K, DEF and players without props.",
        "",
        "## Where the books and Sleeper disagree most",
        *disagreements(teams, projs, owner),
        "",
        "## Teams",
        "",
    ]
    for t in sorted(teams, key=lambda t: t.name.lower()):
        out += team_section(t)
    return "\n".join(out)
