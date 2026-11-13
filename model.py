"""
The expected-move model, shared by build_history.py, analysis.py and implied_move.py.

It answers: "on past FDA decisions like this one, how many normal days' worth of movement happened?"

It works down a ladder from general to specific:

    approved:  all approvals -> same size -> same dependence -> same second-attempt status
    rejected:  all rejections -> same size -> same dependence -> same reason for rejection

At each rung it blends the specific group's average with the rung above, weighted by how many
past decisions the specific group has. A group with 3 examples barely moves the estimate; a group
with 60 dominates it. This is called shrinkage, and it replaced an earlier hard rule
("ignore any group with fewer than 20 examples") that threw away real information.
"""

LEVELS = {
    "approved": ["size", "dependence", "second_attempt"],
    "rejected": ["size", "dependence", "reason"],
}
K = 8  # a group needs about this many examples before it counts as much as the rung above


def _key(outcome, values):
    return "|".join([outcome] + [str(v) for v in values])


def fit(df, levels=LEVELS):
    """Average multiple and count for every group on every rung. df needs outcome, multiple and the level columns."""
    table = {}
    for outcome, cols in levels.items():
        d = df[df["outcome"] == outcome]
        if d.empty:
            continue
        table[_key(outcome, [])] = [int(len(d)), float(d["multiple"].mean())]
        for depth in range(1, len(cols) + 1):
            for vals, g in d.groupby(cols[:depth]):
                vals = vals if isinstance(vals, tuple) else (vals,)
                table[_key(outcome, vals)] = [int(len(g)), float(g["multiple"].mean())]
    return table


def predict(table, outcome, features, levels=LEVELS, k=K):
    """Shrunk multiple for one decision. Stops going down the ladder at the first unknown feature.
    Returns (multiple, examples in the most specific group used, description of that group)."""
    n, est = table[_key(outcome, [])]
    used, used_n, vals = outcome, n, []
    for col in levels[outcome]:
        v = features.get(col)
        if v is None or str(v) in ("", "unknown", "nan"):
            break
        vals.append(v)
        g = table.get(_key(outcome, vals))
        if g is None:
            break
        est = (g[0] * g[1] + k * est) / (g[0] + k)
        used, used_n = _key(outcome, vals).replace("|", " / "), g[0]
    return est, used_n, used
