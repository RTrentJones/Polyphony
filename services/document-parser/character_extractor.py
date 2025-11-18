"""Character extraction and content chunking for RAG"""

from typing import List, Dict
import re
import json
from groq import AsyncGroq

from services.shared.models import ChunkType


class CharacterExtractor:
    """Extract character-specific content from manuscript"""

    def __init__(self, groq_api_key: str):
        """
        Initialize extractor with LLM client

        Args:
            groq_api_key: Groq API key for LLM calls
        """
        self.llm = AsyncGroq(api_key=groq_api_key)

    async def extract_characters(self, text: str, max_characters: int = 10) -> List[str]:
        """
        Use LLM to identify main characters in text

        Args:
            text: Full manuscript text
            max_characters: Maximum number of characters to extract

        Returns:
            List of character names
        """
        # Use first 10k characters for character identification
        sample_text = text[:10000]

        prompt = f"""Analyze this manuscript excerpt and identify the main speaking characters.

Return ONLY a JSON array of character names (limit to top {max_characters} by importance).
Do not include narrators, minor characters mentioned only in passing, or groups.

Text excerpt:
{sample_text}

Important:
- Only include characters who speak or take actions in the story
- Use their most common name (e.g., "Harry" not "Harry Potter")
- Return valid JSON format
- No additional text or explanations

JSON array:"""

        try:
            response = await self.llm.chat.completions.create(
                model="llama-3.1-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )

            content = response.choices[0].message.content.strip()

            # Extract JSON array from response
            # Sometimes LLM wraps it in markdown code blocks
            content = content.replace('```json', '').replace('```', '').strip()

            characters = json.loads(content)

            if not isinstance(characters, list):
                raise ValueError("Response is not a list")

            return [str(name) for name in characters[:max_characters]]

        except json.JSONDecodeError as e:
            print(f"Error parsing LLM response as JSON: {e}")
            print(f"Response was: {content}")
            return []
        except Exception as e:
            print(f"Error extracting characters: {e}")
            return []

    def extract_character_content(
        self,
        text: str,
        character_name: str
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
        paragraphs = text.split('\n\n')

        for i, para in enumerate(paragraphs):
            para = para.strip()
            if not para:
                continue

            # Check if character appears in paragraph
            if self._character_in_paragraph(character_name, para):
                chunk_type = self._classify_chunk(para, character_name)

                chunks.append({
                    'text': para,
                    'chunk_type': chunk_type,
                    'source_location': f'paragraph_{i}',
                    'character_name': character_name
                })

        return chunks

    def extract_dialogue_only(
        self,
        text: str,
        character_name: str
    ) -> List[str]:
        """
        Extract only dialogue lines for this character

        Args:
            text: Full manuscript text
            character_name: Name of character

        Returns:
            List of dialogue strings
        """
        dialogues = []

        # Common dialogue patterns
        patterns = [
            # Pattern: "dialogue" said Character
            rf'"([^"]+)"\s+(?:said|asked|replied|responded|shouted|whispered|muttered)\s+{character_name}',
            # Pattern: Character said, "dialogue"
            rf'{character_name}\s+(?:said|asked|replied|responded|shouted|whispered|muttered),?\s+"([^"]+)"',
            # Pattern: Character: "dialogue"
            rf'{character_name}:\s+"([^"]+)"',
            # Pattern: Character spoke first, then "dialogue"
            rf'{character_name}\s+[^"]*"([^"]+)"',
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
        pattern = rf'\b{re.escape(character_name)}\b'
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
        # Check for dialogue indicators
        if '"' in text or "'" in text or '"' in text:
            # Check if it's actually dialogue involving the character
            dialogue_patterns = [
                rf'{character_name}\s+(?:said|asked|replied)',
                rf'"\s+(?:said|asked|replied)\s+{character_name}',
                rf'{character_name}:\s*["\']'
            ]
            if any(re.search(p, text, re.IGNORECASE) for p in dialogue_patterns):
                return ChunkType.DIALOGUE.value

        # Check for thought indicators
        thought_keywords = [
            'thought', 'wondered', 'pondered', 'realized', 'remembered',
            'considered', 'imagined', 'knew', 'believed', 'felt that'
        ]
        if any(keyword in text.lower() for keyword in thought_keywords):
            return ChunkType.THOUGHT.value

        # Check for action verbs
        action_verbs = [
            'walked', 'ran', 'jumped', 'grabbed', 'took', 'held',
            'moved', 'turned', 'looked', 'went', 'came', 'stood',
            'sat', 'opened', 'closed', 'pulled', 'pushed'
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
                'total_chunks': 0,
                'dialogue_count': 0,
                'action_count': 0,
                'thought_count': 0,
                'description_count': 0,
                'total_words': 0
            }

        stats = {
            'total_chunks': len(chunks),
            'dialogue_count': sum(1 for c in chunks if c['chunk_type'] == ChunkType.DIALOGUE.value),
            'action_count': sum(1 for c in chunks if c['chunk_type'] == ChunkType.ACTION.value),
            'thought_count': sum(1 for c in chunks if c['chunk_type'] == ChunkType.THOUGHT.value),
            'description_count': sum(1 for c in chunks if c['chunk_type'] == ChunkType.DESCRIPTION.value),
            'total_words': sum(len(c['text'].split()) for c in chunks)
        }

        return stats
