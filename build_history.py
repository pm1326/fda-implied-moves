"""
Step 2 of building the history: turn the raw FDA decisions into a dataset of share price moves,
then summarise it into a model.

For every past decision by a listed company we measure:
  - move:      share price change from the close before the decision to the close after it
  - daily_vol: how much the share normally moved per day in the 60 trading days before
  - multiple:  |move| / daily_vol  -> "how many normal days' worth of movement happened"
  - size:      rough company size at the time (small / mid / large)

Writes data/history.csv (one row per decision) and data/model.json (averages by group).
Run fetch_fda.py first.
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
PRICES = os.path.join(RAW, "prices")

# Company name (start of the FDA's sponsor name) -> stock ticker. Private, foreign-listed and
# since-acquired companies are left out because free price data does not cover them.
TICKERS = [
    ("60 DEGREES", "SXTP"), ("ABBVIE", "ABBV"), ("ABEONA", "ABEO"), ("ACADIA", "ACAD"), ("ACHIEVE LIFE", "ACHV"),
    ("ADC THERAP", "ADCT"), ("AGIOS", "AGIO"), ("AKEBIA", "AKBA"), ("ALDEYRA", "ALDX"), ("ALKERMES", "ALKS"),
    ("ALNYLAM", "ALNY"), ("ALPHA COGNITION", "ACOG"), ("ALVOTECH", "ALVO"), ("AMGEN", "AMGN"), ("AMICUS", "FOLD"),
    ("AMNEAL", "AMRX"), ("AMPHASTAR", "AMPH"), ("AMYLYX", "AMLX"), ("APELLIS", "APLS"), ("APPLIED THERAP", "APLT"),
    ("AQUESTIVE", "AQST"), ("ARCUTIS", "ARQT"), ("ARDELYX", "ARDX"), ("ARGENX", "ARGX"), ("ARROWHEAD", "ARWR"),
    ("ARS PHARM", "SPRY"), ("ASCENDIS", "ASND"), ("ASTRAZENECA", "AZN"), ("ATARA", "ATRA"), ("AURINIA", "AUPH"),
    ("AXSOME", "AXSM"), ("BEIGENE", "ONC"), ("BEONE", "ONC"), ("BIOCRYST", "BCRX"), ("BIOGEN", "BIIB"),
    ("BIOHAVEN", "BHVN"), ("BIOMARIN", "BMRN"), ("BIOXCEL", "BTAI"), ("BRIDGEBIO", "BBIO"), ("BRISTOL", "BMY"),
    ("CAPRICOR", "CAPR"), ("CATALYST PHARM", "CPRX"), ("CELCUITY", "CELC"), ("CINGULATE", "CING"), ("CITIUS", "CTXR"),
    ("CLEARSIDE", "CLSD"), ("COHERUS", "CHRS"), ("CORCEPT", "CORT"), ("CORMEDIX", "CRMD"), ("CRINETICS", "CRNX"),
    ("CYTOKINETICS", "CYTK"), ("DAY ONE", "DAWN"), ("DBV TECH", "DBVT"), ("DELCATH", "DCTH"), ("DENALI", "DNLI"),
    ("DISC MEDICINE", "IRON"), ("DR REDDY", "RDY"), ("ELI LILLY", "LLY"), ("ESPERION", "ESPR"), ("ETON", "ETON"),
    ("EVOKE", "EVOK"), ("EVOLUS", "EOLS"), ("EYEPOINT", "EYPT"), ("FENNEC", "FENC"), ("GENMAB", "GMAB"),
    ("GENZYME", "SNY"), ("GERON", "GERN"), ("GILEAD", "GILD"), ("GLAUKOS", "GKOS"), ("GLAXOSMITHKLINE", "GSK"),
    ("HALOZYME", "HALO"), ("HARMONY", "HRMY"), ("HARROW", "HROW"), ("HERON", "HRTX"), ("IMMUNOCORE", "IMCR"),
    ("INCYTE", "INCY"), ("INDIVIOR", "INDV"), ("INSMED", "INSM"), ("IONIS", "IONS"), ("ITERUM", "ITRM"),
    ("LTERUM", "ITRM"), ("JANSSEN", "JNJ"), ("JAZZ", "JAZZ"), ("JOURNEY", "DERM"), ("KALA ", "KALA"),
    ("KALVISTA", "KALV"), ("KARYOPHARM", "KPTI"), ("KURA", "KURA"), ("LENZ", "LENZ"), ("LEXICON", "LXRX"),
    ("LIQUIDIA", "LQDA"), ("MADRIGAL", "MDGL"), ("MEDIWOUND", "MDWD"), ("MERCK SHARP", "MRK"), ("MERUS", "MRUS"),
    ("MESOBLAST", "MESO"), ("MILESTONE", "MIST"), ("MINERVA", "NERV"), ("MIRUM", "MIRM"), ("MSD", "MRK"),
    ("NEUROCRINE", "NBIX"), ("NOVARTIS", "NVS"), ("NOVO", "NVO"), ("NUVALENT", "NUVL"), ("NUVATION", "NUVB"),
    ("OCULAR THERAP", "OCUL"), ("OMEROS", "OMER"), ("OUTLOOK", "OTLK"), ("PFIZER", "PFE"), ("PHARMING", "PHAR"),
    ("PHATHOM", "PHAT"), ("PTC THERAP", "PTCT"), ("PURPLE BIOTECH", "PPBT"), ("REGENERON", "REGN"), ("RENGENERON", "REGN"),
    ("REGENXBIO", "RGNX"), ("REPLIMUNE", "REPL"), ("REVOLUTION MED", "RVMD"), ("RHYTHM", "RYTM"), ("RIGEL", "RIGL"),
    ("ROCKET PHARM", "RCKT"), ("ROCKWELL", "RMTI"), ("SANOFI", "SNY"), ("SAREPTA", "SRPT"), ("SCHOLAR ROCK", "SRRK"),
    ("SCILEX", "SCLX"), ("SIGA", "SIGA"), ("SOLENO", "SLNO"), ("SUPERNUS", "SUPN"), ("SYNDAX", "SNDX"),
    ("TAKEDA", "TAK"), ("TARSUS", "TARS"), ("TELIX", "TLX"), ("TEVA", "TEVA"), ("TG THERAP", "TGTX"),
    ("TONIX", "TNXP"), ("TRAVERE", "TVTX"), ("ULTRAGENYX", "RARE"), ("UNICYCIVE", "UNCY"), ("UNITED THERAP", "UTHR"),
    ("UROGEN", "URGN"), ("VANDA", "VNDA"), ("VERA THERAP", "VERA"), ("VERASTEM", "VSTM"), ("VERRICA", "VRCA"),
    ("VERTEX", "VRTX"), ("VIRIDIAN", "VRDN"), ("X4 PHARM", "XFOR"), ("XERIS", "XERS"), ("ZEVRA", "ZVRA"),
]


def ticker_for(name):
    n = re.sub(r"[^A-Z0-9 ]", "", name.upper()) + " "
    for prefix, t in TICKERS:
        if n.startswith(prefix):
            return t
    return None


def size_bucket(cap):
    if cap is None or cap != cap:
        return "unknown"
    return "small" if cap < 2e9 else "mid" if cap < 20e9 else "large"


def load_prices(ticker):
    """Daily prices since mid 2017 and the current share count, cached on disk."""
    path = os.path.join(PRICES, ticker + ".csv")
    meta = os.path.join(PRICES, ticker + ".json")
    if os.path.exists(path) and os.path.exists(meta):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df, json.load(open(meta)).get("shares")
    t = yf.Ticker(ticker)
    df = t.history(start="2017-06-01", auto_adjust=True)[["Close"]]
    if df.empty:
        raise ValueError("no price data (delisted, acquired or renamed)")
    df.index = df.index.tz_localize(None)
    try:
        shares = float(t.fast_info["shares"])
    except Exception:
        shares = None
    df.to_csv(path)
    json.dump({"shares": shares}, open(meta, "w"))
    time.sleep(0.3)
    return df, shares


def measure(df, shares, d):
    """Move across the decision day, the share's normal daily movement beforehand, and rough size."""
    before, after = df[df.index < d], df[df.index > d]
    if len(before) < 62 or after.empty or (d - before.index[-1]).days > 6 or (after.index[0] - d).days > 6:
        return None
    c0, c1 = float(before["Close"].iloc[-1]), float(after["Close"].iloc[0])
    vol = float(before["Close"].pct_change().iloc[-60:].std())
    if not vol or vol != vol:
        return None
    move = (c1 - c0) / c0
    return {"close_before": round(c0, 4), "close_after": round(c1, 4), "move_pct": round(move * 100, 2),
            "daily_vol_pct": round(vol * 100, 2), "multiple": round(abs(move) / vol, 2),
            "size": size_bucket(c0 * shares if shares else None)}


def main():
    os.makedirs(PRICES, exist_ok=True)
    approvals = json.load(open(os.path.join(RAW, "approvals.json")))
    crls = json.load(open(os.path.join(RAW, "crls.json")))
    events = [{"date": a["date"], "sponsor": a["sponsor"], "drug": a["drug"], "outcome": "approved",
               "new_molecule": a["class"] == "TYPE 1", "priority_review": a["priority"] == "PRIORITY"} for a in approvals]
    events += [{"date": c["date"], "sponsor": c["sponsor"], "drug": "", "outcome": "rejected",
                "new_molecule": "", "priority_review": ""} for c in crls]

    rows, seen, skipped = [], set(), 0
    for e in sorted(events, key=lambda e: e["date"]):
        t = ticker_for(e["sponsor"])
        if not t or (t, e["date"]) in seen:
            continue
        seen.add((t, e["date"]))
        try:
            df, shares = load_prices(t)
            m = measure(df, shares, pd.Timestamp(e["date"]))
        except Exception as ex:
            print("  skipped", t, ex)
            m = None
        if m is None:
            skipped += 1
            continue
        rows.append({"date": e["date"], "ticker": t, "sponsor": e["sponsor"], "drug": e["drug"], "outcome": e["outcome"],
                     "new_molecule": e["new_molecule"], "priority_review": e["priority_review"], **m})

    hist = pd.DataFrame(rows)
    hist.to_csv(os.path.join(HERE, "data", "history.csv"), index=False)

    # The model: for each outcome and company size, how big was the move on average?
    model = {"built": datetime.now().strftime("%Y-%m-%d"), "events": len(hist), "groups": {}}
    hist["abs_move"] = hist["move_pct"].abs()
    for outcome in ["approved", "rejected"]:
        for size in ["small", "mid", "large", "all"]:
            g = hist[hist["outcome"] == outcome]
            if size != "all":
                g = g[g["size"] == size]
            if len(g):
                model["groups"][f"{outcome}/{size}"] = {
                    "n": int(len(g)), "mean_abs_move_pct": round(g["abs_move"].mean(), 2),
                    "median_abs_move_pct": round(g["abs_move"].median(), 2),
                    "mean_move_pct": round(g["move_pct"].mean(), 2),
                    "mean_multiple": round(g["multiple"].mean(), 2), "median_multiple": round(g["multiple"].median(), 2),
                    "worst_pct": round(g["move_pct"].min(), 2), "best_pct": round(g["move_pct"].max(), 2)}
    json.dump(model, open(os.path.join(HERE, "data", "model.json"), "w"), indent=1)

    print(f"{len(hist)} decisions measured across {hist['ticker'].nunique()} companies ({skipped} skipped for missing prices)")
    print(pd.DataFrame(model["groups"]).T.to_string())


if __name__ == "__main__":
    main()
