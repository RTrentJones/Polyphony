"""LLM-drafted outlines / beat sheets.

An outline is a JSON list of nodes: {"node_id", "title", "summary", "children"}.
Top-level nodes map naturally onto chapters ("promote" endpoint) and carry the
chapter's structural cast: pov, character_ids, act_index, threads (SPEC-10).
Children are the chapter's planned beats, each with its own character_ids,
secondary_names, setting and tone (SPEC-24); promote stores them as
`chapters.planned_beats` (SPEC-23).
"""

from typing import Any, Iterable, NamedTuple, Optional
from uuid import UUID, uuid4

from app.core.llm_text import (
    MAX_CANON_CHARS,
    STORY_MATERIAL_NOTICE,
    as_quoted_block,
    clean_for_llm,
)
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array
from app.parsing import names
from app.planning.planned_beat import MAX_SECONDARY_NAMES

logger = setup_logging("planning.outline")


class CastIndex:
    """A book's cast as a lookup for outline structural fields (SPEC-10).

    Resolves a character id, exact name, recorded alias, or an unambiguous name
    token ("Milo" for Milo Voss) to the character's id. Built from anything with
    `id`, `name` and optional `aliases` (ORM rows or test stand-ins); a
    character without an id cannot be referenced and is skipped.
    """

    def __init__(self, characters: Iterable[Any]):
        self.names: dict[str, str] = {}
        self._forms: dict[str, str] = {}
        tokens: dict[str, set[str]] = {}
        for c in characters or []:
            cid = getattr(c, "id", None)
            name = (getattr(c, "name", None) or "").strip()
            if cid is None or not name:
                continue
            cid = str(cid)
            self.names[cid] = name
            for form in [name, *(getattr(c, "aliases", None) or [])]:
                if isinstance(form, str) and form.strip():
                    self._forms.setdefault(form.strip().lower(), cid)
            for tok in name.split():
                if len(tok) > 1:
                    tokens.setdefault(tok.lower(), set()).add(cid)
        for tok, ids in tokens.items():
            if len(ids) == 1:
                self._forms.setdefault(tok, next(iter(ids)))

    def resolve(self, ref: Any) -> Optional[str]:
        """The id of the character `ref` names, or None."""
        if not isinstance(ref, (str, UUID)):
            return None
        text = str(ref).strip()
        if text in self.names:
            return text
        return self._forms.get(text.lower())


class People(NamedTuple):
    ids: list[str]  # cast character ids, first-mention order, deduped
    secondary: list[str]  # named people who are not cast (rule below), deduped
    invented: list[str]  # everything else, as given


def classify_people(
    refs: Iterable[Any], cast: CastIndex, known_text: Optional[str]
) -> People:
    """Classify person references (names, aliases or character ids) against a
    book's cast: the single rule behind every "who is in this chapter / beat /
    scene" decision (review S2).

    `known_text` is names.norm(canon_known_text(canon)), normalised ONCE by the
    caller per outline or request (it is not re-normalised here). None means
    "the names were already vetted" (re-resolving STORED secondary names
    against today's cast, or an author's own edit).

    For each ref, in order (non-str/non-UUID and blank refs are skipped):
      1. cast.resolve(ref) gives an id -> `ids`.
      2. A ref that parses as a UUID but does not resolve (a trashed or
         foreign character) -> `invented`. A dead id is never a name.
      3. A name, whitespace-collapsed and cut to 100 chars:
         -> `secondary` when known_text is None or
            names.mentions(known_text, name);
         -> `invented` otherwise.
    `secondary` is deduped case-insensitively and capped at
    MAX_SECONDARY_NAMES (12); names past the cap are dropped. Never raises.
    Callers decide what `invented` means: dropped on the generation path (the
    fidelity audit warns), rejected under `strict`, ignored at generate time."""
    out = People([], [], [])
    seen: set[str] = set()
    try:
        items = list(refs or [])
    except TypeError:
        return out
    for ref in items:
        if not isinstance(ref, (str, UUID)):
            continue
        given = str(ref)
        if not given.strip():
            continue
        cid = cast.resolve(ref) if cast is not None else None
        if cid is not None:
            if cid not in out.ids:
                out.ids.append(cid)
            continue
        if _uuid_str(given) is not None:
            out.invented.append(given)
            continue
        name = " ".join(given.split())[:100]
        if known_text is None or names.mentions(known_text, name):
            if name.lower() not in seen:
                seen.add(name.lower())
                if len(out.secondary) < MAX_SECONDARY_NAMES:
                    out.secondary.append(name)
        else:
            out.invented.append(given)
    return out


def _uuid_str(value: Any) -> Optional[str]:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _secondary_names(
    node: dict, cast: Optional[CastIndex]
) -> tuple[Optional[list[str]], list[str]]:
    """(the node's `secondary_names`, ids of those that ARE in the cast).

    Trimmed, deduped case-insensitively, at most 12 of at most 100 chars. With
    a cast, a name that resolves to it is cast, not secondary: its id is
    returned to join `character_ids` instead (SPEC-24). None when the node
    carries no list."""
    raw = node.get("secondary_names")
    if not isinstance(raw, list):
        return None, []
    names: list[str] = []
    moved: list[str] = []
    for item in raw:
        if not isinstance(item, (str, int, float)) or isinstance(item, bool):
            continue
        name = str(item).strip()[:100]
        if not name:
            continue
        cid = cast.resolve(name) if cast is not None else None
        if cid is not None:
            if cid not in moved:
                moved.append(cid)
        elif name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return names[:MAX_SECONDARY_NAMES], moved


def _cast_ids(
    node: dict,
    cast: Optional[CastIndex],
    secondary: Optional[list[str]],
    unknown: list[str],
) -> tuple[Optional[list], list[str]]:
    """(the refs the node gave, their resolved ids). `character_ids` may arrive
    as ids or, from the model's `characters`, as names; with a cast both
    resolve to ids of THIS book. Without one (a restore, which must not depend
    on today's cast) stored ids are kept as they are. A name that resolves to
    nobody is appended to `unknown`, unless the node lists it as secondary."""
    refs = node.get("character_ids")
    if not isinstance(refs, list):
        refs = node.get("characters")
    allowed = {n.lower() for n in secondary or []}
    ids: list[str] = []
    for ref in refs if isinstance(refs, list) else []:
        cid = cast.resolve(ref) if cast is not None else _uuid_str(ref)
        if cid is None:
            if str(ref).strip().lower() not in allowed:
                unknown.append(str(ref))
        elif cid not in ids:
            ids.append(cid)
    return (refs if isinstance(refs, list) else None), ids


def _raise_unknown(strict: bool, unknown: list[str]) -> None:
    if strict and unknown:
        raise ValueError(
            "Not in this book's cast: " + ", ".join(dict.fromkeys(unknown))
        )


def _structural_fields(node: dict, cast: Optional[CastIndex], strict: bool) -> dict:
    """pov / character_ids / act_index / threads / secondary_names of a
    top-level node (SPEC-10, SPEC-24). An unknown cast entry is dropped, or
    rejected with `strict`, unless the node lists it in `secondary_names`."""
    out: dict = {}
    unknown: list[str] = []
    secondary, moved = _secondary_names(node, cast)
    refs, ids = _cast_ids(node, cast, secondary, unknown)
    pov = node.get("pov")
    if isinstance(pov, str) and pov.strip():
        pov = pov.strip()[:255]
        if cast is not None:
            pid = cast.resolve(pov)
            if pid is None:
                unknown.append(pov)
                pov = ""
            else:
                # The POV character is in the chapter by definition.
                pov = cast.names[pid]
                if pid not in ids:
                    ids.insert(0, pid)
        if pov:
            out["pov"] = pov
    ids.extend(cid for cid in moved if cid not in ids)
    if ids or refs is not None:
        out["character_ids"] = ids
    act = node.get("act_index")
    if isinstance(act, int) and not isinstance(act, bool) and act >= 0:
        out["act_index"] = act
    threads = node.get("threads")
    if isinstance(threads, list):
        out["threads"] = [str(t).strip()[:255] for t in threads if str(t).strip()]
    if secondary is not None:
        out["secondary_names"] = secondary
    _raise_unknown(strict, unknown)
    return out


def _beat_fields(node: dict, cast: Optional[CastIndex], strict: bool) -> dict:
    """A child beat's own cast, secondaries, setting and tone (SPEC-24 §1.1).

    `character_ids` resolves exactly like a chapter's and is absent when the
    model gave no cast; a secondary that is in the cast moves into it."""
    out: dict = {}
    unknown: list[str] = []
    secondary, moved = _secondary_names(node, cast)
    refs, ids = _cast_ids(node, cast, secondary, unknown)
    ids.extend(cid for cid in moved if cid not in ids)
    if ids or refs is not None:
        out["character_ids"] = ids
    if secondary is not None:
        out["secondary_names"] = secondary
    for key, cap in (("setting", 500), ("tone", 200)):
        if isinstance(node.get(key), str):
            out[key] = node[key].strip()[:cap]
    _raise_unknown(strict, unknown)
    return out


def validate_outline_nodes(
    nodes, *, cast: Optional[Iterable[Any]] = None, strict: bool = False
) -> list[dict]:
    """Normalize/validate outline JSON into [{node_id, title, summary, children,
    pov?, character_ids?, act_index?, threads?, secondary_names?}].

    Every node gets a stable `node_id`, kept when it already has a valid one, so
    edits, versions and restores preserve the link a promoted chapter holds.
    Top-level nodes keep their structural cast (SPEC-10): with `cast`, names
    resolve through aliases to this book's character ids, and an unknown is
    dropped (generation; the fidelity audit warns) or, with `strict`, rejected
    (the author's own edit) — unless the node lists it in `secondary_names`.

    Children are beats (SPEC-24): each may carry its own `character_ids`
    (from ids or `characters` names, resolved the same way), `secondary_names`,
    `setting` and `tone`. A secondary that resolves to the cast moves into
    `character_ids`. Without `cast` (a restore) stored values are kept as-is.
    """
    index = cast if cast is None or isinstance(cast, CastIndex) else CastIndex(cast)
    return _validate(nodes, index, strict, top=True)


def _validate(nodes, cast: Optional[CastIndex], strict: bool, top: bool) -> list:
    if not isinstance(nodes, list):
        raise ValueError("Outline must be a list of nodes")
    normalized = []
    for node in nodes:
        if not isinstance(node, dict) or not str(node.get("title", "")).strip():
            continue
        out = {
            "node_id": _uuid_str(node.get("node_id")) or str(uuid4()),
            "title": str(node.get("title", "")).strip()[:500],
            "summary": str(node.get("summary", "")).strip()[:2000],
            "children": _validate(node.get("children", []) or [], cast, strict, False),
        }
        out.update((_structural_fields if top else _beat_fields)(node, cast, strict))
        normalized.append(out)
    return normalized


async def generate_outline(
    *,
    title: str,
    synopsis: str,
    genre: str = "",
    character_bible: str = "",
    kind: str = "outline",
    chapters_target: int = 12,
    user_id: Optional[UUID] = None,
) -> list[dict]:
    """Draft a chapter-level outline (or beat sheet) from the book's canon.

    The whole canon reaches the model. Nothing here truncates: the synopsis and
    the character bible arrive complete, fenced as story material rather than
    filtered (docs/ADR-002-book-as-root.md §4). The previous version of this
    function cut the synopsis to 2,000 chars and received an always-empty bible,
    which is why it invented "Elara" (docs/BRD.md §1).

    Raises:
        TextTooLargeError: if the assembled canon exceeds MAX_CANON_CHARS — loud
            by design, never a silent cut.
    """
    shape = (
        f"about {chapters_target} chapter nodes, each with 2-4 child scene beats"
        if kind == "outline"
        else "a beat sheet of 12-18 top-level beats (Save the Cat style), no children"
    )

    canon_blocks = [as_quoted_block(synopsis, "synopsis")]
    if character_bible:
        canon_blocks.append(as_quoted_block(character_bible, "characters"))
    canon = "\n\n".join(b for b in canon_blocks if b)
    clean_for_llm(canon, max_chars=MAX_CANON_CHARS, label="canon")

    logger.info(
        "Outline canon assembled",
        extra_fields={
            "event": "outline_canon_assembled",
            "canon_chars": len(canon),
            "synopsis_chars": len(synopsis or ""),
            "bible_chars": len(character_bible or ""),
            "has_cast": bool(character_bible),
        },
    )

    prompt = f"""You are a story architect. Draft {shape} for this book.

{STORY_MATERIAL_NOTICE}

Title: {clean_for_llm(title)}
Genre: {clean_for_llm(genre) or "unspecified"}

{canon}

Develop THIS premise faithfully — this is the author's story, not a prompt to
riff on. Use the characters, setting, and central conflict the premise (and
character bible) name; expand and dramatize what is given. Do NOT introduce
major new characters or plot lines the premise doesn't imply, and do not swap
its conflict for a different one. Recover the shape the premise is reaching for
rather than inventing a new story around the same title.

Within that fidelity, shape the arc so the through-line is unmistakable and
causally ordered:
- open with an inciting incident that sets the premise in motion,
- build rising complications with escalating stakes,
- turn on a midpoint reversal or revelation,
- drive to a climax that pays off the central conflict,
- close with a resolution / new equilibrium.
Each node's summary must say what CHANGES (a decision, reversal, or consequence),
not just what happens. Order nodes so each follows causally from the last.

Return ONLY valid JSON — an array of nodes:
[
  {{"title": "Chapter/beat title", "summary": "1-3 sentences of what changes",
    "children": [{{"title": "scene beat", "summary": "...", "children": []}}]}}
]

JSON:"""

    result = await get_llm_client().generate(
        [{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=8192,
        user_id=user_id,
        purpose="outline",
    )
    try:
        nodes = extract_json_array(result.text)
    except ValueError as e:
        logger.warning(
            f"Outline JSON parse failed: {e}",
            extra_fields={"event": "outline_parse_failed"},
        )
        raise ValueError("The model returned an unparseable outline; try again")
    return validate_outline_nodes(nodes)
