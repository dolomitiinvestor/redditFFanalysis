import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from ff_lineups import __main__ as cli
from ff_lineups import odds as od
from ff_lineups.optimize import optimize
from ff_lineups.projections import Projection, score

HALF_PPR = {"pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "rush_yd": 0.1, "rush_td": 6,
            "rec": 0.5, "rec_yd": 0.1, "rec_td": 6, "fum_lost": -2, "bonus_rec_te": 0.5}


def test_prop_math():
    assert od.implied(-110) == pytest.approx(0.5238, abs=1e-4)
    assert od.implied(150) == pytest.approx(0.4)
    # Poisson: P(X > 0.5) = 1 - e^-lam = 0.6 -> lam = 0.916
    assert od.poisson_mean(0.5, 0.6) == pytest.approx(-math.log(0.4), abs=1e-4)
    # Even line: mean is median plus skew (exp(sigma^2/2))
    assert od.yards_mean(60.5, 0.5, 0.5) == pytest.approx(60.5 * math.exp(0.125))
    assert od.yards_mean(60.5, 0.6, 0.5) > od.yards_mean(60.5, 0.5, 0.5)
    assert od.norm_name("Kenneth Walker III") == od.norm_name("kenneth walker")
    assert od.norm_name("D.J. Moore") == od.norm_name("DJ Moore")
    assert od.norm_name("Amon-Ra St. Brown") == "amon ra st brown"


def test_score_te_premium():
    assert score({"rec": 4, "rec_yd": 40}, HALF_PPR, "TE") == pytest.approx(2 + 4 + 2)
    assert score({"rec": 4, "rec_yd": 40}, HALF_PPR, "WR") == pytest.approx(2 + 4)
    assert score({"pts_ppr": 8.5, "fgm": 2}, HALF_PPR, "K") == 8.5


def test_slate_windows():
    thu = datetime(2026, 10, 15, 13, tzinfo=timezone.utc)  # Thursday 9am ET
    s, e = od.slate_window("thu", thu)
    assert e == datetime(2026, 10, 18, 0, tzinfo=od.ET)
    sun = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    s, e = od.slate_window("sun", sun)
    assert e == datetime(2026, 10, 20, 6, tzinfo=od.ET)
    assert od.auto_slate(thu) == "thu" and od.auto_slate(sun) == "sun"


def _p(pid, pos, pts, kickoff=None, positions=None):
    return Projection(pid, pid, pos, positions or [pos], "X", "", pts, pts, kickoff=kickoff)


def test_optimizer_flex_and_superflex():
    slots = ["QB", "RB", "WR", "FLEX", "SUPER_FLEX"]
    pool = [_p("qb1", "QB", 20), _p("qb2", "QB", 15), _p("rb1", "RB", 14), _p("rb2", "RB", 13),
            _p("wr1", "WR", 12), _p("wr2", "WR", 6), _p("te1", "TE", 9)]
    best = optimize(slots, pool, lambda p: p.used)
    assert [p.player_id for p in best.players] == ["qb1", "rb1", "wr1", "rb2", "qb2"]
    assert best.total(lambda p: p.used) == 74


def test_optimizer_respects_locks():
    now = datetime(2026, 10, 18, 18, tzinfo=timezone.utc)
    played = now - timedelta(hours=3)
    slots = ["RB", "FLEX"]
    started = _p("rb_thu", "RB", 4, kickoff=played)
    benched_played = _p("wr_thu", "WR", 30, kickoff=played)
    pool = [started, benched_played, _p("rb2", "RB", 10), _p("wr1", "WR", 11)]
    from ff_lineups.optimize import current
    cur = current(slots, ["rb_thu", "rb2"], {p.player_id: p for p in pool}, now)
    best = optimize(slots, pool, lambda p: p.used, cur, now)
    assert [p.player_id for p in best.players] == ["rb_thu", "wr1"]
    assert best.locked == [True, False]


# ---- end to end with fake APIs ------------------------------------------------

def _ev(eid, home, away, kick, books):
    return {"id": eid, "home_team": home, "away_team": away, "commence_time": kick, "bookmakers": books}


BOOK = {"key": "draftkings", "markets": [
    {"key": "player_reception_yds", "outcomes": [
        {"name": "Over", "description": "Amon-Ra St. Brown", "price": -115, "point": 75.5},
        {"name": "Under", "description": "Amon-Ra St. Brown", "price": -105, "point": 75.5}]},
    {"key": "player_receptions", "outcomes": [
        {"name": "Over", "description": "Amon-Ra St. Brown", "price": -130, "point": 6.5},
        {"name": "Under", "description": "Amon-Ra St. Brown", "price": 100, "point": 6.5}]},
    {"key": "player_anytime_td", "outcomes": [
        {"name": "Yes", "description": "Amon-Ra St. Brown", "price": 110},
        {"name": "Yes", "description": "Jahmyr Gibbs", "price": -160}]},
    {"key": "player_rush_yds", "outcomes": [
        {"name": "Over", "description": "Jahmyr Gibbs", "price": -110, "point": 70.5},
        {"name": "Under", "description": "Jahmyr Gibbs", "price": -110, "point": 70.5}]},
]}


class FakeSleeper:
    def __init__(self, *a): pass
    def league(self, lid):
        return {"name": "Test League", "season": "2026", "scoring_settings": HALF_PPR,
                "roster_positions": ["RB", "WR", "FLEX", "BN", "BN", "IR"]}
    def state(self): return {"week": 7}
    def rosters(self, lid):
        return [{"roster_id": 1, "owner_id": "u1", "players": ["arsb", "gibbs", "wr9", "rb9"],
                 "starters": ["rb9", "wr9", "arsb"], "reserve": [], "taxi": []}]
    def users(self, lid): return [{"user_id": "u1", "display_name": "me", "metadata": {"team_name": "Lions Den"}}]
    def players(self):
        return {"arsb": {"full_name": "Amon-Ra St. Brown", "position": "WR", "fantasy_positions": ["WR"], "team": "DET"},
                "gibbs": {"full_name": "Jahmyr Gibbs", "position": "RB", "fantasy_positions": ["RB"], "team": "DET"},
                "wr9": {"full_name": "Depth Guy", "position": "WR", "fantasy_positions": ["WR"], "team": "NYJ"},
                "rb9": {"full_name": "Hurt Back", "position": "RB", "fantasy_positions": ["RB"], "team": "NYJ",
                        "injury_status": "Out"}}
    def projections(self, season, week):
        return {"arsb": {"rec": 6.5, "rec_yd": 75, "rec_td": 0.5}, "gibbs": {"rush_yd": 70, "rec": 3, "rec_yd": 25, "rush_td": 0.6},
                "wr9": {"rec": 3, "rec_yd": 35}, "rb9": {"rush_yd": 60}}


class FakeOdds:
    remaining = 500
    regions = "us"
    calls = 0
    def __init__(self, *a, **k): pass
    def events(self):
        return [_ev("e1", "Detroit Lions", "Green Bay Packers", "2026-10-18T17:00:00Z", None),
                _ev("e0", "Miami Dolphins", "Buffalo Bills", "2026-10-15T00:15:00Z", None)]
    def event_props(self, eid):
        FakeOdds.calls += 1
        self.remaining -= 6
        return _ev(eid, "Detroit Lions", "Green Bay Packers", "2026-10-18T17:00:00Z", [BOOK])


def test_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "SleeperClient", FakeSleeper)
    monkeypatch.setattr(cli.od, "OddsClient", FakeOdds)
    monkeypatch.setenv("ODDS_API_KEY", "k")
    args = ["--league", "1", "--slate", "sun", "--cache-dir", str(tmp_path), "--out-dir", str(tmp_path)]

    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, "datetime", Sunday)

    assert cli.main(args) == 0
    md = capsys.readouterr().out
    assert FakeOdds.calls == 1  # only the Sunday game in the window; TNF already played
    assert "| RB | Jahmyr Gibbs (RB, DET) |" in md
    assert "**start (benched now)**" in md and "Hurt Back" in md
    data = json.loads(next(tmp_path.glob("*_lineups.json")).read_text())
    arsb = data["projections"]["arsb"]
    assert arsb["odds"] is not None and arsb["books"] == 1 and arsb["sleeper"] == pytest.approx(13.75)
    # Rerun spends nothing: game is cached
    assert cli.main(args) == 0 and FakeOdds.calls == 1
