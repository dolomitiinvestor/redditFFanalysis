"""Optimal start/sit: exact max-points assignment of players to Sleeper lineup slots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .projections import Projection

SLOT_ELIGIBLE = {
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "WRRB_FLEX": {"WR", "RB"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}
NON_STARTING = {"BN", "IR", "TAXI"}
BIG = 1e6


def eligible(slot: str, p: Projection) -> bool:
    return bool(SLOT_ELIGIBLE.get(slot, {slot}) & set(p.positions))


@dataclass
class Lineup:
    slots: list[str]
    players: list[Projection | None]  # aligned with slots
    locked: list[bool]

    def total(self, value: Callable[[Projection], float]) -> float:
        return round(sum(value(p) for p in self.players if p), 2)


def current(slots: list[str], starters: list[str], proj: dict[str, Projection], now: datetime) -> Lineup:
    players = [proj.get(pid) for pid in starters] + [None] * (len(slots) - len(starters))
    return Lineup(slots, players[: len(slots)], [bool(p and p.locked(now)) for p in players[: len(slots)]])


def optimize(slots: list[str], pool: list[Projection], value: Callable[[Projection], float],
             fixed: Lineup | None = None, now: datetime | None = None) -> Lineup:
    """Best lineup by `value`. Starters whose games have kicked off stay put; benched locked players can't move in."""
    players: list[Projection | None] = [None] * len(slots)
    locked = [False] * len(slots)
    if fixed and now:
        for i, p in enumerate(fixed.players):
            if p and p.locked(now):
                players[i], locked[i] = p, True
    taken = {p.player_id for p in players if p}
    free = [i for i in range(len(slots)) if not locked[i]]
    cands = [p for p in pool if p.player_id not in taken and not (now and p.locked(now))]
    if free:
        # Add one empty dummy per slot so every slot can be filled and cols >= rows.
        cost = [[-value(p) if eligible(slots[i], p) else BIG for p in cands] + [0.0] * len(free) for i in free]
        for row, col in zip(free, _hungarian(cost)):
            if col < len(cands) and eligible(slots[row], cands[col]):
                players[row] = cands[col]
    return Lineup(slots, players, locked)


def _hungarian(cost: list[list[float]]) -> list[int]:
    """Min-cost assignment of every row to a distinct column (rows <= cols). Returns column per row."""
    n, m = len(cost), len(cost[0])
    inf = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0], j0 = p[j1], j1
    ans = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            ans[p[j] - 1] = j - 1
    return ans
