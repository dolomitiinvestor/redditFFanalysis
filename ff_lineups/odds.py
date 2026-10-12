"""The Odds API (free tier): NFL player props -> expected stat lines.

Credit math (free tier = 500/month): one event-odds call costs markets x regions credits.
6 markets x 1 region x ~16 games = ~96/week, ~480 in a 5-week month. The /events list is free.
Each game is fetched once per week; results are cached in data/odds/ so re-runs cost nothing.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
ET = ZoneInfo("America/New_York")

# market -> (Sleeper stat key, distribution). anytime_td is split into rush_td / rec_td.
MARKETS = {
    "player_pass_yds": ("pass_yd", "yards"),
    "player_pass_tds": ("pass_td", "count"),
    "player_rush_yds": ("rush_yd", "yards"),
    "player_receptions": ("rec", "count"),
    "player_reception_yds": ("rec_yd", "yards"),
    "player_anytime_td": (None, "anytime"),
}
# Log-normal spread of yardage around the line. Lines are medians; yardage is right-skewed,
# so mean = median * exp(sigma^2 / 2): ~+3% passing, ~+11% rushing, ~+13% receiving.
LOG_SIGMA = {"pass_yd": 0.25, "rush_yd": 0.45, "rec_yd": 0.5}
YES_ONLY_VIG = 1.08  # books often post only the "Yes" side of anytime TD; strip ~8% hold

TEAMS = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


# ---- slate windows -------------------------------------------------------

def slate_window(slate: str, now: datetime) -> tuple[datetime, datetime]:
    """'thu' = games before Sunday (Thu/Fri/Sat). 'sun' = Sunday through Monday night."""
    local = now.astimezone(ET)
    days_to_sun = (6 - local.weekday()) % 7
    sunday = (local + timedelta(days=days_to_sun)).replace(hour=0, minute=0, second=0, microsecond=0)
    if slate == "thu":
        return now, sunday
    return now, sunday + timedelta(days=2, hours=6)  # through Tuesday 06:00 ET


def auto_slate(now: datetime) -> str:
    return "thu" if now.astimezone(ET).weekday() in (3, 4, 5) else "sun"


# ---- API client ----------------------------------------------------------

class OddsClient:
    def __init__(self, api_key: str | None = None, regions: str | None = None):
        self.api_key = api_key or os.environ["ODDS_API_KEY"]
        self.regions = regions or os.environ.get("ODDS_REGIONS", "us")
        self.remaining: int | None = None

    def _get(self, path: str, **params):
        resp = requests.get(f"{BASE}{path}", params={"apiKey": self.api_key, **params}, timeout=30)
        resp.raise_for_status()
        if "x-requests-remaining" in resp.headers:
            self.remaining = int(float(resp.headers["x-requests-remaining"]))
        return resp.json()

    def events(self) -> list[dict]:
        return self._get("/events")  # free

    def event_props(self, event_id: str) -> dict:
        return self._get(f"/events/{event_id}/odds", regions=self.regions,
                         markets=",".join(MARKETS), oddsFormat="american")


def fetch_week(client: OddsClient, cache_path: Path, slate: str, now: datetime,
               refresh: bool = False, log=print) -> tuple[list[dict], dict[str, dict]]:
    """Fetch props for un-cached games in the slate window. Returns (events, cache of event_id -> odds)."""
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    events = client.events()
    start, end = slate_window(slate, now)
    todo = [e for e in events if start <= _ts(e["commence_time"]) < end and (refresh or e["id"] not in cache)]
    todo.sort(key=lambda e: e["commence_time"])
    cost = len(MARKETS) * len(client.regions.split(","))
    if client.remaining is not None and client.remaining < cost * len(todo):
        keep = client.remaining // cost
        log(f"Only {client.remaining} credits left: fetching {keep} of {len(todo)} games.")
        todo = todo[:keep]
    for e in todo:
        cache[e["id"]] = {**client.event_props(e["id"]), "fetched_at": now.isoformat()}
        log(f"  props: {e['away_team']} @ {e['home_team']} (credits left: {client.remaining})")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache))
    log(f"Odds: {len(todo)} games fetched for '{slate}' slate, {len(cache)} cached this week.")
    return events, cache


def kickoffs(events: list[dict]) -> dict[str, datetime]:
    """Sleeper team abbr -> kickoff time."""
    out = {}
    for e in events:
        for name in (e["home_team"], e["away_team"]):
            if name in TEAMS:
                out[TEAMS[name]] = _ts(e["commence_time"])
    return out


# ---- props -> stats ------------------------------------------------------

def implied(price: float) -> float:
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def poisson_mean(line: float, p_over: float) -> float:
    """lambda such that P(X > line) = p_over for X ~ Poisson(lambda)."""
    k = math.floor(line)  # over hits when X >= k + 1

    def p_gt(lam):
        return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))

    lo, hi = 1e-6, max(1.0, line * 4 + 4)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if p_gt(mid) < p_over else (lo, mid)
    return (lo + hi) / 2


def yards_mean(line: float, p_over: float, sigma: float) -> float:
    z = statistics.NormalDist().inv_cdf(min(max(p_over, 0.02), 0.98))
    return line * math.exp(sigma * z + sigma ** 2 / 2)


def _book_estimates(book: dict) -> dict[tuple[str, str], float]:
    """(player, market) -> expected value from one bookmaker."""
    out = {}
    for m in book.get("markets", []):
        key = m["key"]
        if key not in MARKETS:
            continue
        stat, kind = MARKETS[key]
        sides: dict[str, dict] = {}
        for o in m.get("outcomes", []):
            player = o.get("description")
            if player:
                sides.setdefault(player, {}).setdefault(o["name"], o)
        for player, s in sides.items():
            if kind == "anytime":
                if "Yes" not in s:
                    continue
                yes = implied(s["Yes"]["price"])
                p = yes / (yes + implied(s["No"]["price"])) if "No" in s else yes / YES_ONLY_VIG
                out[(player, key)] = -math.log(1 - min(p, 0.95))  # expected TDs
                continue
            over = s.get("Over")
            if not over or over.get("point") is None:
                continue
            line = over["point"]
            under = s.get("Under")
            if under and under.get("point") == line:
                po = implied(over["price"])
                p_over = po / (po + implied(under["price"]))
            else:
                p_over = 0.5
            out[(player, key)] = (poisson_mean(line, p_over) if kind == "count"
                                  else yards_mean(line, p_over, LOG_SIGMA[stat]))
    return out


def props_to_stats(event: dict) -> dict[str, dict]:
    """Odds event -> {player name: {stat: expected value, ..., '_teams': [abbr, abbr]}} (median across books)."""
    per: dict[tuple[str, str], list[float]] = {}
    for book in event.get("bookmakers", []):
        for k, v in _book_estimates(book).items():
            per.setdefault(k, []).append(v)
    teams = [TEAMS.get(event.get("home_team"), ""), TEAMS.get(event.get("away_team"), "")]
    players: dict[str, dict] = {}
    for (player, market), vals in per.items():
        players.setdefault(player, {"_teams": teams, "_books": 0})
        players[player][market] = statistics.median(vals)
        players[player]["_books"] = max(players[player]["_books"], len(vals))
    return players


def to_stat_line(markets: dict, position: str, fallback: dict) -> dict:
    """Map market means to Sleeper stat keys. Anytime TDs split rush/rec by yardage share."""
    stats = {}
    for key, (stat, _) in MARKETS.items():
        if stat and key in markets:
            stats[stat] = markets[key]
    if "player_anytime_td" in markets:
        tds = markets["player_anytime_td"]
        if position == "QB":
            stats["rush_td"], stats["rec_td"] = tds, 0.0
        else:
            rush = stats.get("rush_yd", fallback.get("rush_yd", 0)) or 0
            rec = stats.get("rec_yd", fallback.get("rec_yd", 0)) or 0
            share = rush / (rush + rec) if rush + rec > 0 else (0.7 if position == "RB" else 0.0)
            stats["rush_td"], stats["rec_td"] = tds * share, tds * (1 - share)
    return stats


# ---- name matching -------------------------------------------------------

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def norm_name(name: str) -> str:
    n = re.sub(r"[^a-z ]", "", name.lower().replace("-", " "))
    return " ".join(_SUFFIX.sub("", n).split())


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def week_cache_path(cache_dir: Path, season, week) -> Path:
    return cache_dir / "odds" / f"{season}_wk{int(week):02d}.json"

