"""Score Sleeper and odds-implied stat lines with the league's own scoring settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .odds import norm_name, props_to_stats, to_stat_line

SKILL = {"QB", "RB", "WR", "TE"}
OUT_STATUSES = {"Out", "IR", "PUP", "Sus", "NA", "COV"}
POS_REC_BONUS = {"TE": "bonus_rec_te", "RB": "bonus_rec_rb", "WR": "bonus_rec_wr"}


@dataclass
class Projection:
    player_id: str
    name: str
    position: str
    positions: list[str]
    team: str
    injury: str
    sleeper: float | None      # Sleeper projection, league scoring
    odds: float | None         # odds-implied projection, league scoring (None = no props)
    books: int = 0
    kickoff: datetime | None = None

    @property
    def used(self) -> float:
        """Odds when props exist, else Sleeper. Out/IR players are zeroed."""
        if self.injury in OUT_STATUSES:
            return 0.0
        val = self.odds if self.odds is not None else self.sleeper
        return val or 0.0

    @property
    def source(self) -> str:
        return "odds" if self.odds is not None else ("sleeper" if self.sleeper is not None else "-")

    def locked(self, now: datetime) -> bool:
        return self.kickoff is not None and self.kickoff <= now


def score(stats: dict, scoring: dict, position: str) -> float:
    if position in ("K", "DEF") and "pts_ppr" in stats:
        return float(stats["pts_ppr"])  # K/DEF buckets aren't in projected stats; Sleeper's own total
    pts = sum(scoring[k] * v for k, v in stats.items() if k in scoring and not k.startswith("pts_"))
    bonus = POS_REC_BONUS.get(position)
    if bonus and bonus in scoring and bonus not in stats:
        pts += scoring[bonus] * stats.get("rec", 0)
    return round(pts, 2)


def build(player_ids: set[str], players: dict, sleeper_proj: dict, odds_cache: dict,
          scoring: dict, kickoffs: dict, log=print) -> dict[str, Projection]:
    by_name: dict[str, list[str]] = {}
    for pid in player_ids:
        p = players.get(pid, {})
        if p.get("position") in SKILL:
            by_name.setdefault(norm_name(_name(p, pid)), []).append(pid)

    odds_lines: dict[str, dict] = {}
    unmatched = 0
    for event in odds_cache.values():
        for name, markets in props_to_stats(event).items():
            cands = by_name.get(norm_name(name), [])
            on_team = [c for c in cands if players[c].get("team") in markets["_teams"]]
            pick = on_team or (cands if len(cands) == 1 else [])
            if pick:
                odds_lines[pick[0]] = markets
            else:
                unmatched += 1  # mostly players not rostered in this league
    if odds_cache:
        log(f"Odds props matched to {len(odds_lines)} rostered players ({unmatched} unrostered/unmatched).")

    out = {}
    for pid in player_ids:
        p = players.get(pid, {})
        pos = p.get("position") or ("DEF" if pid.isalpha() else "")
        sl_stats = sleeper_proj.get(pid)
        sleeper = score(sl_stats, scoring, pos) if sl_stats is not None else None
        odds = books = None
        if pid in odds_lines:
            markets = odds_lines[pid]
            # Props fill what they cover; Sleeper fills the rest (INTs, fumbles, 2-pt, QB rush if no prop).
            stats = {**(sl_stats or {}), **to_stat_line(markets, pos, sl_stats or {})}
            stats = {k: v for k, v in stats.items() if not k.startswith("pts_")}
            odds, books = score(stats, scoring, pos), markets["_books"]
        team = p.get("team") or (pid if pos == "DEF" else "")
        out[pid] = Projection(
            player_id=pid, name=_name(p, pid), position=pos,
            positions=p.get("fantasy_positions") or [pos], team=team or "FA",
            injury=p.get("injury_status") or "", sleeper=sleeper, odds=odds, books=books or 0,
            kickoff=kickoffs.get(team),
        )
    return out


def _name(p: dict, pid: str) -> str:
    if p.get("full_name"):
        return p["full_name"]
    if p.get("first_name"):
        return f"{p['first_name']} {p.get('last_name', '')}".strip()
    return pid
