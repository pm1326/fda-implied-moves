# FDA Implied Moves

Do options markets correctly price how far a biotech share moves on an FDA drug decision?

## The idea

An FDA decision is a near-binary event with a known date. Approval can send a small biotech up sharply; a rejection can halve it. Option traders know the date is coming and price the risk in.

A **straddle** (one call and one put at the same strike, at the current share price) pays off if the share moves a lot in either direction, so its price is roughly the market's expected size of the move:

```
implied move ≈ (call price + put price) / share price
```

For each decision I record the implied move the day before, then the actual move afterwards, and compare:

```
ratio = |actual move| / implied move
```

An average ratio above 1 means options underpriced the risk; below 1 means they overpriced it. The interesting part is how this varies by outcome (approval or rejection), company size, disease area, and first approval versus a new use for an existing drug.

Before each decision I also record my own prediction, using my bioengineering background to assess the trial data and mechanism, to build a track record of that judgement.

## Usage

```bash
pip install -r requirements.txt

# The day before a decision
python implied_move.py log TICKER 2026-12-15 --drug "drug name" --prediction "approve; move looks overpriced"

# After the decision
python implied_move.py outcome TICKER 2026-12-15 --note "approved"

# All events so far
python implied_move.py summary
```

Results are saved in `events.csv`.

## Method notes

- **Event dates:** FDA decision dates (PDUFA dates), which are fixed and public, rather than trial readouts.
- **Expiry:** the first option expiry after the decision date, so the option is still alive when the news lands.
- **Strike:** the strike nearest the share price that has both a call and a put.
- **Prices:** the midpoint of bid and ask. Events with a bid-ask spread wider than 25% of the midpoint are flagged as unreliable.
- **Actual move:** close on the last trading day before the decision to close on the first trading day after it.

## Limitations

- The straddle also prices normal day-to-day volatility up to expiry, not only the event. A later version will separate the event's share using an expiry before the decision.
- Free data from Yahoo Finance gives current option prices only, so events must be logged before they happen.
- Small samples: conclusions need dozens of events.
- If options look overpriced on average, selling straddles looks profitable until one rejection causes a huge loss. Any result has to be read with that tail risk in mind. This is a research project, not a trading strategy.

## Status

In progress: collecting the first events.
