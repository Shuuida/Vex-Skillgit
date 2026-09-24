<div align="center">
  <img src="assets/vex-banner.png" alt="Vex Logo" width="400"/>

  <h3>Declarative Skill Hub for Agentic Memory</h3>

  <p>
    <em>A headless, GitOps-first skills platform that transforms codebases and documents into immutable, versioned cognitive skills.</em>
  </p>

<div>
    <img src="https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
    <img src="https://img.shields.io/badge/TypeScript-007ACC?style=for-the-badge&logo=typescript&logoColor=white" alt="TypeScript" />
    <img src="https://img.shields.io/badge/JavaScript-F7DF1E?style=for-the-badge&logo=javascript&logoColor=black" alt="JavaScript" />
    <img src="https://img.shields.io/badge/Go-00ADD8?style=for-the-badge&logo=go&logoColor=white" alt="Go" />
    <img src="https://img.shields.io/badge/YAML-CB171E?style=for-the-badge&logo=yaml&logoColor=white" alt="YAML" />
    <img src="https://img.shields.io/badge/Markdown-000000?style=for-the-badge&logo=markdown&logoColor=white" alt="Markdown" />
  </div>
</div>

<br/>

## Architecture

Vex uses a **Pointer Architecture** — lightweight vector pointers in Qdrant reference heavy-text records in SQLite. This separates the mathematical search layer from the storage layer, enabling:

- **AST-aware chunking** via Tree-sitter (Python, TypeScript, JavaScript, Go, YAML, Markdown)
- **Intelligent Sub-Chunking** — oversized AST nodes are dynamically split by line breaks to fit token limits without breaking syntax logic.
- **Cryptographic delta caching** — SHA-256 hashes skip re-vectorization of unchanged code
- **Lightly GraphRAG dependency enrichment** — function call graphs are extracted and prepended to chunks
- **Multi-tenant governance** — all queries are scoped by `tenant_id` and `skill_id`
- **Time-travel versioning** — compare how code evolved across commits
- **Native Cognitive Branching** — fork a skill's memory into an isolated environment via zero-cost vector cloning.

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  FastAPI    │───▶│ Tree-sitter   │───▶│   Ollama    │
│  REST API   │     │  AST Chunker │     │  Embeddings │
└──────┬──────┘     └──────────────┘     └──────┬──────┘
       │                                         │
       ▼                                         ▼
┌─────────────┐                           ┌─────────────┐
│   SQLite    │◀──Pointer Architecture──▶│   Qdrant    │
│ (Heavy Text)│                           │  (Vectors)  │
└─────────────┘                           └─────────────┘
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
- Docker & Docker Compose (Optional, for containerized deployment)

### Setup (Docker Compose - Recommended)

The easiest way to run Vex is via Docker Compose, which spins up the API, background worker, and Qdrant database automatically.

```bash
# Clone the repository
git clone https://github.com/your-org/vex.git
cd vex

# Start the stack
docker-compose up -d
```
The API will be available at `http://localhost:8000`.

### Setup (Local Development)

```bash
# Clone and install
git clone https://github.com/your-org/vex.git
cd vex

# Install dependencies with uv
uv sync

# Pull the embedding model
ollama pull nomic-embed-text

# Start the Qdrant database (requires Docker)
docker run -p 6333:6333 -p 6334:6334 -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant

# Start the API server
uv run uvicorn src.api.server:app --reload

# Start the background worker (in a separate terminal)
uv run huey_consumer.py src.tasks.huey
```

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
| `VEX_TEMP_DIR` | `temp_uploads` | Temporary file upload directory |
| `VEX_QDRANT_URL` | `http://localhost:6333` | URL for Qdrant |

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
| `POST` | `/skills/rollback` | Yes | Restores a skill's memory pointers to a historical target version |
| `POST` | `/skills/branch` | Yes | Forks memory directly into a new branch |

### Webhook Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/webhooks/github` | HMAC | Receive GitHub push events (GitOps). Triggers agentic workflows based on commit prefixes. |
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
| `rollback_skill` | Restores a skill's memory pointers to a historical version |
| `branch_vex_memory` | Isolates memory by cloning a source version into a new branch |

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

## GitOps & SKILLS.yaml

Vex operates via a webhook-driven GitOps pipeline.

### GitHub Webhook Setup

1. Go to your repository → Settings → Webhooks → Add webhook
2. **Payload URL:** `https://your-server/webhooks/github`
3. **Content type:** `application/json`
4. **Secret:** Set a strong secret and configure `VEX_GITHUB_WEBHOOK_SECRET`
5. **Events:** Select "Just the push event"

### SKILLS.yaml Manifest

Vex allows fine-grained control over what gets ingested and how it is processed by dropping a `SKILLS.yaml` file into the root of your repository. This manifest allows you to map specific folders to distinct skills and tune the semantic memory chunking.

```yaml
skills:
  core_engine:
    description: "Core logic and routing"
    auto_register: true
    context_boundaries:
      include_extensions: [".py"]
      exclude_paths: ["tests/*"]
    memory_tuning:
      chunk_size: 1500
      overlap: 200

  frontend_components:
    description: "React UI components"
    auto_register: true
    context_boundaries:
      include_extensions: [".tsx", ".ts"]
```

### Agentic & Operational Commits

Vex uses [Conventional Commits](https://www.conventionalcommits.org/) to route cognitive tasks. Prepending standard prefixes signals intent to Vex.

*   `feat:`, `fix:`, `refactor:`, `docs:`, etc. — Standard ingestion behavior.
*   **`roll: <commit_hash>`** — Instructs Vex to perform a cognitive rollback to the specified hash.
*   **`branch: <branch_name>`** — Instructs Vex to branch its memory space off the 'latest' version.

## Project Structure

```
vex/
├── main.py                 # CLI entrypoint
├── pyproject.toml           # Dependencies & project metadata
├── docker-compose.yml       # Stack deployment definition
├── SKILLS.yaml              # Example manifest for cognitive mapping
├── src/
│   ├── config.py            # Centralized configuration
│   ├── logger.py            # Structured logging
│   ├── tasks.py             # Background ingestion/deletion pipeline (Huey)
│   ├── api/
│   │   ├── server.py        # FastAPI REST gateway
│   │   ├── security.py      # API key authentication
│   │   └── schemas.py       # Pydantic request/response models
│   ├── core/
│   │   ├── chunker.py       # Tree-sitter AST chunking engine with Sub-chunking
│   │   ├── search.py        # Shared search service
│   │   ├── manifest.py      # Parses SKILLS.yaml
│   │   └── git_parser.py    # Extracts intent from conventional commits
│   ├── db/
│   │   ├── relational.py    # SQLAlchemy models + SQLite
│   │   └── vector.py        # Qdrant vector database manager
│   └── mcp/
│       └── server.py        # MCP stdio server for AI agents
```

## License

This project is licensed under the Apache License, Version 2.0 - see the LICENSE file for details.