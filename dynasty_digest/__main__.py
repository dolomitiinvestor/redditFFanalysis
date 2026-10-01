"""CLI: python -m dynasty_digest [--subreddit DynastyFF] [--hours 24]"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

from .analyze import analyze
from .reddit import Comment, Post, fetch_subreddit_day
from .report import to_markdown


def _load_posts(path: Path) -> list[Post]:
    raw = json.loads(path.read_text())
    return [Post(**{**p, "comments": [Comment(**c) for c in p["comments"]]}) for p in raw]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Summarize a day of r/DynastyFF with Claude.")
    ap.add_argument("--subreddit", default="DynastyFF")
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--out-dir", default="reports")
    ap.add_argument("--fetch-only", action="store_true", help="Save raw Reddit data and skip the LLM step.")
    ap.add_argument("--from-raw", type=Path, help="Analyze a previously saved raw JSON file instead of fetching.")
    args = ap.parse_args(argv)

    log = lambda msg: print(msg, file=sys.stderr)
    now = datetime.now().astimezone()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{now:%Y-%m-%d_%H%M}"

    if args.from_raw:
        posts = _load_posts(args.from_raw)
    else:
        posts = fetch_subreddit_day(args.subreddit, args.hours, log=log)
        raw_path = out_dir / f"{stamp}_raw.json"
        raw_path.write_text(json.dumps([dataclasses.asdict(p) for p in posts], indent=1))
        log(f"Saved raw data -> {raw_path}")
    if args.fetch_only:
        return 0
    if not posts:
        log("No posts found in the window.")
        return 1

    report = analyze(posts, args.subreddit, args.hours, log=log)
    n_comments = sum(len(p.comments) for p in posts)
    md = to_markdown(report, args.subreddit, args.hours, len(posts), n_comments, now)
    (out_dir / f"{stamp}_report.json").write_text(json.dumps(report, indent=2))
    md_path = out_dir / f"{stamp}_report.md"
    md_path.write_text(md)
    print(md)
    log(f"Saved report -> {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
