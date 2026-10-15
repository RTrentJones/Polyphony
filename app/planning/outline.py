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


_PEOPLE_KEYS = ("people", "character_ids", "characters", "secondary_names")


def people_refs(node: dict) -> list:
    """A node's person references: `people` + `character_ids` + `characters`
    + `secondary_names`, in that order, all optional (§1e). The legacy keys
    stay for stored outlines, restores, author PUTs and old archives."""
    refs: list = []
    for key in _PEOPLE_KEYS:
        if isinstance(node.get(key), list):
            refs.extend(node[key])
    return refs


def _people_fields(
    node: dict,
    cast: Optional[CastIndex],
    strict: bool,
    known_text: Optional[str],
    top: bool,
) -> dict:
    """A node's `pov` (top level only), `character_ids` and `secondary_names`
    from ONE classify_people call over people_refs(node) (SPEC-24 §24b). Without
    a cast (a restore, which must not depend on today's cast) stored ids are
    kept as they are and names stay secondary. An invented ref is dropped
    (generation; the fidelity audit warns) or, with `strict`, rejected. `pov`
    must resolve to the cast; it is never a secondary."""
    people = classify_people(people_refs(node), cast, None if strict else known_text)
    ids, unknown = people.ids, list(people.invented)
    if cast is None:
        ids = list(dict.fromkeys(_uuid_str(r) for r in people.invented))
        unknown = []
    out: dict = {}
    pov = node.get("pov") if top else None
    if isinstance(pov, str) and pov.strip():
        pov = pov.strip()[:255]
        pid = cast.resolve(pov) if cast is not None else None
        if cast is not None and pid is None:
            unknown.append(pov)
        elif pid is not None:
            # The POV character is in the chapter by definition, and first.
            ids = [pid, *(i for i in ids if i != pid)]
            out["pov"] = cast.names[pid]
        else:
            out["pov"] = pov
    if ids or any(isinstance(node.get(k), list) for k in _PEOPLE_KEYS):
        out["character_ids"] = ids
    if people.secondary or any(
        isinstance(node.get(k), list) for k in ("people", "secondary_names")
    ):
        out["secondary_names"] = people.secondary
    if strict and unknown:
        raise ValueError(
            "Not in this book's cast: " + ", ".join(dict.fromkeys(unknown))
        )
    return out


def validate_outline_nodes(
    nodes,
    *,
    cast: Optional[Iterable[Any]] = None,
    strict: bool = False,
    known_text: Optional[str] = None,
) -> list[dict]:
    """Normalize/validate outline JSON into [{node_id, title, summary, children,
    pov?, character_ids?, secondary_names?, act_index?, threads?}] (children:
    character_ids?, secondary_names?, setting?, tone?).

    Every node gets a stable `node_id`, kept when it already has a valid one, so
    edits, versions and restores preserve the link a promoted chapter holds.
    Each node's people (§1e: `people`, or the legacy `character_ids` /
    `characters` / `secondary_names`) go through one classify_people call:

    - generation (`cast`, `known_text` = names.norm(canon_known_text(canon))):
      cast ids, canon-named secondaries, inventions dropped (the audit warns);
    - the author's own edit (`strict`): every unresolved name is a secondary,
      and only a dead or foreign id (or an unknown `pov`) is rejected;
    - a restore (no `cast`): stored values are kept as they are.
    """
    index = cast if cast is None or isinstance(cast, CastIndex) else CastIndex(cast)
    return _validate(nodes, index, strict, known_text, top=True)


def _validate(
    nodes, cast: Optional[CastIndex], strict: bool, known_text, top: bool
) -> list:
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
            "children": _validate(
                node.get("children", []) or [], cast, strict, known_text, False
            ),
        }
        out.update(_people_fields(node, cast, strict, known_text, top))
        if top:
            act = node.get("act_index")
            if isinstance(act, int) and not isinstance(act, bool) and act >= 0:
                out["act_index"] = act
            threads = node.get("threads")
            if isinstance(threads, list):
                out["threads"] = [
                    str(t).strip()[:255] for t in threads if str(t).strip()
                ]
        else:
            for key, cap in (("setting", 500), ("tone", 200)):
                if isinstance(node.get(key), str):
                    out[key] = node[key].strip()[:cap]
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
