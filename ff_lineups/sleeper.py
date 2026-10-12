"""Sleeper API: league settings, rosters, player directory and weekly projections (free, no key)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

BASE = "https://api.sleeper.app/v1"
PROJ_URL = "https://api.sleeper.app/projections/nfl/{season}/{week}"  # undocumented but long-lived
PROJ_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
PLAYERS_TTL = 24 * 3600  # Sleeper asks that /players/nfl (~5MB) be pulled at most once a day


class SleeperClient:
    def __init__(self, cache_dir: Path = Path("data")):
        self.session = requests.Session()
        self.cache_dir = cache_dir

    def _get(self, url: str, params: dict | list | None = None):
        for attempt in range(4):
            resp = self.session.get(url, params=params, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()

    def state(self) -> dict:
        return self._get(f"{BASE}/state/nfl")

    def league(self, league_id: str) -> dict:
        return self._get(f"{BASE}/league/{league_id}")

    def rosters(self, league_id: str) -> list[dict]:
        return self._get(f"{BASE}/league/{league_id}/rosters")

    def users(self, league_id: str) -> list[dict]:
        return self._get(f"{BASE}/league/{league_id}/users")

    def players(self) -> dict[str, dict]:
        path = self.cache_dir / "sleeper_players.json"
        if path.exists() and time.time() - path.stat().st_mtime < PLAYERS_TTL:
            return json.loads(path.read_text())
        data = self._get(f"{BASE}/players/nfl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def projections(self, season: str | int, week: int) -> dict[str, dict]:
        """player_id -> projected stat line (Sleeper stat keys, e.g. pass_yd, rec, rush_td)."""
        params = [("season_type", "regular")] + [("position[]", p) for p in PROJ_POSITIONS]
        rows = self._get(PROJ_URL.format(season=season, week=week), params)
        return {r["player_id"]: r.get("stats") or {} for r in rows if r.get("player_id")}
