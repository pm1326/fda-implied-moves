"""
FDA implied moves: a paper-trading research tool
------------------------------------------------
Question: do options markets correctly price how far a biotech share moves on an FDA decision?

How it decides:
  1. HISTORY   data/model.json holds how far shares moved on ~490 past FDA decisions,
               measured in "normal days' worth of movement", by outcome and company size.
  2. EXPECTED  For an upcoming decision it combines that history with this share's own
               normal daily movement and your probability of approval -> expected move.
  3. IMPLIED   It reads live option prices. The at-the-money straddle (one call + one put)
               costs roughly the move the market expects -> implied move.
  4. DECISION  implied much bigger than expected  -> SELL the straddle (bet on a smaller move)
               implied much smaller than expected -> BUY the straddle (bet on a bigger move)
               otherwise                          -> NO TRADE
  5. RESULT    After the decision it marks the paper trade and records the profit or loss.

Commands:
  python implied_move.py model
  python implied_move.py signal  TICKER EVENT_DATE [--p-approve 0.8]
  python implied_move.py trade   TICKER EVENT_DATE [--p-approve 0.8] [--drug "name"] [--note "why"]
  python implied_move.py outcome TICKER EVENT_DATE [--note "approved"]
  python implied_move.py summary

Paper trading only. No real orders are placed.
"""

import argparse
import csv
import json
import math
import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "trades.csv")
MODEL = os.path.join(HERE, "data", "model.json")
COLUMNS = [
    "logged_at", "ticker", "drug", "event_date", "p_approve", "my_note",
    "share_price", "daily_vol_pct", "size", "dependence", "expiry", "strike",
    "implied_move_pct", "expected_move_pct", "ratio", "decision", "entry_price", "wide_spread",
    "close_before", "close_after", "actual_move_pct", "exit_price", "pnl_per_contract", "outcome_note",
]
SELL_ABOVE = 1.25    # implied is 25%+ bigger than expected  -> options look expensive -> sell
BUY_BELOW = 0.80     # implied is 20%+ smaller than expected -> options look cheap     -> buy
WIDE_SPREAD = 0.25   # flag quotes where (ask - bid) is more than 25% of the midpoint
MIN_GROUP = 20       # need at least this many past events to trust a size group
DEFAULT_P = 0.80     # assumed chance of approval if you do not give one


# ---------- 1. History ----------

def load_model():
    if not os.path.exists(MODEL):
        raise SystemExit("No model yet. Run fetch_fda.py then build_history.py first.")
    return json.load(open(MODEL))


def multiple_for(model, outcome, size, dependence="unknown"):
    """How many normal days' worth of movement past decisions produced.
    Uses the most specific group of similar past decisions that has enough examples:
    same size and same dependence on the drug first, then wider groups."""
    tries = [f"{outcome}/{size}", f"{outcome}/all"]
    if dependence in ("high", "low"):
        tries = [f"{outcome}/{size}/{dependence}"] + ([] if size == "large" else [f"{outcome}/all/{dependence}"]) + tries
    for key in tries:
        g = model["groups"].get(key)
        if g and g["n"] >= MIN_GROUP:
            return g["mean_multiple"], g["n"], key
    g = model["groups"][f"{outcome}/all"]
    return g["mean_multiple"], g["n"], f"{outcome}/all"


# ---------- 2. This share ----------

def share_context(t):
    hist = t.history(period="6mo")
    if hist.empty:
        raise SystemExit("No price data. Check the ticker.")
    spot = float(hist["Close"].iloc[-1])
    vol = float(hist["Close"].pct_change().iloc[-60:].std())
    try:
        cap = float(t.fast_info["marketCap"])
    except Exception:
        cap = None
    size = "unknown" if not cap else "small" if cap < 2e9 else "mid" if cap < 20e9 else "large"
    return spot, vol, size


# ---------- 3. Options ----------

def quote(row):
    bid, ask, last = float(row["bid"] or 0), float(row["ask"] or 0), float(row["lastPrice"] or 0)
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        return {"bid": bid, "ask": ask, "mid": mid, "spread": (ask - bid) / mid}
    return {"bid": last, "ask": last, "mid": last, "spread": 1.0}  # no live quote


def straddle(t, spot, event_date, expiry=None, strike=None):
    expiries = t.options
    if not expiries:
        raise SystemExit("This share has no listed options, so there is nothing to compare.")
    if expiry is None:
        expiry = next((e for e in expiries if datetime.strptime(e, "%Y-%m-%d").date() > event_date), None)
    if expiry is None or expiry not in expiries:
        return None
    chain = t.option_chain(expiry)
    calls, puts = chain.calls, chain.puts
    if strike is None:
        both = sorted(set(calls["strike"]) & set(puts["strike"]), key=lambda k: abs(k - spot))
        if not both:
            return None
        strike = both[0]
    c, p = calls[calls["strike"] == strike], puts[puts["strike"] == strike]
    if c.empty or p.empty:
        return None
    c, p = quote(c.iloc[0]), quote(p.iloc[0])
    return {"expiry": expiry, "strike": float(strike),
            "mid": c["mid"] + p["mid"], "bid": c["bid"] + p["bid"], "ask": c["ask"] + p["ask"],
            "wide": max(c["spread"], p["spread"]) > WIDE_SPREAD}


# ---------- 4. Decision ----------

def analyse(ticker, event_date, p_approve, dependence="unknown"):
    model = load_model()
    t = yf.Ticker(ticker)
    spot, vol, size = share_context(t)
    s = straddle(t, spot, event_date)
    if s is None:
        raise SystemExit(f"No option expiry after {event_date} for {ticker}.")

    m_ap, n_ap, g_ap = multiple_for(model, "approved", size, dependence)
    m_rej, n_rej, g_rej = multiple_for(model, "rejected", size, dependence)
    event_move = (p_approve * m_ap + (1 - p_approve) * m_rej) * vol            # the decision itself
    days = max(int(np.busday_count(date.today(), datetime.strptime(s["expiry"], "%Y-%m-%d").date())), 1)
    normal_move = 0.8 * vol * math.sqrt(max(days - 1, 0))                       # ordinary wobble on the other days
    expected = math.sqrt(event_move ** 2 + normal_move ** 2)

    implied = s["mid"] / spot
    ratio = implied / expected
    decision = "SELL" if ratio > SELL_ABOVE else "BUY" if ratio < BUY_BELOW else "NO TRADE"
    entry = s["bid"] if decision == "SELL" else s["ask"] if decision == "BUY" else s["mid"]

    try:
        news = [(n.get("content") or n).get("title", "") for n in (t.news or [])[:5]]
    except Exception:
        news = []

    return {"share_price": round(spot, 4), "daily_vol_pct": round(vol * 100, 2), "size": size, "dependence": dependence,
            "expiry": s["expiry"], "strike": s["strike"], "implied_move_pct": round(implied * 100, 2),
            "expected_move_pct": round(expected * 100, 2), "ratio": round(ratio, 2), "decision": decision,
            "entry_price": round(entry, 4), "wide_spread": s["wide"],
            "_detail": {"m_ap": m_ap, "n_ap": n_ap, "m_rej": m_rej, "n_rej": n_rej, "g_ap": g_ap, "g_rej": g_rej, "days": days,
                        "event_move": event_move * 100, "normal_move": normal_move * 100,
                        "straddle": s, "news": [n for n in news if n]}}


def explain(ticker, event_date, p, a):
    d = a["_detail"]
    print(f"\n{ticker}   FDA decision {event_date}   chance of approval used: {p:.0%}")
    print(f"  Share price {a['share_price']}   size: {a['size']}   normal daily move: {a['daily_vol_pct']}%")
    stake = {"high": "HIGH (no other approved product: make-or-break)", "low": "LOW (already sells other drugs)"}
    print(f"  Dependence on this drug: {stake.get(a['dependence'], 'not given (use --dependence high or low)')}")
    print("\n  WHAT HISTORY SAYS")
    print(f"    Approved: shares like this moved {d['m_ap']}x a normal day  ({d['n_ap']} past decisions, group {d['g_ap']})")
    print(f"    Rejected: shares like this moved {d['m_rej']}x a normal day  ({d['n_rej']} past decisions, group {d['g_rej']})")
    print(f"    Expected move from the decision     {d['event_move']:.1f}%")
    print(f"    Ordinary wobble over {d['days']} trading days  {d['normal_move']:.1f}%")
    print(f"    EXPECTED MOVE                       {a['expected_move_pct']}%")
    print("\n  WHAT THE OPTIONS MARKET SAYS")
    print(f"    Expiry {a['expiry']}   strike {a['strike']}   straddle costs {d['straddle']['mid']:.2f}")
    print(f"    IMPLIED MOVE                        {a['implied_move_pct']}%")
    print(f"\n  Implied / expected = {a['ratio']}   ->   {a['decision']}")
    if a["decision"] == "SELL":
        print(f"    Options look expensive. Paper trade: sell the straddle at {a['entry_price']} (the bid).")
    elif a["decision"] == "BUY":
        print(f"    Options look cheap. Paper trade: buy the straddle at {a['entry_price']} (the ask).")
    else:
        print("    The market's price is close to what history suggests. No edge, so no trade.")
    if a["wide_spread"]:
        print("    Warning: quotes are wide or stale (is the US market open?). Treat this as unreliable.")
    if d["news"]:
        print("\n  RECENT HEADLINES (read these before setting your chance of approval)")
        for n in d["news"]:
            print("    -", n)


# ---------- 5. Result ----------

def actual_move(ticker, event_date):
    """Close on the last trading day before the decision vs the first trading day after it."""
    hist = yf.Ticker(ticker).history(start=event_date - timedelta(days=10), end=event_date + timedelta(days=10))
    days = hist.index.date
    before, after = hist[days < event_date], hist[days > event_date]
    if before.empty or after.empty:
        raise SystemExit("Not enough trading days either side of the decision yet. Try again tomorrow.")
    c0, c1 = float(before["Close"].iloc[-1]), float(after["Close"].iloc[0])
    return c0, c1, (c1 - c0) / c0 * 100


def read_trades():
    if not os.path.exists(TRADES):
        return []
    with open(TRADES, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_trades(rows):
    with open(TRADES, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


# ---------- Commands ----------

def cmd_model(a):
    m = load_model()
    print(f"\nBuilt {m['built']} from {m['events']} past FDA decisions.\n")
    df = pd.DataFrame(m["groups"]).T[["n", "mean_abs_move_pct", "median_abs_move_pct", "mean_move_pct", "mean_multiple", "worst_pct", "best_pct"]]
    print(df.to_string())
    print("\nmean_multiple = average size of the move, counted in normal days' worth of movement.")


def cmd_signal(a):
    ev = datetime.strptime(a.event_date, "%Y-%m-%d").date()
    explain(a.ticker.upper(), a.event_date, a.p_approve, analyse(a.ticker.upper(), ev, a.p_approve, a.dependence))


def cmd_trade(a):
    ev = datetime.strptime(a.event_date, "%Y-%m-%d").date()
    ticker = a.ticker.upper()
    res = analyse(ticker, ev, a.p_approve, a.dependence)
    explain(ticker, a.event_date, a.p_approve, res)
    if res["wide_spread"] and not a.force:
        raise SystemExit("\nNot saved: the option quotes are stale or too wide to trust. Run this while the US market "
                         "is open (14:30 to 21:00 UK time), or add --force to save anyway.")
    rows = [r for r in read_trades() if not (r["ticker"] == ticker and r["event_date"] == a.event_date)]
    rows.append({"logged_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ticker": ticker, "drug": a.drug or "",
                 "event_date": a.event_date, "p_approve": a.p_approve, "my_note": a.note or "",
                 **{k: v for k, v in res.items() if k != "_detail"}})
    write_trades(rows)
    print(f"\nPaper trade saved to {TRADES}")


def cmd_outcome(a):
    ev = datetime.strptime(a.event_date, "%Y-%m-%d").date()
    ticker = a.ticker.upper()
    rows = read_trades()
    row = next((r for r in rows if r["ticker"] == ticker and r["event_date"] == a.event_date), None)
    if row is None:
        raise SystemExit(f"No paper trade for {ticker} on {a.event_date}. Use the trade command first.")
    c0, c1, move = actual_move(ticker, ev)
    row.update({"close_before": round(c0, 4), "close_after": round(c1, 4), "actual_move_pct": round(move, 2),
                "outcome_note": a.note or ""})

    if row["decision"] in ("SELL", "BUY"):
        strike, entry = float(row["strike"]), float(row["entry_price"])
        t = yf.Ticker(ticker)
        s = None
        try:
            s = straddle(t, c1, ev, expiry=row["expiry"], strike=strike)
        except Exception:
            pass
        if s and not s["wide"]:
            # close the trade at live prices: a seller buys back at the ask, a buyer sells at the bid
            exit_price = s["ask"] if row["decision"] == "SELL" else s["bid"]
        else:
            exit_price = abs(c1 - strike)  # no usable quote: use what the straddle is worth if exercised
        pnl = (entry - exit_price) if row["decision"] == "SELL" else (exit_price - entry)
        row.update({"exit_price": round(exit_price, 4), "pnl_per_contract": round(pnl * 100, 2)})
    write_trades(rows)

    print(f"\n{ticker}: implied {row['implied_move_pct']}%, expected {row['expected_move_pct']}%, actual {move:+.2f}%")
    if row["decision"] in ("SELL", "BUY"):
        print(f"  {row['decision']} at {row['entry_price']}, closed at {row['exit_price']}  ->  P&L per contract: ${row['pnl_per_contract']}")
    else:
        print("  No trade was taken.")


def cmd_positions(a):
    """Open paper positions: logged before a decision and not yet closed."""
    df = pd.DataFrame(read_trades())
    if df.empty or (df["actual_move_pct"] != "").all():
        print("No open positions.")
        return
    pd.set_option("display.width", 200)
    op = df[df["actual_move_pct"] == ""]
    print("\nOPEN POSITIONS (run the outcome command after each decision)\n")
    print(op[["ticker", "event_date", "drug", "p_approve", "dependence", "implied_move_pct", "expected_move_pct",
              "decision", "entry_price", "expiry", "strike"]].to_string(index=False))


def cmd_summary(a):
    df = pd.DataFrame(read_trades())
    if df.empty:
        print("No paper trades yet.")
        return
    pd.set_option("display.width", 200)
    cmd_positions(a)
    done = df[df["actual_move_pct"] != ""].copy()
    if done.empty:
        print("\nNo decisions have happened yet, so there is nothing to score.")
        return

    # Scorecard: for each finished decision, whose forecast of the size of the move was closer?
    for c in ["implied_move_pct", "expected_move_pct", "actual_move_pct"]:
        done[c] = pd.to_numeric(done[c])
    actual = done["actual_move_pct"].abs()
    done["model_miss"] = (done["expected_move_pct"] - actual).abs().round(2)
    done["market_miss"] = (done["implied_move_pct"] - actual).abs().round(2)
    done["closer"] = np.where(done["model_miss"] < done["market_miss"], "MODEL", "MARKET")
    print("\nSCORECARD: how the estimates did\n")
    print(done[["ticker", "event_date", "outcome_note", "expected_move_pct", "implied_move_pct", "actual_move_pct",
                "closer", "decision", "pnl_per_contract"]].to_string(index=False))
    n = len(done)
    print(f"\n{n} decisions scored.  Model closer: {(done['closer'] == 'MODEL').sum()}   Market closer: {(done['closer'] == 'MARKET').sum()}")
    print(f"Average miss   model {done['model_miss'].mean():.1f} points   market {done['market_miss'].mean():.1f} points")
    pnl = pd.to_numeric(done["pnl_per_contract"], errors="coerce").dropna()
    if len(pnl):
        print(f"Paper trades closed: {len(pnl)}   total P&L ${pnl.sum():.2f}   win rate {(pnl > 0).mean():.0%}"
              f"   best ${pnl.max():.2f}   worst ${pnl.min():.2f}")
    print("Note: both forecasts cover every day to option expiry, while the actual move is the two days around the decision.")


def main():
    p = argparse.ArgumentParser(description="Paper trading option-implied vs expected moves around FDA decisions")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("model", help="show what history says").set_defaults(func=cmd_model)
    for name, func, text in [("signal", cmd_signal, "analyse an upcoming decision without saving"),
                             ("trade", cmd_trade, "analyse and save a paper trade")]:
        s = sub.add_parser(name, help=text)
        s.add_argument("ticker")
        s.add_argument("event_date", help="FDA decision date, YYYY-MM-DD")
        s.add_argument("--p-approve", type=float, default=DEFAULT_P, help="your chance of approval, 0 to 1")
        s.add_argument("--dependence", choices=["high", "low", "unknown"], default="unknown",
                       help="high = the company has no other approved product, low = it already sells drugs")
        s.add_argument("--drug")
        s.add_argument("--note", help="your reasoning")
        s.add_argument("--force", action="store_true", help="save the trade even if quotes look stale")
        s.set_defaults(func=func)
    o = sub.add_parser("outcome", help="close a paper trade after the decision")
    o.add_argument("ticker")
    o.add_argument("event_date")
    o.add_argument("--note", help="what happened, e.g. approved")
    o.set_defaults(func=cmd_outcome)
    sub.add_parser("positions", help="show open paper positions").set_defaults(func=cmd_positions)
    sub.add_parser("summary", help="open positions plus a scorecard of how the estimates did").set_defaults(func=cmd_summary)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
