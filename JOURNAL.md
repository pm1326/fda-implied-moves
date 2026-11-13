# Project journal

A running record of how this project developed: what I set out to do, what went wrong, what I learned and what changed as a result. Built with Claude as a coding assistant.

## Where the idea came from

I am a first-year Molecular Bioengineering student at Imperial who wants to work in asset management or trading. I wanted a project that sits where biology meets finance and that I could explain and defend.

Ideas I considered and why I moved on:

| Idea | Why not (yet) |
|---|---|
| Stock pitches | Too common. Everyone applying has one. |
| Earnings call tone signal using an AI model | Good, but not biology. Parked for later. |
| Drug pipeline valuation calculator | Too basic as a plain calculator. More interesting run backwards, to find the approval odds the market is pricing in. Parked. |
| **Options-implied moves before FDA decisions** | Chosen. It combines options, which I am learning at Imperial's Algorithmic Trading Society, with judging drugs, which is my degree. |

**The question:** do options markets correctly price how far a biotech share moves on an FDA decision?

## Version 1: a measuring tool (13 November 2026)

**What it did.** For one share and one decision date, it read the live option chain, priced the at-the-money straddle, and reported the implied move. After the decision it recorded the actual move and the ratio between the two.

**First test.** Moderna: share price $231.35, straddle $30.06, implied move ±13%.

**Problems found**

1. **It did not decide anything.** It only compared what the market expected with what happened. Useful as research, but it could not say whether a price was too high or too low before the event.
2. **No history.** Free data from Yahoo Finance gives today's option prices only. There is no way to look up what options cost before a decision in 2022.
3. **Stale quotes.** Run outside US market hours, the bid and ask are missing or far apart. I added a warning when the spread is wider than 25% of the midpoint.

## Version 2: history, a model and paper trading (13 November 2026)

**The change in thinking.** I could not get past option prices, but past share prices are free. So the history can tell me how far shares actually moved on past decisions, and that gives me my own forecast to compare against today's option prices.

**Building the history**

- I first planned to write a list of past decisions from memory and news searches. That would have been slow and error-prone on dates.
- Better source found: the FDA publishes this itself. Approvals come from the Drugs@FDA database. Rejections come from the Complete Response Letters the FDA began publishing.
- Raw data: 1,228 approvals and 370 rejection letters since 2018.
- The FDA lists company names, not stock tickers, so I built a table matching about 150 company names to tickers.
- Result: **488 decisions across 123 companies** with a measured share price move.

**What the history showed**

| Outcome | Company size | Decisions | Average size of move | Average direction |
|---|---|---|---|---|
| Approved | Small | 73 | 13.5% | +4.9% |
| Approved | Mid | 89 | 6.4% | +2.9% |
| Approved | Large | 228 | 2.1% | +0.5% |
| Rejected | Small | 39 | 28.5% | -22.6% |
| Rejected | Mid | 33 | 8.5% | -4.4% |
| Rejected | Large | 26 | 2.1% | -1.2% |

- Small companies are where the risk is. The worst rejection was -77%.
- Large companies barely react.
- Approval is not always good for the share: one small company fell 33% on an approval.

**Design decisions and why**

| Decision | Reason |
|---|---|
| Measure moves as a multiple of the share's normal daily movement | A 10% move means something different for a calm share and a wild one. Multiples make companies comparable. |
| The chance of approval is my own input | The FDA has published only some rejection letters, so the data cannot give a true approval rate. It is also where my biology judgement belongs. |
| Add ordinary day-to-day movement to the forecast | The straddle prices every day until expiry, not just the decision day. Comparing it with the event alone would be unfair. |
| Paper trades enter and exit at the worse side of the quote | Sellers get the bid and pay the ask. Ignoring that would flatter the results. |
| Show recent headlines | To help set the chance of approval before each decision. |

**Problems found**

1. **Missing companies.** 36 decisions were dropped because the company has since been acquired or delisted and has no free price history. Those are often the biggest winners and losers, so the history is biased towards survivors.
2. **Company size is rough.** I use today's share count with the old share price. Small biotechs issue many new shares, so their past size is overstated.
3. **A download bug.** When a ticker returned no data, the script crashed on an empty table. Fixed by checking for empty data first.
4. **The thresholds are my choice.** Sell above 1.25 and buy below 0.80 are judgement, not derived from data.

## First run on real upcoming decisions (13 November 2026)

| Company | Decision | Expected move | Implied move | Verdict |
|---|---|---|---|---|
| Capricor | 22 Nov | 33% | 99% | Sell |
| Savara | 22 Nov | 18% | 44% | Sell |
| BridgeBio | 27 Nov | 10% | 12% | No trade |
| Cogent | 30 Nov | 14% | 19% | Sell |

Run before the US market opened, so prices were stale and no paper trades were saved.

**The most important finding so far.** The Capricor result is probably the model being wrong, not the market. Capricor depends almost entirely on this one drug, and the FDA has rejected it once already. The decision could double the share or crush it. My history averages all small companies together, including ones where the drug was a minor product, so it has no way of knowing this decision is make-or-break.

Selling a straddle there would be betting against a huge move on a coin flip. That is exactly the trade that wipes out months of small gains.

**Lesson:** the model treats "small company" as one group. What matters is how much of the company rides on this single decision.

## Version 3: how much does the company depend on this drug? (13 November 2026)

**The problem.** The Capricor run showed the model could not tell a make-or-break decision from a minor one.

**The fix.** The FDA's database lists every approval ever, so for each past decision I counted how many products the company already had approved. None means high dependence; one or more means low.

**What it showed**

| Outcome | Company | Decisions | Average size of move | Average direction |
|---|---|---|---|---|
| Rejected | Small, no other product | 28 | 35.5% | -32.5% |
| Rejected | Small, has other products | 11 | 10.5% | +2.5% |
| Approved | Small, no other product | 34 | 15.0% | +4.6% |
| Approved | Small, has other products | 39 | 12.3% | +5.2% |
| Approved | Any size, no other product | 59 | 12.4% | +4.8% |
| Approved | Any size, has other products | 331 | 4.0% | +1.4% |

This is the clearest result so far. A rejection costs a one-product small company about a third of its value on average. For a small company that already sells drugs, a rejection barely registers.

**What changed in the tool**

- New `--dependence high|low` input. The forecast now uses past decisions of the same size and the same dependence, and prints which group it used.
- `trade` now refuses to save when option quotes are stale, unless forced.
- New `positions` command for open paper trades.
- `summary` is now a scorecard: for each finished decision it shows my forecast, the market's forecast and the actual move, and counts which was closer.

**Research on the four upcoming decisions** (full notes in `research/2026-11-decisions.md`)

| Company | What is at stake |
|---|---|
| Capricor | Make or break. Rejected once before, and an advisory panel voted 9 to 3 against in July 2026. |
| Savara | Make or break. Its only product, and about $150m of financing depends on approval. |
| BridgeBio | Minor to moderate. It already sells Attruby and has two other launches. |
| Cogent | Major but spread out. First product, with three separate decisions on the same drug. |

**Re-run with dependence and researched odds**

| Company | Approval odds used | Expected move before | Expected move now | Implied move | Verdict |
|---|---|---|---|---|---|
| Capricor | 35% | 33% | 49% | 98% | Sell |
| Savara | 80% | 18% | 19% | 45% | Sell |
| BridgeBio | 85% | 10% | 10% | 12% | No trade |
| Cogent | 85% | 14% | 15% | 19% | Sell |

**Problems still open**

1. **The model still says sell on Capricor.** Its forecast rose from 33% to 49%, but options imply about 98%. I do not trust the sell. A drug with a negative panel vote and a prior rejection is more extreme than the average one-product company, and my data cannot see that.
2. **"High dependence" is too coarse.** Capricor and Cogent are both "high", but Cogent has three chances with the same drug.
3. **Option quotes were still flagged as stale** minutes after the US market opened. Free quotes are delayed by about 15 minutes and sometimes come without a bid or ask. I need to check whether they are ever reliable for small companies.
4. **Counting earlier products depends on my ticker table.** A company approved under a different name would be miscounted.

## Improvements identified

1. ~~Add a measure of dependence on the drug.~~ Done in version 3. Next: refine it with whether the drug was rejected before and whether an advisory panel voted, since those are what made Capricor extreme.
2. **Separate first approvals from follow-ons.** The data already flags new molecules and priority reviews. A new use for an existing drug matters far less.
3. **Only save trades during US market hours**, and refuse when quotes are stale.
4. **Record the worst case, not just the average.** Report the largest single loss beside the win rate.
5. **Look for historical option prices.** Imperial's library may give access to a research database with them, which would allow a proper backtest.
6. **Recover the missing companies** with a paid or academic price source, to reduce survivorship bias.
7. **Use an AI model to read the FDA's rejection letters** and classify the reason (manufacturing, safety, effectiveness). The letter text is already downloaded.
8. **A simple web page** so the tool can be used without running code.

## What I have learned

- A straddle's price is the market's forecast of how far a share will move.
- Free data shapes the whole design. No past option prices meant building my own forecast from share prices.
- Official primary sources beat a hand-made list.
- An average can be badly wrong for a single case. The interesting work is in finding what separates the cases.
- A model disagreeing with the market is usually a reason to doubt the model first.

## Log

| Date | What happened |
|---|---|
| 12 Nov 2026 | Chose the project from several biology and finance ideas. |
| 13 Nov 2026 | Version 1 built and published: implied move from the straddle. |
| 13 Nov 2026 | Version 2: FDA history of 488 decisions, expected-move model, paper trading. |
| 13 Nov 2026 | First run on four upcoming decisions. Found the single-drug dependence weakness. |
| 13 Nov 2026 | Version 3: dependence measure, positions, scorecard, stale-quote guard. Researched what is at stake for the four decisions. |
| 13 Nov 2026 | Reminder set for 20 Nov, the last trading day before the Capricor and Savara decisions. |
