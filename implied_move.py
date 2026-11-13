"""
FDA implied moves
-----------------
Measures how far the options market expects a biotech share to move on an
FDA decision, then compares that with how far it actually moved.

Three commands:

  python implied_move.py log TICKER EVENT_DATE --drug "name" --prediction "approve"
      Run the day before the decision. Pulls the live option chain, works out
      the implied move from the at-the-money straddle, and saves a row to events.csv.

  python implied_move.py outcome TICKER EVENT_DATE
      Run after the decision. Fills in the actual move and the ratio.

  python implied_move.py summary
      Prints every event and the average of actual move / implied move.

The core idea: a straddle (one call + one put at the same strike, at the
current share price) pays off if the share moves a lot either way. Its price
is roughly the market's expected size of the move:

      implied move  ~=  (call price + put price) / share price
"""

import argparse
import csv
import os
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.csv")
COLUMNS = [
    "logged_at", "ticker", "drug", "event_date", "my_prediction",
    "share_price", "expiry", "strike", "call_mid", "put_mid", "straddle",
    "implied_move_pct", "wide_spread",
    "close_before", "close_after", "actual_move_pct", "ratio", "outcome_note",
]
WIDE_SPREAD = 0.25  # flag options where (ask - bid) is more than 25% of the midpoint


# ---------- Option maths ----------

def mid_price(row):
    """Midpoint of bid and ask. Falls back to the last traded price if there is no live quote."""
    bid, ask = float(row["bid"]), float(row["ask"])
    if bid > 0 and ask > 0:
        return (bid + ask) / 2, (ask - bid) / ((bid + ask) / 2)
    return float(row["lastPrice"]), 1.0  # no live quote: treat the spread as wide


def pick_expiry(expiries, event_date):
    """The first expiry strictly after the decision date, so the option is still alive when the news lands."""
    for e in expiries:
        if datetime.strptime(e, "%Y-%m-%d").date() > event_date:
            return e
    return None


def implied_move(ticker, event_date):
    t = yf.Ticker(ticker)
    hist = t.history(period="5d")
    if hist.empty:
        raise SystemExit(f"No price data for {ticker}. Check the ticker.")
    spot = float(hist["Close"].iloc[-1])

    expiries = t.options
    if not expiries:
        raise SystemExit(f"{ticker} has no listed options, so there is no implied move to measure.")
    expiry = pick_expiry(expiries, event_date)
    if expiry is None:
        raise SystemExit(f"No option expiry after {event_date} for {ticker}.")

    chain = t.option_chain(expiry)
    calls, puts = chain.calls, chain.puts

    # At-the-money: the strike closest to the share price that has both a call and a put.
    strikes = sorted(set(calls["strike"]) & set(puts["strike"]), key=lambda k: abs(k - spot))
    if not strikes:
        raise SystemExit("No strike has both a call and a put.")
    strike = strikes[0]

    call_mid, call_spread = mid_price(calls[calls["strike"] == strike].iloc[0])
    put_mid, put_spread = mid_price(puts[puts["strike"] == strike].iloc[0])
    straddle = call_mid + put_mid

    return {
        "share_price": round(spot, 4),
        "expiry": expiry,
        "strike": strike,
        "call_mid": round(call_mid, 4),
        "put_mid": round(put_mid, 4),
        "straddle": round(straddle, 4),
        "implied_move_pct": round(straddle / spot * 100, 2),
        "wide_spread": max(call_spread, put_spread) > WIDE_SPREAD,
    }


# ---------- Actual move ----------

def actual_move(ticker, event_date):
    """Close on the last trading day before the decision vs close on the first trading day after it.
    The window spans the decision day, so it catches news released before, during or after market hours."""
    hist = yf.Ticker(ticker).history(start=event_date - timedelta(days=10), end=event_date + timedelta(days=10))
    if hist.empty:
        raise SystemExit(f"No price history for {ticker} around {event_date}.")
    days = hist.index.date
    before = hist[days < event_date]
    after = hist[days > event_date]
    if before.empty or after.empty:
        raise SystemExit("Not enough trading days either side of the decision yet. Try again tomorrow.")
    c0, c1 = float(before["Close"].iloc[-1]), float(after["Close"].iloc[0])
    return c0, c1, (c1 - c0) / c0 * 100


# ---------- The event log (events.csv) ----------

def read_log():
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_log(rows):
    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})


# ---------- Commands ----------

def cmd_log(a):
    ev = datetime.strptime(a.event_date, "%Y-%m-%d").date()
    ticker = a.ticker.upper()
    m = implied_move(ticker, ev)
    rows = [r for r in read_log() if not (r["ticker"] == ticker and r["event_date"] == a.event_date)]
    rows.append({"logged_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "ticker": ticker,
                 "drug": a.drug or "", "event_date": a.event_date, "my_prediction": a.prediction or "", **m})
    write_log(rows)

    print(f"\n{ticker}  decision on {a.event_date}  {a.drug or ''}")
    print(f"  Share price      {m['share_price']}")
    print(f"  Expiry used      {m['expiry']}   strike {m['strike']}")
    print(f"  Call + put       {m['call_mid']} + {m['put_mid']} = {m['straddle']}")
    print(f"  Implied move     +/- {m['implied_move_pct']}%")
    if m["wide_spread"]:
        print("  Warning: wide bid-ask spread, so this estimate is unreliable.")
    print(f"\nSaved to {LOG_FILE}")


def cmd_outcome(a):
    ev = datetime.strptime(a.event_date, "%Y-%m-%d").date()
    ticker = a.ticker.upper()
    rows = read_log()
    row = next((r for r in rows if r["ticker"] == ticker and r["event_date"] == a.event_date), None)
    if row is None:
        raise SystemExit(f"No logged event for {ticker} on {a.event_date}. Log it first.")
    c0, c1, move = actual_move(ticker, ev)
    implied = float(row["implied_move_pct"])
    row.update({"close_before": round(c0, 4), "close_after": round(c1, 4),
                "actual_move_pct": round(move, 2), "ratio": round(abs(move) / implied, 2) if implied else "",
                "outcome_note": a.note or ""})
    write_log(rows)
    print(f"\n{ticker}: implied +/- {implied}%, actual {move:+.2f}%, ratio {row['ratio']}")
    print("Ratio above 1: options underpriced the move. Below 1: they overpriced it.")


def cmd_summary(a):
    df = pd.DataFrame(read_log())
    if df.empty:
        print("No events logged yet.")
        return
    show = ["ticker", "event_date", "drug", "my_prediction", "implied_move_pct", "actual_move_pct", "ratio", "outcome_note"]
    pd.set_option("display.width", 160)
    print(df[show].to_string(index=False))
    done = pd.to_numeric(df["ratio"], errors="coerce").dropna()
    if len(done):
        print(f"\n{len(done)} completed events. Average ratio {done.mean():.2f}, median {done.median():.2f}")
    else:
        print("\nNo outcomes recorded yet.")


def main():
    p = argparse.ArgumentParser(description="Option-implied vs actual moves around FDA decisions")
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("log", help="record the implied move before a decision")
    l.add_argument("ticker")
    l.add_argument("event_date", help="decision date, YYYY-MM-DD")
    l.add_argument("--drug")
    l.add_argument("--prediction", help="your call before the event, e.g. 'approve, move overpriced'")
    l.set_defaults(func=cmd_log)

    o = sub.add_parser("outcome", help="record the actual move after a decision")
    o.add_argument("ticker")
    o.add_argument("event_date")
    o.add_argument("--note", help="what happened, e.g. 'approved' or 'rejected'")
    o.set_defaults(func=cmd_outcome)

    s = sub.add_parser("summary", help="show all events")
    s.set_defaults(func=cmd_summary)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
