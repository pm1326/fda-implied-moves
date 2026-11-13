# FDA Implied Moves

Do options markets correctly price how far a biotech share moves on an FDA drug decision? This project measures that against history and paper trades the difference.

## The idea

An FDA decision is a near-binary event with a known date. Option traders price that risk in. A **straddle** (one call and one put at the same strike, at the current share price) pays off if the share moves a lot in either direction, so its price is roughly the move the market expects:

```
implied move ≈ (call price + put price) / share price
```

The tool compares that with the move history suggests, and takes a paper trade when they disagree:

| Implied vs expected | Meaning | Paper trade |
|---|---|---|
| 25%+ bigger | Options look expensive | Sell the straddle |
| 20%+ smaller | Options look cheap | Buy the straddle |
| In between | No clear edge | No trade |

## The history

`fetch_fda.py` downloads every decision since 2018 from the FDA's own public data: approvals from Drugs@FDA and rejections from the FDA's published Complete Response Letters. `build_history.py` matches them to listed companies and measures the share price move across each decision.

Current dataset: **488 decisions across 123 companies** (`data/history.csv`).

| Outcome | Company size | Decisions | Average size of move | Average direction | Worst | Best |
|---|---|---|---|---|---|---|
| Approved | Small (under $2bn) | 73 | 13.5% | +4.9% | -32.8% | +79.4% |
| Approved | Mid ($2bn to $20bn) | 89 | 6.4% | +2.9% | -26.2% | +38.1% |
| Approved | Large (over $20bn) | 228 | 2.1% | +0.5% | -14.2% | +38.2% |
| Rejected | Small | 39 | 28.5% | -22.6% | -76.8% | +29.8% |
| Rejected | Mid | 33 | 8.5% | -4.4% | -43.8% | +14.7% |
| Rejected | Large | 26 | 2.1% | -1.2% | -10.8% | +5.0% |

**How much the company depends on the drug matters most.** Using the FDA's full approval record, each decision is tagged by whether the company already had an approved product:

| Outcome | Company | Decisions | Average size of move | Average direction |
|---|---|---|---|---|
| Rejected | Small, no other product | 28 | 35.5% | -32.5% |
| Rejected | Small, has other products | 11 | 10.5% | +2.5% |
| Approved | Small, no other product | 34 | 15.0% | +4.6% |
| Approved | Small, has other products | 39 | 12.3% | +5.2% |

Each move is also stored as a **multiple** of the share's normal daily movement in the 60 trading days before the decision. A small company's rejection is on average about 6 normal days' worth of movement in one day.

## How the expected move is calculated

```
event move    = (P(approve) x approval multiple + P(reject) x rejection multiple) x this share's normal daily move
normal wobble = 0.8 x normal daily move x sqrt(other trading days until expiry)
expected move = sqrt(event move^2 + normal wobble^2)
```

The multiples come from the history, by company size. The probability of approval is my own input for each decision, based on the trial data and mechanism. That is where the biology comes in. The tool prints recent headlines for the company to help set it.

## Usage

```bash
pip install -r requirements.txt
python fetch_fda.py        # download FDA decisions
python build_history.py    # measure past moves and build the model

python implied_move.py model
python implied_move.py signal  TICKER 2026-12-15 --p-approve 0.7 --dependence high
python implied_move.py trade   TICKER 2026-12-15 --p-approve 0.7 --dependence high --drug "name" --note "why"
python implied_move.py positions
python implied_move.py outcome TICKER 2026-12-15 --note "approved"
python implied_move.py summary
```

`--dependence high` means the company has no other approved product; `low` means it already sells drugs. `summary` shows open positions and a scorecard of whether my forecast or the market's was closer on each finished decision. Research notes on upcoming decisions are in `research/`, and the development history is in `JOURNAL.md`.

Paper trades are saved in `trades.csv`. A seller enters at the bid and exits at the ask, and a buyer the reverse, so trading costs are included.

## Limitations

- **Survivorship:** companies that were acquired or delisted have no free price data and are missing. Many of the biggest winners and losers are among them.
- **Rejections are incomplete:** the FDA has published only some of its rejection letters, so the mix of approvals and rejections in the data is not the true rate. That is why the probability of approval is an input rather than taken from the data.
- **Company size is rough:** it uses today's share count with the share price at the time.
- **Decision dates:** the FDA action date is used. Companies sometimes announce a day later; the before-and-after window covers that.
- **No options backtest:** free data gives current option prices only, so the trading rule can only be tested going forward.
- **Tail risk:** selling straddles wins small and often, then loses big on a surprise. Results must be read with the worst case in mind.
- **Averages hide a lot:** two decisions in the same group can move very differently.

This is a research project with paper trades only. It is not investment advice.

## Status

History and model built. Collecting paper trades on upcoming decisions.

Built with Claude as a coding assistant.
