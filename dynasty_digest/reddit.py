"""Fetch the last N hours of posts and comments from a subreddit via the Reddit API."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import requests

OAUTH_BASE = "https://oauth.reddit.com"
PUBLIC_BASE = "https://www.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

SKIP_AUTHORS = {"AutoModerator", "[deleted]"}
SKIP_BODIES = {"[deleted]", "[removed]", ""}


@dataclass
class Comment:
    author: str
    score: int
    body: str
    depth: int


@dataclass
class Post:
    id: str
    title: str
    author: str
    score: int
    num_comments: int
    created_utc: float
    flair: str
    url: str
    selftext: str
    is_self: bool
    comments: list[Comment] = field(default_factory=list)


class RedditClient:
    """Uses OAuth (app-only) when REDDIT_CLIENT_ID/SECRET are set, else public JSON endpoints."""

    def __init__(self, user_agent: str | None = None, min_interval: float | None = None):
        self.user_agent = user_agent or os.environ.get(
            "REDDIT_USER_AGENT", "dynasty-digest/0.1 (by u/your_username)"
        )
        self.client_id = os.environ.get("REDDIT_CLIENT_ID")
        self.client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        self.session = requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        self.oauth = bool(self.client_id and self.client_secret)
        # OAuth allows ~100 req/min; unauthenticated is ~10 req/min.
        self.min_interval = min_interval if min_interval is not None else (0.7 if self.oauth else 6.5)
        self._last_request = 0.0
        self._token_expiry = 0.0
        if self.oauth:
            self._refresh_token()

    @property
    def base(self) -> str:
        return OAUTH_BASE if self.oauth else PUBLIC_BASE

    def _refresh_token(self) -> None:
        resp = requests.post(
            TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self.session.headers["Authorization"] = f"bearer {data['access_token']}"
        self._token_expiry = time.time() + data.get("expires_in", 3600) - 60

    def get(self, path: str, params: dict | None = None) -> dict | list:
        if self.oauth and time.time() > self._token_expiry:
            self._refresh_token()
        if not self.oauth and not path.endswith(".json"):
            path += ".json"
        params = {"raw_json": 1, **(params or {})}
        for attempt in range(5):
            wait = self.min_interval - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()
            resp = self.session.get(f"{self.base}{path}", params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(float(resp.headers.get("Retry-After", 2 ** (attempt + 1))))
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        raise RuntimeError(f"Failed to fetch {path}")

    # ---- posts ---------------------------------------------------------

    def recent_posts(self, subreddit: str, hours: float) -> list[Post]:
        """All posts in /new created within the last `hours` hours."""
        cutoff = time.time() - hours * 3600
        posts: list[Post] = []
        after = None
        while True:
            params = {"limit": 100}
            if after:
                params["after"] = after
            listing = self.get(f"/r/{subreddit}/new", params)["data"]
            children = listing["children"]
            for child in children:
                d = child["data"]
                if d["created_utc"] < cutoff:
                    if d.get("stickied"):
                        continue  # pinned posts sit at the top regardless of age
                    return posts
                posts.append(
                    Post(
                        id=d["id"],
                        title=d["title"],
                        author=d.get("author") or "[deleted]",
                        score=d.get("score", 0),
                        num_comments=d.get("num_comments", 0),
                        created_utc=d["created_utc"],
                        flair=d.get("link_flair_text") or "",
                        url=f"https://www.reddit.com{d['permalink']}",
                        selftext=d.get("selftext") or "",
                        is_self=d.get("is_self", False),
                    )
                )
            after = listing.get("after")
            if not after or not children:
                return posts

    # ---- comments ------------------------------------------------------

    def comments(self, post: Post, expand_more: bool = True, max_more_calls: int = 10) -> list[Comment]:
        data = self.get(
            f"/r/{_subreddit_from_url(post.url)}/comments/{post.id}",
            {"limit": 500, "depth": 10, "sort": "top"},
        )
        out: list[Comment] = []
        more_ids: list[str] = []
        _walk(data[1]["data"]["children"], out, more_ids)

        # "load more comments" stubs: only expandable through the OAuth API.
        calls = 0
        while expand_more and self.oauth and more_ids and calls < max_more_calls:
            batch, more_ids = more_ids[:100], more_ids[100:]
            resp = self.get(
                "/api/morechildren",
                {"link_id": f"t3_{post.id}", "children": ",".join(batch), "api_type": "json", "sort": "top"},
            )
            things = resp.get("json", {}).get("data", {}).get("things", [])
            _walk(things, out, more_ids, flat=True)
            calls += 1
        return out


def _subreddit_from_url(url: str) -> str:
    # https://www.reddit.com/r/DynastyFF/comments/<id>/...
    parts = url.split("/r/", 1)[1]
    return parts.split("/", 1)[0]


def _walk(children: list, out: list[Comment], more_ids: list[str], flat: bool = False) -> None:
    for child in children:
        kind, d = child.get("kind"), child.get("data", {})
        if kind == "more":
            more_ids.extend(d.get("children", []))
            continue
        if kind != "t1":
            continue
        author = d.get("author") or "[deleted]"
        body = (d.get("body") or "").strip()
        if author not in SKIP_AUTHORS and body not in SKIP_BODIES:
            out.append(Comment(author=author, score=d.get("score", 0), body=body, depth=d.get("depth", 0)))
        if not flat:
            replies = d.get("replies")
            if isinstance(replies, dict):
                _walk(replies["data"]["children"], out, more_ids)


def fetch_subreddit_day(
    subreddit: str = "DynastyFF",
    hours: float = 24,
    client: RedditClient | None = None,
    log=print,
) -> list[Post]:
    client = client or RedditClient()
    log(f"Auth mode: {'OAuth' if client.oauth else 'public JSON (slow; set REDDIT_CLIENT_ID/SECRET)'}")
    posts = client.recent_posts(subreddit, hours)
    log(f"Found {len(posts)} posts in the last {hours:g}h")
    for i, post in enumerate(posts, 1):
        if post.num_comments:
            post.comments = client.comments(post)
        log(f"  [{i}/{len(posts)}] {len(post.comments):>4} comments  {post.title[:70]}")
    return posts
