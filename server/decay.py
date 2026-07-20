"""Deterministic salience decay math (unit-tested; the visualization and
benchmark depend on it).

Model: exponential half-life decay, modulated by importance, with
reinforcement boosts on recall.

  salience(t) = base * 0.5 ** (hours_since_touch / half_life(importance))

Importance 1..5 stretches the half-life so important memories linger.
Reinforcement re-bases salience at (current + BOOST, capped at 1.0) and
resets the clock — recall literally keeps memories alive.
"""

BASE_HALF_LIFE_HOURS = 24.0
REINFORCE_BOOST = 0.25
FLOOR = 0.05          # below this: episodes → consolidation/archive, facts drop
                      # from default recall, lessons retire.
DEMO_TIME_SCALE = 1.0  # Demo Mode accelerates time client-side, not here.


def half_life_hours(importance: float) -> float:
    imp = min(5.0, max(1.0, float(importance)))
    return BASE_HALF_LIFE_HOURS * (imp ** 1.5)


def effective_salience(base: float, importance: float, last_touch: float, now: float) -> float:
    dt_hours = max(0.0, now - last_touch) / 3600.0
    return base * (0.5 ** (dt_hours / half_life_hours(importance)))


def reinforce(base: float, importance: float, last_touch: float, now: float):
    """Returns (new_base, new_last_touch) after a recall reinforcement."""
    current = effective_salience(base, importance, last_touch, now)
    return min(1.0, current + REINFORCE_BOOST), now


def is_below_floor(base: float, importance: float, last_touch: float, now: float) -> bool:
    return effective_salience(base, importance, last_touch, now) < FLOOR
