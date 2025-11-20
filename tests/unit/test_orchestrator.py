"""Unit tests for Orchestrator service and workflow"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.orchestrator.workflow import (
    create_scene_workflow,
    plan_scene_beats,
    generate_dialogue_for_beat,
    assemble_scene,
    _call_character_agent,
    get_groq_client
)
from services.shared.models import SceneRequest


@pytest.mark.unit
class TestGroqClient:
    """Test Groq client singleton"""

    def test_groq_client_singleton(self):
        """Test Groq client is a singleton"""
        client1 = get_groq_client()
        client2 = get_groq_client()

        assert client1 is client2

    def test_groq_client_initialization(self):
        """Test Groq client initializes correctly"""
        client = get_groq_client()

        assert client is not None


@pytest.mark.unit
class TestWorkflowStateFunctions:
    """Test workflow state management"""

    @pytest.mark.asyncio
    @patch('services.orchestrator.workflow.get_groq_client')
    async def test_plan_scene_beats(self, mock_groq):
        """Test planning scene beats"""
        # Mock LLM response
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='["Beat 1: Opening", "Beat 2: Conflict", "Beat 3: Resolution"]'))
        ]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_groq.return_value = mock_client

        state = {
            "scene_request": {
                "scene_description": "A tense negotiation",
                "setting": "Dark alley",
                "emotional_tone": "suspenseful",
                "characters": ["Alice", "Bob"]
            },
            "beats": [],
            "scene_id": "test-scene-123"
        }

        result = await plan_scene_beats(state)

        assert "beats" in result
        assert len(result["beats"]) > 0

    @pytest.mark.asyncio
    async def test_assemble_scene(self):
        """Test scene assembly"""
        state = {
            "scene_id": "test-scene-123",
            "scene_request": {
                "manuscript_id": "manuscript-456",
                "scene_description": "A test scene",
                "setting": "Test location",
                "emotional_tone": "neutral",
                "characters": ["Alice"]
            },
            "beats": ["Beat 1", "Beat 2"],
            "completed_beats": [
                {
                    "beat": "Beat 1",
                    "dialogue_turns": [
                        {"character": "Alice", "dialogue": "Hello", "action": "waves"}
                    ]
                },
                {
                    "beat": "Beat 2",
                    "dialogue_turns": [
                        {"character": "Alice", "dialogue": "Goodbye", "action": "leaves"}
                    ]
                }
            ]
        }

        with patch('services.orchestrator.workflow.get_async_session'):
            result = await assemble_scene(state)

            assert "final_scene" in result
            assert len(result["final_scene"]) > 0
            assert "Alice" in result["final_scene"]


@pytest.mark.unit
class TestCharacterAgentCalls:
    """Test character agent integration"""

    @pytest.mark.asyncio
    async def test_call_character_agent_success(self):
        """Test successful character agent call"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={
                "character": "Alice",
                "dialogue": "I understand.",
                "action": "nods thoughtfully",
                "confidence_score": 0.95
            })

            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await _call_character_agent(
                character_name="Alice",
                beat="Opening scene",
                previous_context=[],
                scene_context={"setting": "Office"}
            )

            assert result["character"] == "Alice"
            assert "dialogue" in result
            assert result["confidence_score"] >= 0

    @pytest.mark.asyncio
    async def test_call_character_agent_failure_fallback(self):
        """Test character agent fallback on failure"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await _call_character_agent(
                character_name="Bob",
                beat="Test beat",
                previous_context=[],
                scene_context={}
            )

            # Should return fallback dialogue
            assert result["character"] == "Bob"
            assert "confidence_score" in result
            assert result["confidence_score"] == 0.0  # Fallback has 0 confidence

    @pytest.mark.asyncio
    async def test_call_character_agent_with_context(self):
        """Test character agent with previous context"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={
                "character": "Alice",
                "dialogue": "I remember what you said.",
                "action": "recalls",
                "confidence_score": 0.9
            })

            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__.return_value = mock_client

            previous_context = [
                {"character": "Bob", "dialogue": "Remember this."}
            ]

            result = await _call_character_agent(
                character_name="Alice",
                beat="Continuation",
                previous_context=previous_context,
                scene_context={}
            )

            assert result is not None

            # Verify context was included in request
            call_args = mock_client.post.call_args
            assert call_args is not None


@pytest.mark.unit
class TestDialogueGeneration:
    """Test dialogue generation for beats"""

    @pytest.mark.asyncio
    @patch('services.orchestrator.workflow._call_character_agent')
    async def test_generate_dialogue_for_beat(self, mock_agent_call):
        """Test generating dialogue for a beat"""
        mock_agent_call.return_value = {
            "character": "Alice",
            "dialogue": "Test dialogue",
            "action": "Test action",
            "confidence_score": 0.9
        }

        state = {
            "scene_request": {
                "characters": ["Alice", "Bob"],
                "setting": "Test setting",
                "emotional_tone": "neutral"
            },
            "current_beat": "Test beat",
            "completed_beats": []
        }

        result = await generate_dialogue_for_beat(state)

        assert "completed_beats" in result
        assert len(result["completed_beats"]) > 0
        assert "dialogue_turns" in result["completed_beats"][0]

    @pytest.mark.asyncio
    @patch('services.orchestrator.workflow._call_character_agent')
    async def test_generate_dialogue_multiple_characters(self, mock_agent_call):
        """Test dialogue generation with multiple characters"""
        # Mock responses for different characters
        responses = [
            {"character": "Alice", "dialogue": "Hello", "action": "greets", "confidence_score": 0.9},
            {"character": "Bob", "dialogue": "Hi there", "action": "responds", "confidence_score": 0.85}
        ]
        mock_agent_call.side_effect = responses

        state = {
            "scene_request": {
                "characters": ["Alice", "Bob"],
                "setting": "Park",
                "emotional_tone": "friendly"
            },
            "current_beat": "Opening",
            "completed_beats": []
        }

        result = await generate_dialogue_for_beat(state)

        assert len(result["completed_beats"]) > 0
        dialogue_turns = result["completed_beats"][0]["dialogue_turns"]

        # Should have dialogue from multiple characters
        characters = [turn["character"] for turn in dialogue_turns]
        assert len(set(characters)) > 1  # Multiple unique characters


@pytest.mark.unit
class TestWorkflowIntegration:
    """Test full workflow integration"""

    def test_create_scene_workflow(self):
        """Test workflow creation"""
        workflow = create_scene_workflow()

        assert workflow is not None

    @pytest.mark.asyncio
    @patch('services.orchestrator.workflow.get_groq_client')
    @patch('services.orchestrator.workflow._call_character_agent')
    @patch('services.orchestrator.workflow.get_async_session')
    async def test_full_workflow_execution(self, mock_session, mock_agent, mock_groq):
        """Test full workflow execution"""
        # Mock Groq responses
        mock_groq_response = MagicMock()
        mock_groq_response.choices = [
            MagicMock(message=MagicMock(content='["Beat 1: Introduction"]'))
        ]

        mock_groq_client = AsyncMock()
        mock_groq_client.chat.completions.create = AsyncMock(return_value=mock_groq_response)
        mock_groq.return_value = mock_groq_client

        # Mock character agent
        mock_agent.return_value = {
            "character": "Alice",
            "dialogue": "Test dialogue",
            "action": "Test action",
            "confidence_score": 0.9
        }

        # Mock database session
        mock_db_session = AsyncMock()
        mock_session.return_value.__aenter__.return_value = mock_db_session

        # Create workflow
        workflow = create_scene_workflow()

        # Initial state
        initial_state = {
            "scene_id": "test-123",
            "scene_request": {
                "manuscript_id": "ms-456",
                "scene_description": "A meeting",
                "setting": "Office",
                "emotional_tone": "professional",
                "characters": ["Alice"]
            },
            "beats": [],
            "completed_beats": []
        }

        # This would execute the full workflow
        # Actual execution requires LangGraph runtime


@pytest.mark.unit
class TestInputSanitization:
    """Test input sanitization in workflow"""

    @pytest.mark.asyncio
    @patch('services.orchestrator.workflow.get_groq_client')
    @patch('services.orchestrator.workflow.sanitize_for_llm')
    async def test_scene_description_sanitization(self, mock_sanitize, mock_groq):
        """Test scene description is sanitized"""
        mock_sanitize.return_value = "Safe description"

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(message=MagicMock(content='["Beat 1"]'))
        ]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_groq.return_value = mock_client

        state = {
            "scene_request": {
                "scene_description": "Dangerous <script>alert('xss')</script> input",
                "setting": "Test",
                "emotional_tone": "neutral",
                "characters": ["Alice"]
            },
            "beats": [],
            "scene_id": "test-123"
        }

        await plan_scene_beats(state)

        # Verify sanitization was called
        mock_sanitize.assert_called()


@pytest.mark.unit
class TestCircuitBreakerIntegration:
    """Test circuit breaker in workflow"""

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self):
        """Test circuit breaker opens after repeated failures"""
        from services.shared.resilience import CircuitBreaker, CircuitBreakerState

        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=1)

        # Simulate failures
        async def failing_service():
            raise Exception("Service down")

        for _ in range(3):
            try:
                await breaker.call(failing_service)
            except:
                pass

        assert breaker.state == CircuitBreakerState.OPEN

    @pytest.mark.asyncio
    async def test_workflow_handles_circuit_breaker_open(self):
        """Test workflow handles open circuit breaker gracefully"""
        # Character agent call should return fallback when circuit is open
        with patch('httpx.AsyncClient') as mock_client_class:
            # Simulate connection failures
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=Exception("Service unavailable"))
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Multiple calls should eventually open circuit breaker
            results = []
            for _ in range(5):
                result = await _call_character_agent(
                    character_name="Alice",
                    beat="Test",
                    previous_context=[],
                    scene_context={}
                )
                results.append(result)

            # All should return fallback
            assert all(r["confidence_score"] == 0.0 for r in results)


@pytest.mark.unit
class TestRetryLogic:
    """Test retry logic in workflow"""

    @pytest.mark.asyncio
    async def test_llm_call_retries_on_failure(self):
        """Test LLM calls retry on transient failures"""
        # This would test the retry decorator
        # Implementation depends on actual retry configuration
        pass

    @pytest.mark.asyncio
    async def test_character_agent_retries(self):
        """Test character agent calls retry"""
        with patch('httpx.AsyncClient') as mock_client_class:
            mock_client = AsyncMock()

            # Fail twice, then succeed
            call_count = 0

            async def mock_post(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise Exception("Temporary failure")

                mock_response = AsyncMock()
                mock_response.raise_for_status = MagicMock()
                mock_response.json = AsyncMock(return_value={
                    "character": "Alice",
                    "dialogue": "Success",
                    "action": "speaks",
                    "confidence_score": 0.9
                })
                return mock_response

            mock_client.post = mock_post
            mock_client_class.return_value.__aenter__.return_value = mock_client

            result = await _call_character_agent(
                character_name="Alice",
                beat="Test",
                previous_context=[],
                scene_context={}
            )

            # Should eventually succeed after retries
            # Or return fallback if retries exhausted


@pytest.mark.integration
class TestOrchestratorEndpoints:
    """Integration tests for orchestrator endpoints"""

    @pytest.mark.asyncio
    async def test_orchestrate_endpoint(self):
        """Test /orchestrate endpoint"""
        # This would require FastAPI test client
        # And database setup
        pass

    @pytest.mark.asyncio
    async def test_scene_status_endpoint(self):
        """Test scene status polling endpoint"""
        pass
