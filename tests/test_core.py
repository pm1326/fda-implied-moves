r"""
Automated tests for the core logic. They use made-up prices and option chains, so they run
offline, at any time of day, and give the same answer every time.

Run with:  .\.venv\Scripts\python.exe -m pytest -q
"""
import argparse
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build_history as BH  # noqa: E402
import implied_move as IM  # noqa: E402
import model as M  # noqa: E402

TODAY = date.today()


# ---------- A fake share, so tests never touch the internet ----------

def business_days(start, end):
    return pd.bdate_range(start, end)


class FakeTicker:
    """Stands in for yfinance.Ticker: a price history and a simple option chain."""

    def __init__(self, daily_vol=0.03, spot=10.0, straddle_price=2.0, spread=0.02, cap=1e9,
                 expiries=None, jump_after=None, jump=0.0, seed=1):
        rng = np.random.default_rng(seed)
        idx = business_days(TODAY - timedelta(days=200), TODAY + timedelta(days=30))
        rets = rng.normal(0, daily_vol, len(idx))
        if jump_after is not None:
            # no movement on the decision day itself, then the whole jump on the next trading day
            rets[[i for i, d in enumerate(idx.date) if d == jump_after]] = 0.0
            first_after = next(i for i, d in enumerate(idx.date) if d > jump_after)
            rets[first_after] = jump
        close = spot * np.cumprod(1 + rets)
        # make the latest real close equal spot, so straddle maths is easy to check
        today_pos = max(i for i, d in enumerate(idx.date) if d <= TODAY)
        close = close / close[today_pos] * spot
        self.df = pd.DataFrame({"Close": close}, index=idx)
        self.spot, self.straddle_price, self.spread, self.cap = spot, straddle_price, spread, cap
        self.options = expiries or [(TODAY + timedelta(days=14)).isoformat()]
        self.news = []

    @property
    def fast_info(self):
        return {"marketCap": self.cap, "shares": self.cap / self.spot}

    def history(self, period=None, start=None, end=None, **_):
        if period:
            return self.df[self.df.index.date <= TODAY].iloc[-120:]
        return self.df[(self.df.index.date >= start) & (self.df.index.date < end)]

    def option_chain(self, expiry):
        half = self.straddle_price / 2
        strikes = [self.spot - 1, self.spot, self.spot + 1]
        def side():
            mids = [half if k == self.spot else half * 0.8 for k in strikes]
            return pd.DataFrame({"strike": strikes,
                                 "bid": [m * (1 - self.spread / 2) for m in mids],
                                 "ask": [m * (1 + self.spread / 2) for m in mids],
                                 "lastPrice": mids})
        return SimpleNamespace(calls=side(), puts=side())


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Swap the internet for a FakeTicker and send trades to a temporary file."""
    holder = {}
    monkeypatch.setattr(IM.yf, "Ticker", lambda _t: holder["t"])
    monkeypatch.setattr(IM, "TRADES", str(tmp_path / "trades.csv"))
    def use(**kw):
        holder["t"] = FakeTicker(**kw)
        return holder["t"]
    return use


def args(**kw):
    base = dict(ticker="TEST", event_date=(TODAY + timedelta(days=7)).isoformat(), p_approve=0.8,
                dependence="low", second_attempt=False, risk="unknown", drug="", note="", force=False)
    base.update(kw)
    return argparse.Namespace(**base)


# ---------- Option quotes ----------

def test_quote_uses_midpoint_and_measures_spread():
    q = IM.quote({"bid": 1.0, "ask": 1.2, "lastPrice": 5.0})
    assert q["mid"] == pytest.approx(1.1)
    assert q["spread"] == pytest.approx(0.2 / 1.1)


def test_quote_without_live_prices_falls_back_and_is_flagged():
    q = IM.quote({"bid": 0, "ask": 0, "lastPrice": 0.7})
    assert q["mid"] == 0.7 and q["spread"] == 1.0


def test_straddle_picks_strike_nearest_the_share_price():
    t = FakeTicker(spot=10.0, straddle_price=2.0)
    s = IM.straddle(t, 10.3, t.options[0])
    assert s["strike"] == 10.0
    assert s["mid"] == pytest.approx(2.0)
    assert s["bid"] < s["mid"] < s["ask"]


# ---------- The model ----------

def toy_history():
    rows = [("approved", "small", "high", False, "", 4.0)] * 10 + [("approved", "small", "low", False, "", 2.0)] * 30 \
         + [("approved", "large", "low", False, "", 1.0)] * 60 + [("rejected", "small", "high", "", "clinical", 10.0)] * 2 \
         + [("rejected", "small", "low", "", "manufacturing", 2.0)] * 20
    return pd.DataFrame(rows, columns=["outcome", "size", "dependence", "second_attempt", "reason", "multiple"])


def test_shrinkage_pulls_small_groups_towards_the_wider_average():
    table = M.fit(toy_history())
    est, n, group = M.predict(table, "approved", {"size": "small", "dependence": "high"})
    small = (10 * 4.0 + 30 * 2.0) / 40          # all small approvals
    overall = (10 * 4 + 30 * 2 + 60 * 1) / 100   # all approvals
    step1 = (40 * small + M.K * overall) / (40 + M.K)
    expected = (10 * 4.0 + M.K * step1) / (10 + M.K)
    assert est == pytest.approx(expected)
    assert n == 10 and "high" in group
    assert step1 < est < 4.0                     # pulled from 4.0 towards the wider group, not all the way


def test_same_cases_are_not_counted_twice():
    # rejected/small/high and rejected/small/high/clinical contain the same 2 decisions.
    # The deeper group adds no new information, so it must not pull the estimate further.
    table = M.fit(toy_history())
    with_reason, n, _ = M.predict(table, "rejected", {"size": "small", "dependence": "high", "reason": "clinical"})
    without, _, _ = M.predict(table, "rejected", {"size": "small", "dependence": "high"})
    assert n == 2
    assert with_reason == pytest.approx(without)


def test_unknown_feature_stops_the_ladder():
    table = M.fit(toy_history())
    est, _, group = M.predict(table, "approved", {"size": "small", "dependence": "unknown"})
    assert group == "approved / small"
    assert est < 4.0


# ---------- Building the history ----------

def test_ticker_lookup():
    assert BH.ticker_for("CAPRICOR, INC.") == "CAPR"
    assert BH.ticker_for("Capricor Therapeutics") == "CAPR"
    assert BH.ticker_for("Some Private Pharma Ltd") is None


def test_rejection_reason_reads_letter_headings():
    clinical = "Dear Sir\nCLINICAL\nThe trial did not show...\nPRODUCT QUALITY\nAlso..."
    mfg = "Dear Sir\nFACILITY INSPECTIONS\nDuring a recent inspection..."
    assert BH.rejection_reason(clinical) == "clinical"
    assert BH.rejection_reason(mfg) == "manufacturing"
    assert BH.rejection_reason("no headings at all") == "other"


def test_measure_captures_the_jump_across_the_decision_day():
    idx = business_days("2024-01-01", "2024-06-28")
    close = pd.Series(10.0, index=idx)
    event = pd.Timestamp("2024-05-15")
    close[close.index > event] = 7.0               # 30% fall after the decision
    close.iloc[::2] *= 1.01                        # a little normal wobble
    m = BH.measure(pd.DataFrame({"Close": close}), shares=1e8, d=event)
    assert m["move_pct"] == pytest.approx(-30, abs=1.5)
    assert m["multiple"] > 10
    assert m["size"] == "small"


def test_measure_rejects_gaps_in_the_price_data():
    idx = business_days("2024-01-01", "2024-03-29")
    df = pd.DataFrame({"Close": 10.0 + np.arange(len(idx)) * 0.01}, index=idx)
    assert BH.measure(df, 1e8, pd.Timestamp("2024-06-01")) is None   # no prices after the decision


# ---------- The signal ----------

def expected_for(fake):
    fake(straddle_price=1.0)
    return IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")["expected_move_pct"]


@pytest.mark.parametrize("multiple", [0.3, 0.6, 1.0, 1.3, 1.8, 3.0, 8.0])
def test_decision_always_follows_the_voting_rule(fake, multiple):
    exp = expected_for(fake)
    fake(straddle_price=10.0 * exp / 100 * multiple)
    a = IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")
    votes = a["_d"]["votes"]
    e, c = list(votes.values()).count("expensive"), list(votes.values()).count("cheap")
    if a["_d"]["out_of_range"]:
        assert a["decision"] == "NO TRADE"
    elif e >= 2 and c == 0:
        assert a["decision"] == "SELL"
    elif c >= 2 and e == 0:
        assert a["decision"] == "BUY"
    else:
        assert a["decision"] == "NO TRADE"


def test_cheap_options_buy_and_dear_options_sell(fake):
    exp = expected_for(fake)
    fake(straddle_price=10.0 * exp / 100 * 0.3)
    assert IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")["decision"] == "BUY"
    fake(straddle_price=10.0 * exp / 100 * 1.8)
    assert IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")["decision"] == "SELL"


def test_guard_refuses_moves_beyond_the_history(fake):
    fake(straddle_price=9.0)                       # implies a ~90% move on a calm share
    a = IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")
    assert a["_d"]["out_of_range"] and a["decision"] == "NO TRADE"


def test_seller_enters_at_bid_and_buyer_at_ask(fake):
    exp = expected_for(fake)
    fake(straddle_price=10.0 * exp / 100 * 1.8, spread=0.1)
    a = IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")
    assert a["decision"] == "SELL" and a["entry_price"] < 10.0 * exp / 100 * 1.8


def test_term_structure_isolates_the_decision(fake):
    before = (TODAY + timedelta(days=4)).isoformat()
    after = (TODAY + timedelta(days=14)).isoformat()
    fake(straddle_price=2.0, expiries=[before, after])
    a = IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")
    assert "C" in a["_d"]["votes"] or "already pricing" in a["_d"]["term_note"]


def test_no_expiry_after_the_decision_is_a_clear_error(fake):
    fake(expiries=[(TODAY + timedelta(days=3)).isoformat()])
    with pytest.raises(SystemExit, match="No option expiry"):
        IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")


def test_unusual_characters_in_headlines_do_not_crash(fake, monkeypatch):
    # Found on 19 Nov 2026: a Savara headline with a non-breaking hyphen crashed a Windows terminal.
    import io
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    IM.safe_output()
    t = fake(straddle_price=2.0)
    t.news = [{"title": "Will Long‑Term Data Change Savara’s Narrative?"}]
    a = IM.analyse("TEST", TODAY + timedelta(days=7), 0.8, "low")
    IM.explain("TEST", "2026-11-26", 0.8, a)
    out.flush()
    assert b"Long?Term" in out.buffer.getvalue()


# ---------- Paper trades from start to finish ----------

def test_stale_quotes_are_not_saved_unless_forced(fake):
    fake(straddle_price=2.0, spread=0.6)
    with pytest.raises(SystemExit, match="Not saved"):
        IM.cmd_trade(args())
    assert not os.path.exists(IM.TRADES)
    IM.cmd_trade(args(force=True))
    assert len(IM.read_trades()) == 1


def test_trade_then_outcome_records_profit_and_loss(fake):
    event = TODAY + timedelta(days=7)
    exp = expected_for(fake)
    price = 10.0 * exp / 100 * 1.8
    fake(straddle_price=price, jump_after=event, jump=0.02)        # options dear, share barely moves
    IM.cmd_trade(args(event_date=event.isoformat()))
    row = IM.read_trades()[0]
    assert row["decision"] == "SELL"

    IM.cmd_outcome(argparse.Namespace(ticker="TEST", event_date=event.isoformat(), note="approved"))
    row = IM.read_trades()[0]
    entry, exit_ = float(row["entry_price"]), float(row["exit_price"])
    assert float(row["pnl_per_contract"]) == pytest.approx((entry - exit_) * 100, abs=0.01)
    assert float(row["actual_move_pct"]) == pytest.approx(2.0, abs=0.01)


def test_logging_the_same_decision_twice_replaces_it(fake):
    fake(straddle_price=2.0)
    IM.cmd_trade(args(force=True))
    IM.cmd_trade(args(force=True, note="second look"))
    rows = IM.read_trades()
    assert len(rows) == 1 and rows[0]["my_note"] == "second look"


def test_summary_and_positions_run_with_and_without_trades(fake, capsys):
    IM.cmd_summary(None)
    assert "No paper trades" in capsys.readouterr().out
    fake(straddle_price=2.0)
    IM.cmd_trade(args(force=True))
    IM.cmd_positions(None)
    assert "OPEN POSITIONS" in capsys.readouterr().out
