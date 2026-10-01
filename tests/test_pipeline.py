import json
import time
from types import SimpleNamespace

from dynasty_digest import analyze as an
from dynasty_digest.reddit import RedditClient
from dynasty_digest.report import to_markdown


def _post(pid, age_h, **kw):
    return {"kind": "t3", "data": {
        "id": pid, "title": f"Thread {pid}", "author": "u", "score": 10, "num_comments": 2,
        "created_utc": time.time() - age_h * 3600, "link_flair_text": "Discussion",
        "permalink": f"/r/DynastyFF/comments/{pid}/x/", "selftext": "Body text", "is_self": True, **kw}}


def _comment(body, replies=None, author="a", depth=0):
    return {"kind": "t1", "data": {"author": author, "score": 5, "body": body, "depth": depth,
            "replies": {"data": {"children": replies}} if replies else ""}}


class FakeReddit(RedditClient):
    def __init__(self):
        self.oauth = False

    def get(self, path, params=None):
        if path.endswith("/new"):
            return {"data": {"after": None, "children": [
                _post("pin", 100, stickied=True), _post("a", 1), _post("b", 23), _post("old", 30)]}}
        return [None, {"data": {"children": [
            _comment("Buy Player X", [_comment("Agreed", depth=1), _comment("[removed]", depth=1)]),
            _comment("bot", author="AutoModerator"),
            {"kind": "more", "data": {"children": ["zz"]}},
        ]}}]


def test_fetch_window_and_comment_filtering():
    c = FakeReddit()
    posts = c.recent_posts("DynastyFF", 24)
    assert [p.id for p in posts] == ["a", "b"]  # sticky skipped, old cut off
    comments = c.comments(posts[0])
    assert [x.body for x in comments] == ["Buy Player X", "Agreed"]


SAMPLE = {
    "headline_summary": "S", "key_insights": [{"insight": "I", "evidence": "E"}],
    "breakout_candidates": [{"player": "P", "position": "WR", "team": "DET", "why": "W",
                             "confidence": "high", "source_threads": ["T"]}],
    "value_risers": [], "value_fallers": [], "contrarian_takes": [], "action_items": ["Buy P"],
}


class FakeStream:
    def __init__(self, calls, kw):
        calls.append(kw)
        fmt = kw["output_config"].get("format")
        text = json.dumps(SAMPLE) if fmt else "notes"
        self.msg = SimpleNamespace(stop_reason="end_turn", stop_details=None,
                                   content=[SimpleNamespace(type="text", text=text)])
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def get_final_message(self): return self.msg


def _fake_client(calls):
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: FakeStream(calls, kw))))


def test_analyze_single_and_map_reduce(monkeypatch):
    c = FakeReddit()
    posts = c.recent_posts("DynastyFF", 24)
    for p in posts:
        p.comments = c.comments(p)
    calls = []
    out = an.analyze(posts, "DynastyFF", 24, client=_fake_client(calls), log=lambda *_: None)
    assert out == SAMPLE and len(calls) == 1
    assert calls[0]["model"] == "claude-opus-5-5"

    monkeypatch.setattr(an, "SINGLE_PASS_TOKEN_LIMIT", 10)
    monkeypatch.setattr(an, "CHUNK_TOKEN_TARGET", 10)
    calls = []
    an.analyze(posts, "DynastyFF", 24, client=_fake_client(calls), log=lambda *_: None)
    assert len(calls) == 3  # 2 chunks + final merge

    from datetime import datetime
    md = to_markdown(out, "DynastyFF", 24, 2, 4, datetime.now().astimezone())
    assert "**P** (WR, DET)" in md and "Buy P" in md
