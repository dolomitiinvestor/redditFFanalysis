"""CLI: python -m ff_lineups --league <sleeper league id> [--slate auto|thu|sun] [--no-odds]"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import odds as od
from .optimize import NON_STARTING, current, optimize
from .projections import build
from .report import TeamResult, sleeper_val, to_markdown, used
from .sleeper import SleeperClient


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Optimal lineups for every team in a Sleeper league.")
    ap.add_argument("--league", default=os.environ.get("SLEEPER_LEAGUE_ID"), help="Sleeper league ID")
    ap.add_argument("--week", type=int, help="NFL week (default: Sleeper's current week)")
    ap.add_argument("--slate", choices=["auto", "thu", "sun"], default="auto",
                    help="Which games to pull props for: thu = Thu-Sat games, sun = Sun+Mon. auto picks by weekday.")
    ap.add_argument("--no-odds", action="store_true", help="Spend no odds credits; use cached props only.")
    ap.add_argument("--refresh", action="store_true", help="Re-pull props for games already cached (costs credits).")
    ap.add_argument("--cache-dir", default="data")
    ap.add_argument("--out-dir", default="reports")
    args = ap.parse_args(argv)
    if not args.league:
        ap.error("pass --league or set SLEEPER_LEAGUE_ID")

    log = lambda msg: print(msg, file=sys.stderr)
    now = datetime.now(timezone.utc)
    cache_dir, out_dir = Path(args.cache_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sl = SleeperClient(cache_dir)
    league = sl.league(args.league)
    season = league["season"]
    week = args.week or int(sl.state()["week"])
    rosters, users, players = sl.rosters(args.league), sl.users(args.league), sl.players()
    sleeper_proj = sl.projections(season, week)
    log(f"{league['name']}: {len(rosters)} teams, week {week}, {len(sleeper_proj)} Sleeper projections.")

    slate = od.auto_slate(now) if args.slate == "auto" else args.slate
    cache_path = od.week_cache_path(cache_dir, season, week)
    events, odds_cache, credits = [], {}, None
    if os.environ.get("ODDS_API_KEY"):
        client = od.OddsClient()
        if args.no_odds:
            events = client.events()  # free
            odds_cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        else:
            events, odds_cache = od.fetch_week(client, cache_path, slate, now, args.refresh, log)
        credits = client.remaining
    else:
        log("ODDS_API_KEY not set: Sleeper projections only.")

    names = {u["user_id"]: (u.get("metadata") or {}).get("team_name") or u.get("display_name") for u in users}
    slots = [s for s in league["roster_positions"] if s not in NON_STARTING]
    all_ids = {pid for r in rosters for pid in (r.get("players") or [])}
    projs = build(all_ids, players, sleeper_proj, odds_cache, league["scoring_settings"], od.kickoffs(events), log)

    teams, owner = [], {}
    for r in rosters:
        name = names.get(r.get("owner_id")) or f"Team {r['roster_id']}"
        inactive = set(r.get("reserve") or []) | set(r.get("taxi") or [])
        pool = [projs[pid] for pid in (r.get("players") or []) if pid not in inactive]
        for p in pool:
            owner[p.player_id] = name
        cur = current(slots, r.get("starters") or [], projs, now)
        teams.append(TeamResult(name, cur, optimize(slots, pool, used, cur, now),
                                optimize(slots, pool, sleeper_val, cur, now)))

    meta = {"slate": slate, "games_cached": len(odds_cache), "credits_left": credits}
    md = to_markdown(league["name"], season, week, teams, projs, owner, meta, now.astimezone())
    stamp = f"{now.astimezone():%Y-%m-%d_%H%M}"
    (out_dir / f"{stamp}_lineups.json").write_text(json.dumps({
        "meta": {**meta, "season": season, "week": week},
        "projections": {pid: {**dataclasses.asdict(p), "kickoff": p.kickoff and p.kickoff.isoformat(),
                              "used": p.used, "fantasy_team": owner.get(pid)} for pid, p in projs.items()},
        "lineups": {t.name: {"optimal": [[s, p and p.name] for s, p in zip(t.best.slots, t.best.players)],
                             "gain": t.gain} for t in teams},
    }, indent=1))
    md_path = out_dir / f"{stamp}_lineups.md"
    md_path.write_text(md)
    print(md)
    log(f"Saved report -> {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
