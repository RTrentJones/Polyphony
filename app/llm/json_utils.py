"""Tolerant JSON extraction for LLM replies.

Models wrap JSON in code fences, prepend prose, or get truncated mid-structure
when they hit max_tokens. Every structured call site parses through here so a
recoverable reply never turns into a user-facing failure.
"""

import json

from app.core.logging_config import setup_logging

logger = setup_logging("llm.json_utils")


def extract_json_array(text: str) -> list:
    """Best-effort extraction of a JSON array from an LLM reply.

    Handles code fences, leading/trailing prose, and truncation (repairs by
    trimming to the last complete object and closing open brackets).
    Raises ValueError when nothing parseable remains.
    """
    t = (text or "").replace("```json", "").replace("```", "").strip()

    # Whole reply as JSON: a clean array, or a single object (one node) which we
    # wrap — NOT reach into for its inner array, which silently returned the
    # wrong subset before.
    try:
        whole = json.loads(t)
        if isinstance(whole, list):
            return whole
        if isinstance(whole, dict):
            # {"outline": [...]} / {"findings": [...]} — a single wrapper key over
            # the array → unwrap it. A real node ({"title","summary","children"})
            # has several keys → treat the object itself as one node (don't reach
            # into its inner array, which returned the wrong subset before).
            if len(whole) == 1:
                (only,) = whole.values()
                if isinstance(only, list):
                    return only
            return [whole]
    except json.JSONDecodeError:
        pass

    start = t.find("[")
    if start != -1:
        seg = t[start:]
        # Clean array from the first '[' (whole, then trimmed to its last ']' for
        # trailing prose).
        for cand in _dedup([seg, _to_last_bracket(seg)]):
            arr = _as_list(cand)
            if arr is not None:
                return arr
        # Truncation repair on the first '[' BEFORE scanning later brackets, so a
        # truncated outer array isn't mistaken for its own complete-but-empty
        # inner "children": [] (which parses as a valid []).
        repaired = _repair_truncated(seg)
        if repaired is not None:
            return repaired

    # Prose before the array may itself contain a bracket ("the beats [see
    # below]:\n[…]"); the first '[' was the prose one. Try each SUBSEQUENT '[' .
    for i in range(start + 1, len(t)):
        if t[i] != "[":
            continue
        seg = t[i:]
        for cand in _dedup([seg, _to_last_bracket(seg)]):
            arr = _as_list(cand)
            if arr is not None:
                return arr

    raise ValueError("Reply contains no parseable JSON array")


def _as_list(s: str | None):
    if not s:
        return None
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def _to_last_bracket(seg: str) -> str | None:
    end = seg.rfind("]")
    return seg[: end + 1] if end != -1 else None


def _dedup(items: list) -> list:
    out = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def _repair_truncated(t: str):
    """Cut back to a '}' boundary (a complete object) and append whatever closers
    the open-bracket stack still needs; keep trimming until a cut parses. Returns
    the repaired list or None."""
    work = t
    while True:
        cut = work.rfind("}")
        if cut <= 0:
            return None
        work = work[: cut + 1]
        closers = _close_sequence(work)
        if closers is not None:
            arr = _as_list(work + closers)
            if arr is not None:
                logger.warning(
                    "Repaired truncated JSON array from LLM reply",
                    extra_fields={"event": "json_truncation_repaired"},
                )
                return arr
        work = work[:cut]


def _close_sequence(s: str) -> str | None:
    """The closing brackets an unterminated JSON fragment needs, tracked with
    a string-aware stack; None when the fragment ends inside a string."""
    stack: list[str] = []
    in_str = False
    escaped = False
    for ch in s:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
    if in_str:
        return None
    return "".join("]" if c == "[" else "}" for c in reversed(stack))
