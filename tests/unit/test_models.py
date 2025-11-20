"""Unit tests for Pydantic models"""

import pytest
from uuid import uuid4
from pydantic import ValidationError

from services.shared.models import (
    UserCreate,
    ManuscriptCreate,
    SceneRequest,
    DialogueRequest,
    ChunkType,
)


@pytest.mark.unit
class TestUserModels:
    """Test User-related models"""

    def test_user_create_valid(self):
        """Test creating valid user"""
        user = UserCreate(
            email="test@example.com", password="securepassword", full_name="Test User"
        )

        assert user.email == "test@example.com"
        assert user.password == "securepassword"
        assert user.full_name == "Test User"

    def test_user_create_without_full_name(self):
        """Test creating user without full name"""
        user = UserCreate(email="test@example.com", password="securepassword")

        assert user.email == "test@example.com"
        assert user.full_name is None

    def test_user_create_invalid_email(self):
        """Test that invalid email raises error"""
        with pytest.raises(ValidationError):
            UserCreate(email="notanemail", password="securepassword")

    def test_user_create_missing_password(self):
        """Test that missing password raises error"""
        with pytest.raises(ValidationError):
            UserCreate(email="test@example.com")


@pytest.mark.unit
class TestManuscriptModels:
    """Test Manuscript-related models"""

    def test_manuscript_create_valid(self):
        """Test creating valid manuscript"""
        manuscript = ManuscriptCreate(title="Test Manuscript", author="Test Author")

        assert manuscript.title == "Test Manuscript"
        assert manuscript.author == "Test Author"

    def test_manuscript_create_without_author(self):
        """Test creating manuscript without author"""
        manuscript = ManuscriptCreate(title="Test Manuscript")

        assert manuscript.title == "Test Manuscript"
        assert manuscript.author is None


@pytest.mark.unit
class TestSceneModels:
    """Test Scene-related models"""

    def test_scene_request_valid(self):
        """Test creating valid scene request"""
        scene = SceneRequest(
            manuscript_id=uuid4(),
            characters=["Alice", "Bob"],
            scene_description="A tense confrontation",
            setting="Dark alley",
            emotional_tone="tense",
        )

        assert len(scene.characters) == 2
        assert scene.scene_description == "A tense confrontation"
        assert scene.setting == "Dark alley"
        assert scene.emotional_tone == "tense"

    def test_scene_request_default_word_count(self):
        """Test default target word count"""
        scene = SceneRequest(
            manuscript_id=uuid4(),
            characters=["Alice"],
            scene_description="Test scene",
            setting="Test setting",
            emotional_tone="neutral",
        )

        assert scene.target_word_count == 500

    def test_scene_request_custom_word_count(self):
        """Test custom target word count"""
        scene = SceneRequest(
            manuscript_id=uuid4(),
            characters=["Alice"],
            scene_description="Test scene",
            setting="Test setting",
            emotional_tone="neutral",
            target_word_count=1000,
        )

        assert scene.target_word_count == 1000

    def test_scene_request_word_count_validation(self):
        """Test word count min/max validation"""
        # Too low
        with pytest.raises(ValidationError):
            SceneRequest(
                manuscript_id=uuid4(),
                characters=["Alice"],
                scene_description="Test scene",
                setting="Test setting",
                emotional_tone="neutral",
                target_word_count=50,  # Below minimum of 100
            )

        # Too high
        with pytest.raises(ValidationError):
            SceneRequest(
                manuscript_id=uuid4(),
                characters=["Alice"],
                scene_description="Test scene",
                setting="Test setting",
                emotional_tone="neutral",
                target_word_count=5000,  # Above maximum of 3000
            )

    def test_scene_request_requires_characters(self):
        """Test that at least one character is required"""
        with pytest.raises(ValidationError):
            SceneRequest(
                manuscript_id=uuid4(),
                characters=[],  # Empty list
                scene_description="Test scene",
                setting="Test setting",
                emotional_tone="neutral",
            )

    def test_scene_request_short_description(self):
        """Test that scene description has minimum length"""
        with pytest.raises(ValidationError):
            SceneRequest(
                manuscript_id=uuid4(),
                characters=["Alice"],
                scene_description="Short",  # Less than 10 characters
                setting="Test setting",
                emotional_tone="neutral",
            )


@pytest.mark.unit
class TestDialogueModels:
    """Test Dialogue-related models"""

    def test_dialogue_request_valid(self):
        """Test creating valid dialogue request"""
        request = DialogueRequest(
            character_name="Alice",
            scene_context={"setting": "library"},
            emotional_state="curious",
            other_characters=["Bob"],
            beat_description="Alice asks a question",
        )

        assert request.character_name == "Alice"
        assert request.emotional_state == "curious"
        assert len(request.other_characters) == 1
        assert len(request.previous_dialogue) == 0

    def test_dialogue_request_with_previous_dialogue(self):
        """Test dialogue request with previous dialogue"""
        request = DialogueRequest(
            character_name="Alice",
            scene_context={"setting": "library"},
            emotional_state="curious",
            other_characters=["Bob"],
            beat_description="Alice responds",
            previous_dialogue=[{"character": "Bob", "dialogue": "Hello"}],
        )

        assert len(request.previous_dialogue) == 1
        assert request.previous_dialogue[0]["character"] == "Bob"


@pytest.mark.unit
class TestEnums:
    """Test enum types"""

    def test_chunk_type_enum(self):
        """Test ChunkType enum values"""
        assert ChunkType.DIALOGUE.value == "dialogue"
        assert ChunkType.ACTION.value == "action"
        assert ChunkType.THOUGHT.value == "thought"
        assert ChunkType.DESCRIPTION.value == "description"

    def test_chunk_type_enum_membership(self):
        """Test ChunkType enum membership"""
        assert "dialogue" in [ct.value for ct in ChunkType]
        assert "invalid" not in [ct.value for ct in ChunkType]
