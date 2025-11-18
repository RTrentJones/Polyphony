# Polyphony - Setup Guide

This guide will help you get Polyphony up and running on your local machine.

## Prerequisites

Before you begin, ensure you have the following installed:

- **Docker** (24.x or higher) and **Docker Compose** (v2.x or higher)
- **Git**
- **Groq API Key** (free tier available at https://console.groq.com/)
- At least **8GB RAM** and **4 CPU cores** for local development
- **50GB disk space** for Docker images and volumes

## Quick Start

### 1. Clone the Repository

```bash
git clone <repository-url>
cd Polyphony
```

### 2. Configure Environment Variables

Copy the example environment file and update with your API keys:

```bash
cp .env.example .env
```

Edit `.env` and set your Groq API key:

```bash
GROQ_API_KEY=your-groq-api-key-here
```

**Important**: Also update the following in `.env`:
- `SECRET_KEY`: Generate a secure random key (min 32 characters)
- `POSTGRES_PASSWORD`: Set a secure database password

### 3. Start the Services

Start all services using Docker Compose:

```bash
docker-compose up -d
```

This will start:
- PostgreSQL database (port 5432)
- Qdrant vector database (port 6333)
- Redis cache (port 6379)
- API Gateway (port 8000)
- Orchestrator (port 8001)
- Character Agents: Hermione (8002), Harry (8003), Ron (8004)
- Document Parser (port 8005)
- Prometheus monitoring (port 9090)
- Grafana dashboards (port 3001)

### 4. Verify Services are Running

Check service health:

```bash
# Check all services
docker-compose ps

# Check API Gateway
curl http://localhost:8000/health

# Check Character Agent (Hermione)
curl http://localhost:8002/health

# Check Document Parser
curl http://localhost:8005/health
```

### 5. View Logs

```bash
# View all logs
docker-compose logs -f

# View specific service logs
docker-compose logs -f character-hermione
docker-compose logs -f api-gateway
```

## Project Structure

```
Polyphony/
├── services/
│   ├── shared/              # Shared models and utilities
│   │   ├── models.py        # Pydantic models
│   │   ├── config.py        # Configuration
│   │   └── database.py      # Database utilities
│   ├── api-gateway/         # Main API gateway
│   ├── orchestrator/        # Scene orchestration service
│   ├── character-agent/     # Character dialogue generation
│   │   ├── main.py          # FastAPI app
│   │   └── rag_system.py    # RAG implementation
│   └── document-parser/     # Document parsing service
│       ├── parser.py        # File parsing
│       └── character_extractor.py
├── frontend/                # Next.js frontend (TBD)
├── scripts/
│   └── init-db.sql         # Database schema
├── monitoring/
│   └── prometheus.yml      # Prometheus config
├── docker-compose.yml      # Local development setup
└── requirements.txt        # Python dependencies
```

## API Documentation

Once services are running, visit:

- **API Gateway**: http://localhost:8000/docs
- **Character Agent (Hermione)**: http://localhost:8002/docs
- **Document Parser**: http://localhost:8005/docs
- **Orchestrator**: http://localhost:8001/docs

## Testing the System

### 1. Parse a Document

```bash
# Create a test file
echo "This is a test manuscript with dialogue.
Hermione said, 'We need to study for the exam.'
Harry replied, 'But it's not until next week!'
Ron muttered, 'I haven't even started reading yet.'" > test.txt

# Upload and parse
curl -X POST http://localhost:8005/parse \
  -F "file=@test.txt" \
  -F "extract_characters=true"
```

### 2. Index Character Content

```bash
# Get character content from the file_id returned above
FILE_ID="<file-id-from-previous-response>"

curl -X POST "http://localhost:8005/extract-character-content?file_id=$FILE_ID&character_name=Hermione"

# Index the content into character RAG (use the chunks from above)
curl -X POST http://localhost:8002/index-content \
  -H "Content-Type: application/json" \
  -d '[{
    "text": "We need to study for the exam.",
    "chunk_type": "dialogue",
    "source_location": "test.txt",
    "character_name": "Hermione"
  }]'
```

### 3. Generate Character Dialogue

```bash
curl -X POST http://localhost:8002/generate-dialogue \
  -H "Content-Type: application/json" \
  -d '{
    "character_name": "Hermione",
    "scene_context": {
      "description": "Study session in library",
      "setting": "Hogwarts library"
    },
    "emotional_state": "focused",
    "other_characters": ["Harry", "Ron"],
    "beat_description": "Hermione suggests a study plan",
    "previous_dialogue": []
  }'
```

## Monitoring

### Prometheus

Visit http://localhost:9090 to view Prometheus metrics.

Key metrics to monitor:
- `dialogue_requests_total` - Total dialogue generation requests
- `dialogue_generation_duration_seconds` - Time to generate dialogue
- `rag_retrieval_duration_seconds` - Time to retrieve from RAG

### Grafana

Visit http://localhost:3001 to view Grafana dashboards.

Default credentials:
- Username: `admin`
- Password: `admin` (or set in `.env` as `GRAFANA_PASSWORD`)

## Development

### Running Services Locally (without Docker)

If you want to run services locally for development:

```bash
# Install dependencies
pip install -r requirements.txt

# Start individual services
cd services
export GROQ_API_KEY=your-key-here
export QDRANT_URL=http://localhost:6333
export REDIS_URL=redis://localhost:6379
export POSTGRES_HOST=localhost

# Run character agent
CHARACTER_NAME=Hermione CHARACTER_ID=hermione-001 SERVICE_PORT=8002 \
  uvicorn character-agent.main:app --reload

# Run document parser
SERVICE_PORT=8005 uvicorn document-parser.main:app --reload
```

### Adding a New Character Agent

To add a new character agent, add a new service in `docker-compose.yml`:

```yaml
character-<name>:
  build:
    context: .
    dockerfile: services/character-agent/Dockerfile
  container_name: polyphony-character-<name>
  ports:
    - "80XX:80XX"
  environment:
    SERVICE_NAME: character-agent
    SERVICE_PORT: 80XX
    CHARACTER_NAME: <Name>
    CHARACTER_ID: <name>-001
  # ... rest of config
```

## Troubleshooting

### Services won't start

```bash
# Check logs
docker-compose logs

# Rebuild images
docker-compose build --no-cache

# Remove old volumes (WARNING: deletes data)
docker-compose down -v
docker-compose up -d
```

### Connection errors

```bash
# Ensure all services are on the same network
docker network ls
docker network inspect polyphony_polyphony-network

# Check service connectivity
docker-compose exec api-gateway ping qdrant
```

### Out of memory

Increase Docker memory allocation:
- Docker Desktop → Settings → Resources → Memory (increase to 8GB+)

### Database initialization issues

```bash
# Manually initialize database
docker-compose exec postgres psql -U postgres -d polyphony -f /docker-entrypoint-initdb.d/init.sql
```

## Next Steps

1. **Implement Frontend**: Build Next.js interface (see `frontend/` directory)
2. **Complete Orchestrator**: Implement LangGraph-based scene orchestration
3. **Add Authentication**: Implement JWT authentication in API Gateway
4. **Upload Real Manuscripts**: Test with actual creative writing manuscripts
5. **Tune RAG**: Experiment with different embedding models and retrieval strategies

## Resources

- [Groq API Documentation](https://console.groq.com/docs)
- [Qdrant Documentation](https://qdrant.tech/documentation/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)

## Support

For issues and questions:
1. Check the logs: `docker-compose logs -f`
2. Review the API documentation: http://localhost:8000/docs
3. Check Prometheus metrics: http://localhost:9090

## License

[Add your license here]
