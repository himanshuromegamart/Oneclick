"""Template helpers for the dashboard."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter
def times(value: int) -> range:
    """Repeat a block N times: ``{% for _ in folder.depth|times %}``.

    Django's template language has no loop counter, and the usual workarounds
    (abusing ``rjust`` to build a string of the right length) break the moment
    the value is not what you assumed. This says what it means.
    """
    try:
        count = int(value)
    except (TypeError, ValueError):
        return range(0)
    # Depth is bounded at 32, but clamp anyway - a template should never be
    # able to emit thousands of elements because a number was wrong.
    return range(max(0, min(count, 32)))
