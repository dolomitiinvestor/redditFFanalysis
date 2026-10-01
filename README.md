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
