"""Proposal: sports channels play from whichever service runs ahead (closer to live), when it plays properly.
Runs in SHADOW MODE only: its choices are shown on the status page next to the real ones and never used.
To adopt it, its logic moves into rules.py; to drop it, delete this file."""
from rules import decide as current

LEAD_MIN = 3.0  # seconds; smaller differences don't matter to a viewer


def decide(st, m_ok, b_ok, has):
    lead = st.get("lead") or {}
    if st.get("folder") == "Sports" and has["main"] and has["backup"] and lead.get("secs", 0) >= LEAD_MIN:
        fast = lead["ahead"]
        if (fast == "main" and m_ok) or (fast == "backup" and b_ok):
            return fast
    return current(st, m_ok, b_ok, has)
