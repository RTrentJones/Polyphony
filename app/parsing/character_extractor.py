"""Character extraction and content chunking for RAG"""

from typing import List, Dict, Optional
from uuid import UUID
import re
import json

from app.core.models import ChunkType
from app.core.logging_config import setup_logging
from app.llm.client import get_llm_client
from app.llm.json_utils import extract_json_array

logger = setup_logging("parsing.character_extractor")

# Real prose (Gutenberg, Standard Ebooks, Word, most published text) uses smart
# quotes. Dialogue detection matched only straight ASCII quotes, so it found
# ZERO dialogue in curly-quoted manuscripts — every character's spoken voice was
# invisible to indexing. Normalize smart→straight before matching.
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "″": '"'})
# Speech tags vary widely; the old set was just said/asked/replied.
_SPEECH_VERBS = (
    "said|asked|replied|responded|shouted|whispered|muttered|cried|exclaimed|"
    "answered|continued|added|declared|murmured|remarked|returned|observed|"
    "demanded|inquired|repeated|began|went on"
)


def _normalize_quotes(text: str) -> str:
    return text.translate(_SMART_QUOTES)


def stratified_sample(text: str, budget: int = 14000, windows: int = 4) -> str:
    """Evenly-spaced windows across the WHOLE manuscript, not just the head.

    Character identification off `text[:10000]` misses anyone introduced later
    (a POV/narrator who first appears in a late chapter is invisible). Sampling
    a few windows spread across the text surfaces the full cast while keeping
    the prompt within a fixed character budget.
    """
    if len(text) <= budget:
        return text
    win = max(1, budget // windows)
    # Anchor the first window at the start; distribute the rest across the span
    # so the final window ends at the text's tail.
    span = len(text) - win
    starts = [round(i * span / (windows - 1)) for i in range(windows)]
    parts, seen_end = [], 0
    for s in starts:
        s = max(s, seen_end)  # never overlap the previous window
        if s >= len(text):
            break
        parts.append(text[s : s + win])
        seen_end = s + win
    return "\n\n[...]\n\n".join(parts)


class CharacterExtractor:
    """Extract character-specific content from manuscript"""

    async def extract_characters(
        self,
        text: str,
        max_characters: int = 10,
        user_id: Optional[UUID] = None,
    ) -> List[str]:
        """
        Use LLM to identify main characters in text

        Args:
            text: Full manuscript text
            max_characters: Maximum number of characters to extract

        Returns:
            List of character names
        """
        # Sample across the whole manuscript so late-introduced characters
        # (and epistolary narrators) are seen, not just those in the opening.
        sample_text = stratified_sample(text)

        prompt = f"""Analyze this manuscript and identify the main characters.

Return ONLY a JSON array of character names (limit to top {max_characters} by importance).

Text (sampled across the whole manuscript, windows separated by [...]):
{sample_text}

Important:
- Include first-person narrators, diarists, and letter-writers — in epistolary
  or multi-POV works THEY are the main characters, even if rarely named in
  third person.
- Include anyone who speaks or drives action; exclude groups and figures
  mentioned only once in passing.
- Use each character's most common name (e.g., "Harry" not "Harry Potter").
- Return valid JSON only — no prose, no explanation.

JSON array:"""

        content = ""
        try:
            result = await get_llm_client().generate(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=1000,
                user_id=user_id,
                purpose="extract_characters",
            )
            content = result.text
            characters = extract_json_array(content)

            return [str(name) for name in characters[:max_characters]]

        except json.JSONDecodeError as e:
            logger.warning(
                f"Error parsing LLM response as JSON: {e}",
                extra_fields={"event": "character_extraction_parse_failed"},
            )
            return []
        except Exception as e:
            logger.warning(
                f"Error extracting characters: {e}",
                extra_fields={"event": "character_extraction_failed"},
            )
            return []

    def extract_character_content(
        self, text: str, character_name: str
    ) -> List[Dict[str, str]]:
        """
        Extract all content related to specific character

        Args:
            text: Full manuscript text
            character_name: Name of character to extract

        Returns:
            List of chunks with metadata:
            [{
                'text': str,
                'chunk_type': str,
                'source_location': str,
                'character_name': str
            }]
        """
        chunks = []

        # Split into paragraphs
        paragraphs = text.split("\n\n")

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue

            # Check if character appears in paragraph
            if self._character_in_paragraph(character_name, para):
                chunk_type = self._classify_chunk(para, character_name)

                chunks.append(
                    {
                        "text": para,
                        "chunk_type": chunk_type,
                        "source_location": f"paragraph_{i}",
                        "character_name": character_name,
                    }
                )

        # Also index the character's actual spoken lines as dialogue voice. The
        # name-in-paragraph gate above misses inverted attributions ("…," said X)
        # and, more importantly, a speaking character's most voice-bearing text is
        # their dialogue — which the paragraph classifier under-labels. Deduped
        # against paragraphs already captured.
        seen = {c["text"] for c in chunks}
        for j, line in enumerate(self.extract_dialogue_only(text, character_name)):
            if line not in seen:
                seen.add(line)
                chunks.append(
                    {
                        "text": line,
                        "chunk_type": ChunkType.DIALOGUE.value,
                        "source_location": f"dialogue_{j}",
                        "character_name": character_name,
                    }
                )

        return chunks

    def extract_dialogue_only(self, text: str, character_name: str) -> List[str]:
        """
        Extract only dialogue lines for this character

        Args:
            text: Full manuscript text
            character_name: Name of character

        Returns:
            List of dialogue strings
        """
        dialogues = []
        # Normalize smart quotes first so the straight-quote patterns fire on
        # real-world prose (see _SMART_QUOTES).
        text = _normalize_quotes(text)
        name = re.escape(character_name)

        # Common dialogue patterns (the closing quote may carry a comma, e.g.
        # "No," said Sarah — [^"]+ absorbs it before the closing ").
        patterns = [
            # "dialogue," said Character   (inversion — the common newspaper form)
            rf'"([^"]+)"\s+(?:{_SPEECH_VERBS})\s+{name}',
            # Character said, "dialogue"
            rf'{name}\s+(?:{_SPEECH_VERBS}),?\s+"([^"]+)"',
            # Character: "dialogue"
            rf'{name}:\s+"([^"]+)"',
            # Character <clause> "dialogue"
            rf'{name}\s+[^"]*"([^"]+)"',
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                dialogue = match.group(1).strip()
                if dialogue and len(dialogue) > 3:  # Filter out very short matches
                    dialogues.append(dialogue)

        # Remove duplicates while preserving order
        seen = set()
        unique_dialogues = []
        for dialogue in dialogues:
            if dialogue.lower() not in seen:
                seen.add(dialogue.lower())
                unique_dialogues.append(dialogue)

        return unique_dialogues

    def _character_in_paragraph(self, character_name: str, paragraph: str) -> bool:
        """
        Check if character name appears in paragraph

        Uses word boundary matching to avoid false positives
        """
        # Create pattern with word boundaries
        pattern = rf"\b{re.escape(character_name)}\b"
        return bool(re.search(pattern, paragraph, re.IGNORECASE))

    def _classify_chunk(self, text: str, character_name: str) -> str:
        """
        Classify chunk as dialogue, action, thought, or description

        Args:
            text: Paragraph text
            character_name: Character name

        Returns:
            ChunkType value
        """
        # Check for dialogue indicators (normalize smart quotes first).
        norm = _normalize_quotes(text)
        name = re.escape(character_name)
        if '"' in norm or "'" in norm:
            # Is it actually dialogue involving the character (either speaker order)?
            dialogue_patterns = [
                rf"{name}\s+(?:{_SPEECH_VERBS})",
                rf'"\s*(?:{_SPEECH_VERBS})\s+{name}',
                rf'"[^"]*,?"\s+(?:{_SPEECH_VERBS})\s+{name}',
                rf'{name}:\s*["\']',
            ]
            if any(re.search(p, norm, re.IGNORECASE) for p in dialogue_patterns):
                return ChunkType.DIALOGUE.value

        # Check for thought indicators
        thought_keywords = [
            "thought",
            "wondered",
            "pondered",
            "realized",
            "remembered",
            "considered",
            "imagined",
            "knew",
            "believed",
            "felt that",
        ]
        if any(keyword in text.lower() for keyword in thought_keywords):
            return ChunkType.THOUGHT.value

        # Check for action verbs
        action_verbs = [
            "walked",
            "ran",
            "jumped",
            "grabbed",
            "took",
            "held",
            "moved",
            "turned",
            "looked",
            "went",
            "came",
            "stood",
            "sat",
            "opened",
            "closed",
            "pulled",
            "pushed",
        ]
        if any(verb in text.lower() for verb in action_verbs):
            return ChunkType.ACTION.value

        # Default to description
        return ChunkType.DESCRIPTION.value

    def get_character_statistics(self, chunks: List[Dict[str, str]]) -> Dict:
        """
        Get statistics about extracted character content

        Args:
            chunks: List of character chunks

        Returns:
            Dictionary with statistics
        """
        if not chunks:
            return {
                "total_chunks": 0,
                "dialogue_count": 0,
                "action_count": 0,
                "thought_count": 0,
                "description_count": 0,
                "total_words": 0,
            }

        stats = {
            "total_chunks": len(chunks),
            "dialogue_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.DIALOGUE.value
            ),
            "action_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.ACTION.value
            ),
            "thought_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.THOUGHT.value
            ),
            "description_count": sum(
                1 for c in chunks if c["chunk_type"] == ChunkType.DESCRIPTION.value
            ),
            "total_words": sum(len(c["text"].split()) for c in chunks),
        }

        return stats
