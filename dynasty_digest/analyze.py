"""Summarize a day of subreddit discussion with Claude."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import anthropic

from .reddit import Post

MODEL = "claude-opus-5-5"

# Rough chars-per-token for English Reddit text. Used only to decide chunking.
CHARS_PER_TOKEN = 3.5
# Corpora under this go to the model in one call; bigger ones are map-reduced.
SINGLE_PASS_TOKEN_LIMIT = 400_000
CHUNK_TOKEN_TARGET = 150_000

SYSTEM_PROMPT = """You are a sharp dynasty fantasy football analyst. You read a full day of \
r/DynastyFF discussion (self posts and comments) and turn it into an actionable briefing for a \
dynasty manager deciding trades, waiver adds, and start/sit.

How to read the data:
- Each thread shows its title, flair, upvote score and comment count; each comment shows its score.
  Weigh community consensus by upvotes, but call out sharp contrarian takes when they have real reasoning.
- Separate signal (injuries, depth-chart changes, snap/target share, coaching quotes, contract news,
  trade-market prices, rookie usage) from noise (memes, pure "who do I start" polls with no reasoning).
- Only name players actually discussed in the data. Do not invent stats, injuries, or news that is
  not in the posts. If the community cites a number, you may repeat it and attribute it to the thread.
- "Value" means dynasty trade value (long-term asset price), not just this week's points."""

VALUE_LIST = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "player": {"type": "string"},
            "position": {"type": "string"},
            "team": {"type": "string"},
            "why": {"type": "string"},
            "magnitude": {"type": "string", "enum": ["slight", "moderate", "major"]},
            "source_threads": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["player", "position", "team", "why", "magnitude", "source_threads"],
        "additionalProperties": False,
    },
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline_summary": {
            "type": "string",
            "description": "3-5 sentence top-line read on what moved the dynasty market today.",
        },
        "key_insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "insight": {"type": "string"},
                    "evidence": {"type": "string", "description": "What in the threads supports this."},
                },
                "required": ["insight", "evidence"],
                "additionalProperties": False,
            },
        },
        "breakout_candidates": {
            "type": "array",
            "description": "Players who could break out THIS WEEK.",
            "items": {
                "type": "object",
                "properties": {
                    "player": {"type": "string"},
                    "position": {"type": "string"},
                    "team": {"type": "string"},
                    "why": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "source_threads": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["player", "position", "team", "why", "confidence", "source_threads"],
                "additionalProperties": False,
            },
        },
        "value_risers": VALUE_LIST,
        "value_fallers": VALUE_LIST,
        "contrarian_takes": {
            "type": "array",
            "description": "Well-argued takes that go against the consensus.",
            "items": {"type": "string"},
        },
        "action_items": {
            "type": "array",
            "description": "Concrete buy/sell/add/hold moves a manager could make today.",
            "items": {"type": "string"},
        },
    },
    "required": [
        "headline_summary",
        "key_insights",
        "breakout_candidates",
        "value_risers",
        "value_fallers",
        "contrarian_takes",
        "action_items",
    ],
    "additionalProperties": False,
}

FINAL_INSTRUCTIONS = """Produce the daily briefing as JSON matching the schema.
- breakout_candidates: players with a concrete reason to pop THIS week (role change, injury ahead of them,
  plus matchup, rising usage). Aim for 5-10, best first.
- value_risers / value_fallers: players whose dynasty trade value the community is moving, aim for 5-12 each,
  biggest movers first.
- source_threads: thread titles that support the call.
- Be specific: cite snap %, target share, depth chart spots, trade prices when the threads mention them."""

MAP_INSTRUCTIONS = """This is one slice of the day's threads. Extract dense analyst notes for a later merge step:
- Every player discussed with a meaningful take: name, position/team if known, the claim, the reasoning,
  rough consensus (comment scores), and the thread title.
- Breakout signals for this week, value up/down signals, trade prices quoted, injury/depth-chart news.
Plain bullet notes, no preamble. Skip memes and empty start/sit polls."""


def format_post(post: Post) -> str:
    ts = datetime.fromtimestamp(post.created_utc, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"=== THREAD: {post.title}",
        f"flair: {post.flair or '-'} | score: {post.score} | comments: {post.num_comments} | {ts}",
        f"url: {post.url}",
    ]
    if post.is_self and post.selftext.strip():
        lines += ["POST:", post.selftext.strip()]
    elif not post.is_self:
        lines.append("(link post, no body)")
    if post.comments:
        lines.append("COMMENTS:")
        for c in post.comments:
            indent = "  " * min(c.depth, 4)
            body = c.body.replace("\n", " ")
            lines.append(f"{indent}- [{c.score:+d}] {body}")
    return "\n".join(lines)


def build_chunks(posts: list[Post], token_target: int) -> list[str]:
    """Group whole threads into chunks under ~token_target tokens (a giant thread gets its own chunk)."""
    char_target = int(token_target * CHARS_PER_TOKEN)
    chunks, current, size = [], [], 0
    for text in (format_post(p) for p in posts):
        if current and size + len(text) > char_target:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _stream_text(client: anthropic.Anthropic, user_content: str, effort: str, schema: dict | None) -> str:
    output_config: dict = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        thinking={"type": "adaptive"},
        output_config=output_config,
        # On a safety-classifier decline, re-run on a fallback model chosen by the API.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": user_content}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError(f"Model declined the request: {message.stop_details}")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Response hit max_tokens before finishing.")
    return "".join(b.text for b in message.content if b.type == "text")


def analyze(posts: list[Post], subreddit: str, hours: float, client: anthropic.Anthropic | None = None, log=print) -> dict:
    client = client or anthropic.Anthropic()
    corpus_chars = sum(len(format_post(p)) for p in posts)
    est_tokens = int(corpus_chars / CHARS_PER_TOKEN)
    header = f"Subreddit: r/{subreddit} | window: last {hours:g}h | threads: {len(posts)} | " \
             f"comments: {sum(len(p.comments) for p in posts)}"
    log(f"Corpus ~{est_tokens:,} tokens")

    if est_tokens <= SINGLE_PASS_TOKEN_LIMIT:
        body = "\n\n".join(format_post(p) for p in posts)
        prompt = f"{header}\n\n<threads>\n{body}\n</threads>\n\n{FINAL_INSTRUCTIONS}"
    else:
        chunks = build_chunks(posts, CHUNK_TOKEN_TARGET)
        log(f"Large corpus: map-reduce over {len(chunks)} chunks")
        notes = []
        for i, chunk in enumerate(chunks, 1):
            log(f"  summarizing chunk {i}/{len(chunks)}")
            notes.append(_stream_text(client, f"{header}\n\n<threads>\n{chunk}\n</threads>\n\n{MAP_INSTRUCTIONS}", "medium", None))
        joined = "\n\n".join(f"<notes slice=\"{i}\">\n{n}\n</notes>" for i, n in enumerate(notes, 1))
        prompt = (
            f"{header}\n\nThe day's threads were split into slices and summarized into analyst notes. "
            f"Merge them; when multiple slices mention a player, that is a stronger signal.\n\n{joined}\n\n{FINAL_INSTRUCTIONS}"
        )

    log("Running final analysis...")
    return json.loads(_stream_text(client, prompt, "high", REPORT_SCHEMA))
