# Vex — Headless Skill Hub for Agentic Memory

> A user-less, GitOps-first skills platform that transforms codebases and documents into immutable, versioned cognitive skills. Built with AST segmentation, cryptographic delta updates, and native MCP compatibility.

## Architecture

Vex uses a **Pointer Architecture** — lightweight vector pointers in Qdrant reference heavy-text records in SQLite. This separates the mathematical search layer from the storage layer, enabling:

- **AST-aware chunking** via Tree-sitter (Python, TypeScript, JavaScript, Go, YAML, Markdown)
- **Cryptographic delta caching** — SHA-256 hashes skip re-vectorization of unchanged code
- **GraphRAG dependency enrichment** — function call graphs are extracted and prepended to chunks
- **Multi-tenant governance** — all queries are scoped by `tenant_id` and `skill_id`
- **Time-travel versioning** — compare how code evolved across commits

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  FastAPI     │────▶│  Tree-sitter │────▶│   Ollama    │
│  REST API    │     │  AST Chunker │     │  Embeddings │
└──────┬──────┘     └──────────────┘     └──────┬──────┘
       │                                         │
       ▼                                         ▼
┌─────────────┐                          ┌─────────────┐
│   SQLite    │◀─── Pointer Architecture ──▶│   Qdrant   │
│ (Heavy Text)│                          │  (Vectors)   │
└─────────────┘                          └─────────────┘
       ▲
       │
┌──────┴──────┐
│  MCP Server │  ← AI agents connect via stdio
│  (stdio)    │
└─────────────┘
```

## Quick Start

### Prerequisites

- Python 3.13+
- [Ollama](https://ollama.ai) running locally with `nomic-embed-text` model
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

### Setup

```bash
# Clone and install
git clone https://github.com/your-org/vex.git
cd vex

# Install dependencies with uv
uv sync

# Pull the embedding model
ollama pull nomic-embed-text

# Start the API server
uv run uvicorn src.api.server:app --reload
```

The API will be available at `http://localhost:8000`.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `VEX_EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding model name |
| `VEX_VECTOR_DIMENSION` | `768` | Vector dimension (must match model) |
| `VEX_COLLECTION_NAME` | `vex_skills` | Qdrant collection name |
| `VEX_SCORE_THRESHOLD` | `0.60` | Minimum similarity score for search results |
| `VEX_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `VEX_REQUIRE_AUTH` | `False` | Enable API key authentication |
| `VEX_API_KEY` | — | API key (required when auth is enabled) |
| `VEX_GITHUB_WEBHOOK_SECRET` | — | HMAC secret for GitHub webhook verification |
| `VEX_GITHUB_ALLOWED_REFS` | `refs/heads/main,refs/heads/master` | Comma-separated allowed branches |
| `VEX_CORS_ORIGINS` | `*` | Comma-separated CORS allowed origins |
| `VEX_DATA_DIR` | `.vex_data` | SQLite database directory |
| `VEX_QDRANT_PATH` | `.qdrant_data` | Qdrant local storage directory |
| `VEX_TEMP_DIR` | `temp_uploads` | Temporary file upload directory |

## API Reference

### Core Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | No | System status |
| `GET` | `/health` | No | Composite health check (Qdrant + SQLite) |
| `POST` | `/skills/create` | Yes | Register a new skill |
| `GET` | `/skills/{skill_id}` | Yes | Get skill metadata |
| `POST` | `/documents/upload` | Yes | Upload a file for ingestion |
| `POST` | `/skills/search` | Yes | Semantic search within a skill |

### Webhook Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/webhooks/github` | HMAC | Receive GitHub push events (GitOps) |
| `POST` | `/webhooks/docs` | API Key | Receive documentation payloads |

### Search Example

```bash
curl -X POST http://localhost:8000/skills/search \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "tnt_gh_myorg",
    "skill_id": "repo_myproject",
    "query": "authentication login function",
    "limit": 5
  }'
```

## MCP Server (For AI Agents)

Vex exposes tools via the [Model Context Protocol](https://modelcontextprotocol.io) for direct agent integration.

### Available Tools

| Tool | Description |
|---|---|
| `search_vex_skill` | Semantic search within a specific skill |
| `compare_skill_versions` | Compare how code changed between two versions |
| `list_skills` | List all registered skills (optionally by tenant) |
| `get_skill_versions` | List available versions for a skill |

### Running the MCP Server

```bash
uv run python -m src.mcp.server
```

### MCP Configuration (for Claude, etc.)

```json
{
  "mcpServers": {
    "vex": {
      "command": "uv",
      "args": ["run", "python", "-m", "src.mcp.server"],
      "cwd": "/path/to/vex"
    }
  }
}
```

## GitHub Webhook Setup (GitOps)

1. Go to your repository → Settings → Webhooks → Add webhook
2. **Payload URL:** `https://your-server/webhooks/github`
3. **Content type:** `application/json`
4. **Secret:** Set a strong secret and configure `VEX_GITHUB_WEBHOOK_SECRET`
5. **Events:** Select "Just the push event"

On each push to main/master, Vex will:
- Download added/modified files from the commit
- Chunk them via Tree-sitter AST analysis
- Vectorize with delta caching (skip unchanged chunks)
- Delete vectors for removed files (pruning)

## Project Structure

```
vex/
├── main.py                 # CLI entrypoint
├── pyproject.toml           # Dependencies & project metadata
├── src/
│   ├── config.py            # Centralized configuration
│   ├── logger.py            # Structured logging
│   ├── tasks.py             # Background ingestion/deletion pipeline
│   ├── api/
│   │   ├── server.py        # FastAPI REST gateway
│   │   ├── security.py      # API key authentication
│   │   └── schemas.py       # Pydantic request/response models
│   ├── core/
│   │   ├── chunker.py       # Tree-sitter AST chunking engine
│   │   └── search.py        # Shared search service
│   ├── db/
│   │   ├── relational.py    # SQLAlchemy models + SQLite
│   │   └── vector.py        # Qdrant vector database manager
│   └── mcp/
│       └── server.py        # MCP stdio server for AI agents
└── test_mcp.py              # MCP integration test
```

## License

