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
    start = t.find("[")
    if start == -1:
        raise ValueError("Reply contains no JSON array")
    t = t[start:]

    # Whole thing (common case), then the outermost [...] span.
    try:
        parsed = json.loads(t)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    end = t.rfind("]")
    if end != -1:
        try:
            parsed = json.loads(t[: end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # Truncation repair: cut back to a '}' boundary (a complete object) and
    # append whatever closers the open bracket stack still needs. Keep
    # trimming until a cut parses or nothing is left.
    work = t
    while True:
        cut = work.rfind("}")
        if cut <= 0:
            raise ValueError("Reply contains no parseable JSON array")
        work = work[: cut + 1]
        closers = _close_sequence(work)
        if closers is not None:
            try:
                parsed = json.loads(work + closers)
                if isinstance(parsed, list):
                    logger.warning(
                        "Repaired truncated JSON array from LLM reply",
                        extra_fields={"event": "json_truncation_repaired"},
                    )
                    return parsed
            except json.JSONDecodeError:
                pass
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
