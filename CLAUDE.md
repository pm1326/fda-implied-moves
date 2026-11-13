# FDA implied moves

Paper-trading research project: option-implied vs expected share moves around FDA decisions. Owner is a first-year bioengineering student learning Python; explain changes in plain language.

## Standing instructions

- **Keep JOURNAL.md up to date.** Whenever the project changes (new feature, bug found, model change, paper trade logged or closed, a finding or a new limitation), add to JOURNAL.md in the same piece of work: what changed, what problem prompted it, what was learned, plus a dated row in the Log table. Written in the owner's first-person voice. (Asked for on 2026-11-13.)
- Keep README.md's numbers in step with data/model.json after rebuilding the history.
- Paper trading only. Never place or suggest real orders.
- Ask before pushing anything new and personal to the public GitHub repo; routine code and journal updates can be pushed.

## Running

Use the project's Python: `.\.venv\Scripts\python.exe`. Git and gh are installed at `C:\Program Files\Git\cmd` and `C:\Program Files\GitHub CLI` and may not be on PATH.

- `fetch_fda.py` then `build_history.py` rebuild data/history.csv and data/model.json.
- `implied_move.py model | signal | trade | outcome | summary`.
- Option quotes are only live during US market hours (14:30 to 21:00 UK).
