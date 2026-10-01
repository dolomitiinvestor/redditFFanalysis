"""Render the analysis JSON as a Markdown report."""

from __future__ import annotations

from datetime import datetime


def _player_line(p: dict, tag_key: str) -> str:
    meta = ", ".join(x for x in (p.get("position"), p.get("team")) if x)
    sources = "; ".join(p.get("source_threads", []))
    line = f"- **{p['player']}** ({meta}) — _{p[tag_key]}_: {p['why']}"
    if sources:
        line += f"\n  - Threads: {sources}"
    return line


def to_markdown(report: dict, subreddit: str, hours: float, n_posts: int, n_comments: int, generated: datetime) -> str:
    out = [
        f"# r/{subreddit} Dynasty Digest — {generated:%Y-%m-%d}",
        f"_Last {hours:g}h · {n_posts} threads · {n_comments} comments · generated {generated:%Y-%m-%d %H:%M %Z}_",
        "",
        "## Bottom line",
        report["headline_summary"],
        "",
        "## Action items",
        *[f"- {a}" for a in report["action_items"]],
        "",
        "## Breakout candidates this week",
        *[_player_line(p, "confidence") for p in report["breakout_candidates"]],
        "",
        "## Value risers 📈",
        *[_player_line(p, "magnitude") for p in report["value_risers"]],
        "",
        "## Value fallers 📉",
        *[_player_line(p, "magnitude") for p in report["value_fallers"]],
        "",
        "## Key insights",
        *[f"- **{i['insight']}** — {i['evidence']}" for i in report["key_insights"]],
        "",
        "## Contrarian takes",
        *[f"- {t}" for t in report["contrarian_takes"]],
        "",
    ]
    return "\n".join(out)
