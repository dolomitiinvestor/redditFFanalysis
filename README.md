# Dynasty Digest

Pulls the last 24h of r/DynastyFF (all posts, self-post text, and every text comment) from the Reddit API and has Claude write a briefing:

- **Bottom line** + **action items** (buy / sell / add / hold)
- **Breakout candidates this week** — with reason, confidence, and source threads
- **Value risers / fallers** — dynasty trade value moves, with size of move
- **Key insights** and **contrarian takes**

## Setup

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

# Recommended: Reddit OAuth app (https://www.reddit.com/prefs/apps -> "script" type)
export REDDIT_CLIENT_ID=...
export REDDIT_CLIENT_SECRET=...
export REDDIT_USER_AGENT="dynasty-digest/0.1 (by u/yourname)"
```

Without Reddit credentials it falls back to public `.json` endpoints (~10 req/min, and can't expand "load more comments"). With OAuth it runs at ~100 req/min and expands hidden comments.

## Run

```bash
python -m dynasty_digest                    # last 24h of r/DynastyFF
python -m dynasty_digest --hours 48
python -m dynasty_digest --fetch-only       # just save raw Reddit data
python -m dynasty_digest --from-raw reports/2026-10-01_0800_raw.json   # re-run the LLM on saved data
```

Output goes to `reports/`: `*_raw.json` (Reddit data), `*_report.json` (structured), `*_report.md` (readable briefing, also printed to stdout).

## How it works

1. `reddit.py` — pages `/r/DynastyFF/new` until posts are older than the window (skips stickies), then pulls each thread's full comment tree (drops AutoModerator, deleted, removed).
2. `analyze.py` — formats every thread with upvote scores so the model can weigh consensus. Up to ~400K tokens goes to Claude in one call; bigger days are split into ~150K-token slices, summarized into notes, then merged. Final output is schema-enforced JSON (`claude-opus-5-5`, adaptive thinking, effort `high`, server-side refusal fallback enabled).
3. `report.py` — renders Markdown.

Run it daily via cron, e.g. `0 8 * * * cd /path && python -m dynasty_digest`.

## Tests

```bash
pytest -q
```

---

# Lineup Optimizer (`ff_lineups`)

Projects every rostered player in a Sleeper league two ways, then sets the best lineup for **every team**:

- **Odds**: sportsbook player props (The Odds API, free tier), converted to expected stats and scored with your league's settings.
- **Sleeper**: Sleeper's own weekly projections (free, no key).

Both numbers are shown side by side. The optimizer uses odds where props exist and falls back to Sleeper (K, DEF, low-usage players). It also flags where a Sleeper-only lineup would differ.

## Setup

```bash
export SLEEPER_LEAGUE_ID=123456789012345678   # from your league URL
export ODDS_API_KEY=...                       # free key: https://the-odds-api.com
python -m ff_lineups                          # --slate auto|thu|sun, --no-odds, --week N
```

Output: `reports/*_lineups.md` (also printed) and `*_lineups.json`.

## Credit budget (free tier = 500/month)

- Markets: pass yds, pass TDs, rush yds, receptions, rec yds, anytime TD = 6 credits per game.
- Each game is fetched **once per week** and cached in `data/odds/`: Thursday run → Thu–Sat games, Sunday run → Sun + Mon games.
- ~16 games × 6 = ~96/week, ~480 in a 5-week month. If credits run short it fetches the earliest games it can afford and logs it.
- `--no-odds` re-runs any time for 0 credits (fresh Sleeper projections + injuries, cached props).

## How props become points

- **Yardage** lines are medians. De-vig the over/under, shift the line by the implied probability, then convert to a mean with a log-normal skew (+3% pass, +11% rush, +13% receiving).
- **Counts** (receptions, pass TDs): Poisson mean that matches the de-vigged over probability.
- **Anytime TD**: de-vigged P(TD) → expected TDs = −ln(1−p). Split rush/rec by yardage share; for QBs it counts as rushing TDs.
- Median across books. Stats with no prop (INTs, fumbles, 2-pt, QB rush yards if missing) come from Sleeper.
- Players already kicked off are locked in place; Out/IR/PUP/Suspended players project 0.

## Schedule

`.github/workflows/lineups.yml` runs Thursday 14:00 UTC and Sunday 12:00 UTC. Add repo secrets `SLEEPER_LEAGUE_ID` and `ODDS_API_KEY`. The report shows in the run's summary page and as an artifact. Props cache carries between runs via `actions/cache`.

Local cron equivalent (ET machine):

```
0 9 * * 4  cd /path && python -m ff_lineups --slate thu
0 8 * * 0  cd /path && python -m ff_lineups --slate sun
```
