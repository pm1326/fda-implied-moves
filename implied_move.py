"""
FDA implied moves: a paper-trading research tool
------------------------------------------------
Question: do options markets correctly price how far a biotech share moves on an FDA decision?

The at-the-money straddle (one call + one put) costs roughly the move the market expects.
The tool judges that price in several independent ways and only trades when they agree:

  A. AVERAGE        Is the implied move bigger or smaller than the average move on similar past decisions?
  B. DISTRIBUTION   In what share of similar past decisions was the move bigger than the options need?
  C. TERM STRUCTURE Comparing options that expire before the decision with ones that expire after
                    isolates what the market is charging for the decision itself.
  D. IMPLIED ODDS   What chance of approval would make history agree with the option price?
                    (Shown for comparison with your own view. Not a vote, because it reuses A's inputs.)
  GUARD             If the implied move is beyond almost anything in the history, the model has no
                    basis to judge, so it refuses to trade.

"Similar" means same company size, same dependence on the drug, and (if you say) whether the drug
was rejected before and what a rejection would most likely be about. See model.py.

Commands:
  python implied_move.py model
  python implied_move.py signal  TICKER EVENT_DATE [options]
  python implied_move.py trade   TICKER EVENT_DATE [options] [--drug "name"] [--note "why"]
  python implied_move.py positions
  python implied_move.py outcome TICKER EVENT_DATE [--note "approved"]
  python implied_move.py summary

Options for signal and trade:
  --p-approve 0.8          your chance of approval
  --dependence high|low    high = no other approved product
  --second-attempt         this drug has been rejected before
  --risk clinical|manufacturing   if it is rejected, the most likely reason

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

import model as M

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "trades.csv")
MODEL = os.path.join(HERE, "data", "model.json")
HISTORY = os.path.join(HERE, "data", "history.csv")
COLUMNS = [
    "logged_at", "ticker", "drug", "event_date", "p_approve", "my_note",
    "share_price", "daily_vol_pct", "size", "dependence", "second_attempt", "risk", "expiry", "strike",
    "implied_move_pct", "expected_move_pct", "ratio", "pct_bigger", "term_ratio", "market_p", "votes",
    "decision", "entry_price", "wide_spread",
    "close_before", "close_after", "actual_move_pct", "exit_price", "pnl_per_contract", "outcome_note",
]
EXPENSIVE, CHEAP = 1.25, 0.80          # metrics A and C: implied / expected above or below these
FEW_BIGGER, MANY_BIGGER = 0.20, 0.50   # metric B: share of similar past moves bigger than the options need
WIDE_SPREAD = 0.25                     # flag quotes where (ask - bid) is more than 25% of the midpoint
DEFAULT_P = 0.80


# ---------- History ----------

def load_model():
    if not os.path.exists(MODEL):
        raise SystemExit("No model yet. Run fetch_fda.py then build_history.py first.")
    return json.load(open(MODEL))


def similar(hist, outcome, size, dependence):
    """Past decisions most like this one: same outcome and dependence, and same size if there are enough."""
    d = hist[hist["outcome"] == outcome]
    if dependence in ("high", "low"):
        d = d[d["dependence"] == dependence]
    same_size = d[d["size"] == size]
    return same_size if len(same_size) >= 15 else d


# ---------- This share and its options ----------

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


def quote(row):
    bid, ask, last = float(row["bid"] or 0), float(row["ask"] or 0), float(row["lastPrice"] or 0)
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2
        return {"bid": bid, "ask": ask, "mid": mid, "spread": (ask - bid) / mid}
    return {"bid": last, "ask": last, "mid": last, "spread": 1.0}  # no live quote


def straddle(t, spot, expiry, strike=None):
    """Price of the at-the-money straddle for one expiry."""
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


def trading_days(expiry):
    return max(int(np.busday_count(date.today(), datetime.strptime(expiry, "%Y-%m-%d").date())), 1)


# ---------- The analysis ----------

def analyse(ticker, event_date, p, dependence="unknown", second_attempt=None, risk="unknown"):
    model, hist = load_model(), pd.read_csv(HISTORY)
    t = yf.Ticker(ticker)
    spot, vol, size = share_context(t)
    expiries = [e for e in (t.options or [])]
    if not expiries:
        raise SystemExit("This share has no listed options, so there is nothing to compare.")
    as_date = lambda e: datetime.strptime(e, "%Y-%m-%d").date()
    after = next((e for e in expiries if as_date(e) > event_date), None)
    if after is None:
        raise SystemExit(f"No option expiry after {event_date} for {ticker}.")
    s = straddle(t, spot, after)
    if s is None:
        raise SystemExit("No usable at-the-money options.")
    days = trading_days(after)
    implied = s["mid"] / spot

    # What history expects
    m_ap, n_ap, g_ap = M.predict(model["table"], "approved", {"size": size, "dependence": dependence, "second_attempt": second_attempt})
    m_rej, n_rej, g_rej = M.predict(model["table"], "rejected", {"size": size, "dependence": dependence, "reason": risk})
    up, down = m_ap * vol, m_rej * vol                       # size of move if approved / if rejected
    event = p * up + (1 - p) * down
    normal = 0.8 * vol * math.sqrt(max(days - 1, 0))         # ordinary wobble on the other days
    expected = math.sqrt(event ** 2 + normal ** 2)
    implied_event = math.sqrt(max(implied ** 2 - normal ** 2, 0))   # what the options charge for the decision alone

    votes = {}
    label = lambda r: "expensive" if r > EXPENSIVE else "cheap" if r < CHEAP else "fair"

    # A. Average
    ratio = implied / expected
    votes["A"] = label(ratio)

    # B. Distribution: how often was the move on similar decisions bigger than the options need?
    sim_ap, sim_rej = similar(hist, "approved", size, dependence), similar(hist, "rejected", size, dependence)
    bigger = (p * float((sim_ap["multiple"] * vol > implied_event).mean())
              + (1 - p) * float((sim_rej["multiple"] * vol > implied_event).mean()))
    votes["B"] = "expensive" if bigger < FEW_BIGGER else "cheap" if bigger > MANY_BIGGER else "fair"

    # C. Term structure: options expiring before the decision show what the market charges for ordinary days
    term_ratio, term_note = None, "no option expiry between now and the decision"
    before = [e for e in expiries if date.today() < as_date(e) < event_date]
    if before:
        sb = straddle(t, spot, before[-1])
        days_b = trading_days(before[-1])
        if sb and not sb["wide"] and not s["wide"] and days_b >= 2:
            daily_var = (sb["mid"] / spot / 0.8) ** 2 / days_b
            event_var = (implied / 0.8) ** 2 - daily_var * (days - 1)
            if event_var > 0:
                term_ratio = 0.8 * math.sqrt(event_var) / event
                votes["C"] = label(term_ratio)
                term_note = f"options expiring {before[-1]} imply {math.sqrt(daily_var) * 80:.1f}% a day before the decision"
            else:
                term_note = (f"options expiring {before[-1]}, before the decision, are already pricing a big move. "
                             "The market may expect early news, so the decision cannot be isolated")
        else:
            term_note = "quotes too wide or stale to compare expiries"

    # D. Implied odds: the chance of approval at which history would match the option price
    market_p = (down - implied_event) / (down - up) if down > up else None

    # Guard: is the implied move beyond almost anything seen on similar decisions?
    ceiling = max(sim_ap["move_pct"].abs().quantile(0.95), sim_rej["move_pct"].abs().quantile(0.95)) / 100
    out_of_range = implied_event > ceiling

    exp, chp = list(votes.values()).count("expensive"), list(votes.values()).count("cheap")
    if out_of_range:
        decision = "NO TRADE"
    elif exp >= 2 and chp == 0:
        decision = "SELL"
    elif chp >= 2 and exp == 0:
        decision = "BUY"
    else:
        decision = "NO TRADE"
    entry = s["bid"] if decision == "SELL" else s["ask"] if decision == "BUY" else s["mid"]

    try:
        news = [(n.get("content") or n).get("title", "") for n in (t.news or [])[:5]]
    except Exception:
        news = []

    return {"share_price": round(spot, 4), "daily_vol_pct": round(vol * 100, 2), "size": size, "dependence": dependence,
            "second_attempt": "" if second_attempt is None else second_attempt, "risk": risk,
            "expiry": after, "strike": s["strike"], "implied_move_pct": round(implied * 100, 2),
            "expected_move_pct": round(expected * 100, 2), "ratio": round(ratio, 2),
            "pct_bigger": round(bigger * 100), "term_ratio": "" if term_ratio is None else round(term_ratio, 2),
            "market_p": "" if market_p is None else round(market_p, 2),
            "votes": " ".join(f"{k}:{v}" for k, v in votes.items()), "decision": decision,
            "entry_price": round(entry, 4), "wide_spread": s["wide"],
            "_d": {"up": up * 100, "down": down * 100, "n_ap": n_ap, "n_rej": n_rej, "g_ap": g_ap, "g_rej": g_rej,
                   "event": event * 100, "normal": normal * 100, "days": days, "implied_event": implied_event * 100,
                   "straddle": s["mid"], "term_note": term_note, "ceiling": ceiling * 100, "out_of_range": out_of_range,
                   "n_sim": (len(sim_ap), len(sim_rej)), "votes": votes, "news": [n for n in news if n]}}


def explain(ticker, event_date, p, a):
    d = a["_d"]
    print(f"\n{ticker}   FDA decision {event_date}   your chance of approval: {p:.0%}")
    print(f"  Share price {a['share_price']}   size: {a['size']}   normal daily move: {a['daily_vol_pct']}%")
    print(f"  Dependence: {a['dependence']}   rejected before: {a['second_attempt'] if a['second_attempt'] != '' else 'not given'}"
          f"   likely reason if rejected: {a['risk']}")

    print("\n  WHAT HISTORY EXPECTS")
    print(f"    If approved: about {d['up']:.0f}% move   ({d['n_ap']} past decisions in group: {d['g_ap']})")
    print(f"    If rejected: about {d['down']:.0f}% move   ({d['n_rej']} past decisions in group: {d['g_rej']})")
    print(f"    Blended by your odds: {d['event']:.1f}% from the decision, plus {d['normal']:.1f}% ordinary wobble over {d['days']} days")
    print(f"    EXPECTED MOVE  {a['expected_move_pct']}%")

    print("\n  WHAT THE OPTIONS MARKET SAYS")
    print(f"    Expiry {a['expiry']}, strike {a['strike']}: straddle costs {d['straddle']:.2f}")
    print(f"    IMPLIED MOVE   {a['implied_move_pct']}%   (about {d['implied_event']:.0f}% of that is for the decision itself)")

    v = d["votes"]
    print("\n  FOUR WAYS OF JUDGING THE PRICE")
    print(f"    A. Average         implied is {a['ratio']}x the expected move                         -> {v['A']}")
    print(f"    B. Distribution    {a['pct_bigger']}% of similar past decisions moved more than the options need   -> {v['B']}")
    if "C" in v:
        print(f"    C. Term structure  decision-only price is {a['term_ratio']}x history ({d['term_note']})   -> {v['C']}")
    else:
        print(f"    C. Term structure  not usable: {d['term_note']}")
    if a["market_p"] == "":
        print("    D. Implied odds    not available")
    elif 0 <= a["market_p"] <= 1:
        print(f"    D. Implied odds    history matches the option price at a {a['market_p']:.0%} chance of approval (yours: {p:.0%})")
    else:
        print("    D. Implied odds    no chance of approval makes history match this price")

    print(f"\n  VERDICT: {a['decision']}")
    if d["out_of_range"]:
        print(f"    The options price in a bigger move than 95% of similar past decisions ({d['ceiling']:.0f}%).")
        print("    The market is seeing something the history cannot. The model has no basis to disagree, so no trade.")
    elif a["decision"] == "SELL":
        print(f"    At least two measures say expensive and none say cheap. Paper trade: sell the straddle at {a['entry_price']} (the bid).")
    elif a["decision"] == "BUY":
        print(f"    At least two measures say cheap and none say expensive. Paper trade: buy the straddle at {a['entry_price']} (the ask).")
    else:
        print("    The measures do not agree strongly enough. No trade.")
    if a["wide_spread"]:
        print("    Warning: quotes are wide or stale. Treat the prices as rough.")
    print(f"    (Similar past decisions used for B and the guard: {d['n_sim'][0]} approvals, {d['n_sim'][1]} rejections.)")
    if d["news"]:
        print("\n  RECENT HEADLINES")
        for n in d["news"]:
            print("    -", n)


# ---------- After the decision ----------

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
    print("Out-of-sample tests of the model are in results/analysis.md.")


def run(a):
    ev = datetime.strptime(a.event_date, "%Y-%m-%d").date()
    res = analyse(a.ticker.upper(), ev, a.p_approve, a.dependence, True if a.second_attempt else None, a.risk)
    explain(a.ticker.upper(), a.event_date, a.p_approve, res)
    return res


def cmd_signal(a):
    run(a)


def cmd_trade(a):
    res = run(a)
    if res["wide_spread"] and not a.force:
        raise SystemExit("\nNot saved: the option quotes are stale or too wide to trust. Run this while the US market "
                         "is open (14:30 to 21:00 UK time), or add --force to save anyway.")
    ticker = a.ticker.upper()
    rows = [r for r in read_trades() if not (r["ticker"] == ticker and r["event_date"] == a.event_date)]
    rows.append({"logged_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ticker": ticker, "drug": a.drug or "",
                 "event_date": a.event_date, "p_approve": a.p_approve, "my_note": a.note or "",
                 **{k: v for k, v in res.items() if k != "_d"}})
    write_trades(rows)
    print(f"\nSaved to {TRADES}")


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
        s = None
        try:
            s = straddle(yf.Ticker(ticker), c1, row["expiry"], strike=strike)
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
    pd.set_option("display.width", 220)
    op = df[df["actual_move_pct"] == ""]
    print("\nOPEN POSITIONS (run the outcome command after each decision)\n")
    print(op[["ticker", "event_date", "drug", "p_approve", "implied_move_pct", "expected_move_pct", "votes",
              "decision", "entry_price", "expiry", "strike"]].to_string(index=False))


def cmd_summary(a):
    df = pd.DataFrame(read_trades())
    if df.empty:
        print("No paper trades yet.")
        return
    pd.set_option("display.width", 220)
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
    print(f"\n{len(done)} decisions scored.  Model closer: {(done['closer'] == 'MODEL').sum()}   Market closer: {(done['closer'] == 'MARKET').sum()}")
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
        s.add_argument("--second-attempt", action="store_true", help="this drug has been rejected before")
        s.add_argument("--risk", choices=["clinical", "manufacturing", "other", "unknown"], default="unknown",
                       help="if it is rejected, the most likely reason")
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
