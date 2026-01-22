"""Comprehensive unit tests for ORM models"""

import pytest
from sqlalchemy import inspect

from services.shared.orm_models import (
    User,
    Manuscript,
    Character,
    CharacterChunk,
    Scene,
    SceneBeat,
    APIUsage,
)
from services.shared.database import Base


@pytest.mark.unit
class TestUserModel:
    """Test User ORM model"""

    def test_user_model_exists(self):
        """Test that User model is defined"""
        assert User is not None
        assert User.__tablename__ == "users"

    def test_user_model_columns(self):
        """Test User model has expected columns"""
        mapper = inspect(User)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "email",
            "hashed_password",
            "full_name",
            "created_at",
            "updated_at",
        }
        assert expected_columns.issubset(columns)

    def test_user_model_relationships(self):
        """Test User model relationships"""
        mapper = inspect(User)
        relationships = {r.key for r in mapper.relationships}

        assert "manuscripts" in relationships
        assert "scenes" in relationships
        assert "api_usage" in relationships

    def test_user_email_unique(self):
        """Test email column is unique"""
        mapper = inspect(User)
        email_column = mapper.columns["email"]

        assert email_column.unique is True
        assert email_column.index is True
        assert email_column.nullable is False

    def test_user_hashed_password_required(self):
        """Test hashed_password is required"""
        mapper = inspect(User)
        password_column = mapper.columns["hashed_password"]

        assert password_column.nullable is False


@pytest.mark.unit
class TestManuscriptModel:
    """Test Manuscript ORM model"""

    def test_manuscript_model_exists(self):
        """Test that Manuscript model is defined"""
        assert Manuscript is not None
        assert Manuscript.__tablename__ == "manuscripts"

    def test_manuscript_model_columns(self):
        """Test Manuscript model has expected columns"""
        mapper = inspect(Manuscript)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "user_id",
            "title",
            "author",
            "content_hash",
            "file_path",
            "word_count",
            "uploaded_at",
            "processed_at",
            "status",
        }
        assert expected_columns.issubset(columns)

    def test_manuscript_model_relationships(self):
        """Test Manuscript model relationships"""
        mapper = inspect(Manuscript)
        relationships = {r.key for r in mapper.relationships}

        assert "user" in relationships
        assert "characters" in relationships
        assert "scenes" in relationships

    def test_manuscript_user_foreign_key(self):
        """Test user_id is foreign key"""
        mapper = inspect(Manuscript)
        user_id_column = mapper.columns["user_id"]

        assert user_id_column.nullable is False
        assert len(user_id_column.foreign_keys) == 1

    def test_manuscript_status_default(self):
        """Test status has default value"""
        mapper = inspect(Manuscript)
        status_column = mapper.columns["status"]

        assert status_column.default is not None

    def test_manuscript_content_hash_unique(self):
        """Test content_hash is unique"""
        mapper = inspect(Manuscript)
        hash_column = mapper.columns["content_hash"]

        assert hash_column.unique is True


@pytest.mark.unit
class TestCharacterModel:
    """Test Character ORM model"""

    def test_character_model_exists(self):
        """Test that Character model is defined"""
        assert Character is not None
        assert Character.__tablename__ == "characters"

    def test_character_model_columns(self):
        """Test Character model has expected columns"""
        mapper = inspect(Character)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "manuscript_id",
            "name",
            "description",
            "personality_traits",
            "voice_characteristics",
            "dialogue_count",
            "indexed_at",
            "qdrant_collection_name",
        }
        assert expected_columns.issubset(columns)

    def test_character_model_relationships(self):
        """Test Character model relationships"""
        mapper = inspect(Character)
        relationships = {r.key for r in mapper.relationships}

        assert "manuscript" in relationships
        assert "chunks" in relationships

    def test_character_name_required(self):
        """Test name is required"""
        mapper = inspect(Character)
        name_column = mapper.columns["name"]

        assert name_column.nullable is False

    def test_character_dialogue_count_default(self):
        """Test dialogue_count has default"""
        mapper = inspect(Character)
        count_column = mapper.columns["dialogue_count"]

        assert count_column.default is not None

    def test_character_json_columns(self):
        """Test JSON columns for traits and characteristics"""
        mapper = inspect(Character)

        traits_col = mapper.columns["personality_traits"]
        voice_col = mapper.columns["voice_characteristics"]

        assert str(traits_col.type) == "JSON"
        assert str(voice_col.type) == "JSON"


@pytest.mark.unit
class TestCharacterChunkModel:
    """Test CharacterChunk ORM model"""

    def test_character_chunk_model_exists(self):
        """Test that CharacterChunk model is defined"""
        assert CharacterChunk is not None
        assert CharacterChunk.__tablename__ == "character_chunks"

    def test_character_chunk_model_columns(self):
        """Test CharacterChunk model has expected columns"""
        mapper = inspect(CharacterChunk)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "character_id",
            "chunk_type",
            "content",
            "source_location",
            "embedding_id",
            "created_at",
        }
        assert expected_columns.issubset(columns)

    def test_character_chunk_content_required(self):
        """Test content is required"""
        mapper = inspect(CharacterChunk)
        content_column = mapper.columns["content"]

        assert content_column.nullable is False

    def test_character_chunk_relationships(self):
        """Test CharacterChunk relationships"""
        mapper = inspect(CharacterChunk)
        relationships = {r.key for r in mapper.relationships}

        assert "character" in relationships


@pytest.mark.unit
class TestSceneModel:
    """Test Scene ORM model"""

    def test_scene_model_exists(self):
        """Test that Scene model is defined"""
        assert Scene is not None
        assert Scene.__tablename__ == "scenes"

    def test_scene_model_columns(self):
        """Test Scene model has expected columns"""
        mapper = inspect(Scene)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "user_id",
            "manuscript_id",
            "title",
            "setting",
            "emotional_tone",
            "characters",
            "scene_description",
            "scene_request",
            "generated_content",
            "word_count",
            "status",
            "evaluation_scores",
            "created_at",
        }
        assert expected_columns.issubset(columns)

    def test_scene_model_relationships(self):
        """Test Scene model relationships"""
        mapper = inspect(Scene)
        relationships = {r.key for r in mapper.relationships}

        assert "user" in relationships
        assert "manuscript" in relationships
        assert "beats" in relationships

    def test_scene_status_default(self):
        """Test status default value"""
        mapper = inspect(Scene)
        status_column = mapper.columns["status"]

        assert status_column.default is not None

    def test_scene_characters_array(self):
        """Test characters is an array column"""
        mapper = inspect(Scene)
        chars_column = mapper.columns["characters"]

        assert "ARRAY" in str(chars_column.type)


@pytest.mark.unit
class TestSceneBeatModel:
    """Test SceneBeat ORM model"""

    def test_scene_beat_model_exists(self):
        """Test that SceneBeat model is defined"""
        assert SceneBeat is not None
        assert SceneBeat.__tablename__ == "scene_beats"

    def test_scene_beat_model_columns(self):
        """Test SceneBeat model has expected columns"""
        mapper = inspect(SceneBeat)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "scene_id",
            "beat_number",
            "description",
            "dialogue",
            "content",
            "created_at",
        }
        assert expected_columns.issubset(columns)

    def test_scene_beat_relationships(self):
        """Test SceneBeat relationships"""
        mapper = inspect(SceneBeat)
        relationships = {r.key for r in mapper.relationships}

        assert "scene" in relationships

    def test_scene_beat_scene_id_required(self):
        """Test scene_id is required"""
        mapper = inspect(SceneBeat)
        scene_id_column = mapper.columns["scene_id"]

        assert scene_id_column.nullable is False

    def test_scene_beat_number_required(self):
        """Test beat_number is required"""
        mapper = inspect(SceneBeat)
        beat_num_column = mapper.columns["beat_number"]

        assert beat_num_column.nullable is False


@pytest.mark.unit
class TestAPIUsageModel:
    """Test APIUsage ORM model"""

    def test_api_usage_model_exists(self):
        """Test that APIUsage model is defined"""
        assert APIUsage is not None
        assert APIUsage.__tablename__ == "api_usage"

    def test_api_usage_model_columns(self):
        """Test APIUsage model has expected columns"""
        mapper = inspect(APIUsage)
        columns = {c.key for c in mapper.columns}

        expected_columns = {
            "id",
            "user_id",
            "endpoint",
            "tokens_used",
            "cost_usd",
            "timestamp",
        }
        assert expected_columns.issubset(columns)

    def test_api_usage_relationships(self):
        """Test APIUsage relationships"""
        mapper = inspect(APIUsage)
        relationships = {r.key for r in mapper.relationships}

        assert "user" in relationships

    def test_api_usage_cost_decimal(self):
        """Test cost_usd is decimal type"""
        mapper = inspect(APIUsage)
        cost_column = mapper.columns["cost_usd"]

        assert "DECIMAL" in str(cost_column.type).upper()


@pytest.mark.unit
class TestModelIndexes:
    """Test model indexes are properly defined"""

    def test_user_email_index(self):
        """Test User email has index"""
        mapper = inspect(User)
        email_column = mapper.columns["email"]
        assert email_column.index is True

    def test_manuscript_indexes(self):
        """Test Manuscript table indexes"""
        # Check table args for indexes
        table_args = getattr(Manuscript, "__table_args__", ())
        index_names = [idx.name for idx in table_args if hasattr(idx, "name")]

        assert any("user_id" in name for name in index_names if name)
        assert any("status" in name for name in index_names if name)

    def test_character_indexes(self):
        """Test Character table indexes"""
        table_args = getattr(Character, "__table_args__", ())
        index_names = [idx.name for idx in table_args if hasattr(idx, "name")]

        assert any("manuscript_id" in name for name in index_names if name)

    def test_scene_indexes(self):
        """Test Scene table indexes"""
        table_args = getattr(Scene, "__table_args__", ())
        index_names = [idx.name for idx in table_args if hasattr(idx, "name")]

        assert any("user_id" in name for name in index_names if name)
        assert any("status" in name for name in index_names if name)


@pytest.mark.unit
class TestCascadeDeletes:
    """Test cascade delete relationships"""

    def test_user_cascades_to_manuscripts(self):
        """Test User deletion cascades to manuscripts"""
        mapper = inspect(User)
        manuscripts_rel = None
        for rel in mapper.relationships:
            if rel.key == "manuscripts":
                manuscripts_rel = rel
                break

        assert manuscripts_rel is not None
        assert "delete-orphan" in str(manuscripts_rel.cascade)

    def test_manuscript_cascades_to_characters(self):
        """Test Manuscript deletion cascades to characters"""
        mapper = inspect(Manuscript)
        characters_rel = None
        for rel in mapper.relationships:
            if rel.key == "characters":
                characters_rel = rel
                break

        assert characters_rel is not None
        assert "delete-orphan" in str(characters_rel.cascade)

    def test_character_cascades_to_chunks(self):
        """Test Character deletion cascades to chunks"""
        mapper = inspect(Character)
        chunks_rel = None
        for rel in mapper.relationships:
            if rel.key == "chunks":
                chunks_rel = rel
                break

        assert chunks_rel is not None
        assert "delete-orphan" in str(chunks_rel.cascade)


@pytest.mark.unit
class TestBaseModel:
    """Test Base model configuration"""

    def test_base_declarative_base(self):
        """Test Base is a declarative base"""
        assert Base is not None
        assert hasattr(Base, "metadata")

    def test_all_models_inherit_base(self):
        """Test all models inherit from Base"""
        models = [
            User,
            Manuscript,
            Character,
            CharacterChunk,
            Scene,
            SceneBeat,
            APIUsage,
        ]

        for model in models:
            assert issubclass(model, Base)


@pytest.mark.unit
@pytest.mark.database
class TestORMModelCreation:
    """Test ORM model instance creation (requires database fixture)"""

    @pytest.mark.asyncio
    async def test_create_user_instance(self, async_session):
        """Test creating a User instance"""
        user = User(
            email="test@example.com",
            hashed_password="hashedpwd",
            full_name="Test User",
        )

        async_session.add(user)
        await async_session.commit()
        await async_session.refresh(user)

        assert user.id is not None
        assert user.email == "test@example.com"
        assert user.created_at is not None

    @pytest.mark.asyncio
    async def test_create_manuscript_instance(self, async_session, test_user):
        """Test creating a Manuscript instance"""
        manuscript = Manuscript(
            user_id=test_user.id,
            title="Test Manuscript",
            author="Test Author",
            word_count=1000,
            status="pending",
        )

        async_session.add(manuscript)
        await async_session.commit()
        await async_session.refresh(manuscript)

        assert manuscript.id is not None
        assert manuscript.user_id == test_user.id
        assert manuscript.status == "pending"

    @pytest.mark.asyncio
    async def test_create_character_instance(self, async_session, test_manuscript):
        """Test creating a Character instance"""
        character = Character(
            manuscript_id=test_manuscript.id,
            name="Test Character",
            description="A test character",
            personality_traits={"trait": "curious"},
            voice_characteristics={"style": "formal"},
            dialogue_count=5,
        )

        async_session.add(character)
        await async_session.commit()
        await async_session.refresh(character)

        assert character.id is not None
        assert character.name == "Test Character"
        assert character.personality_traits == {"trait": "curious"}

    @pytest.mark.asyncio
    async def test_create_scene_instance(
        self, async_session, test_user, test_manuscript
    ):
        """Test creating a Scene instance"""
        scene = Scene(
            user_id=test_user.id,
            manuscript_id=test_manuscript.id,
            title="Test Scene",
            setting="A library",
            emotional_tone="mysterious",
            characters=["Alice", "Bob"],
            scene_description="A tense encounter",
            status="processing",
        )

        async_session.add(scene)
        await async_session.commit()
        await async_session.refresh(scene)

        assert scene.id is not None
        assert scene.characters == ["Alice", "Bob"]
        assert scene.status == "processing"

    @pytest.mark.asyncio
    async def test_relationship_navigation(
        self, async_session, test_user, test_manuscript, test_character
    ):
        """Test navigating relationships"""
        # Navigate from user to manuscripts
        assert test_manuscript.user_id == test_user.id

        # Navigate from character to manuscript
        assert test_character.manuscript_id == test_manuscript.id
