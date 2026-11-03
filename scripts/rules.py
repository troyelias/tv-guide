"""The switching rules: which service a channel should play from after this morning's test.
Kept on their own so a candidate version (rules_candidate.py) can run in SHADOW MODE next to them:
the candidate's choices are recorded and compared, but never used, until it is promoted."""


def need_streak(st):
    """Good mornings in a row on the main service before switching back. A channel that failed there more than
    once in the last 7 mornings is flaky, so it has to prove itself for longer."""
    return 2 if st.get("main_hist", []).count(0) <= 1 else 4


def decide(st, m_ok, b_ok, has):
    """st: the channel's memory (use, main_streak, main_hist...); m_ok/b_ok: played this morning (None = not tested);
    has: {"main": bool, "backup": bool} - whether each service carries it. Returns "main" or "backup"."""
    use = st.get("use", "main")
    if not has["main"] and has["backup"]:
        return "backup"
    if has["main"] and not has["backup"]:
        return "main"
    if use == "main" and m_ok is False and b_ok:
        return "backup"
    if use == "backup" and ((m_ok and st.get("main_streak", 0) >= need_streak(st)) or (b_ok is False and m_ok)):
        return "main"
    return use
