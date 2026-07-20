"""Cheap deterministic token estimator (≈4 chars/token, English text).

Good enough for budgets and gauges; exact provider counts are logged from
API usage fields when available.
"""

def estimate(text) -> int:
    if text is None:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return max(1, len(text) // 4)


def estimate_messages(messages) -> int:
    return sum(estimate(m.get("content", "")) + 4 for m in messages)
