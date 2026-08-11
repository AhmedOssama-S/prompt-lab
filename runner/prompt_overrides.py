"""Temporarily substitute a prompt file's contents for the duration of a call.

This is what lets Compare run a business-team draft through the *real* engine.
A draft only ever replaces ONE file; everything else about the run -- the other
fragments, the retry loop, the per-provider call parameters -- stays exactly as
production does it. That is the whole point: a draft has to be tested the way it
would actually ship, not through a simplified single-call preview.

Implemented as a contextvar consulted by prompt_loader._read() rather than an
extra argument threaded through all 19 call sites. Two reasons that matters:
the render_*() signatures stay identical to production's own prompt-building
functions (easier to audit against source), and the override cannot leak into a
call it wasn't scoped around -- contextvars are per-thread/per-task, so
Streamlit's reruns and the engine's own retries can't cross-contaminate.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Optional, Tuple

# key: the same *parts tuple prompt_loader._read() is called with,
# e.g. ("pillar_summarizer", "v2", "overall_ar.txt")
_overrides: ContextVar[Optional[Dict[Tuple[str, ...], str]]] = ContextVar("prompt_overrides", default=None)


@contextmanager
def prompt_override(overrides: Dict[Tuple[str, ...], str]) -> Iterator[None]:
    """Within this block, _read(*parts) returns overrides[parts] when present.

    Nests correctly: an inner block sees the outer block's entries too, with its
    own taking precedence.
    """
    current = _overrides.get() or {}
    token = _overrides.set({**current, **overrides})
    try:
        yield
    finally:
        _overrides.reset(token)


def lookup(parts: Tuple[str, ...]) -> Optional[str]:
    """Returns the override for these path parts, or None. Used by _read()."""
    active = _overrides.get()
    return active.get(parts) if active else None


def active_overrides() -> Dict[Tuple[str, ...], str]:
    """Currently-active overrides. For UI display / run logging, so a logged
    comparison records that a draft (not a stored version) produced it."""
    return dict(_overrides.get() or {})
