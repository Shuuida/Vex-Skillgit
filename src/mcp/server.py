import asyncio
import sys
import ollama
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.db.vector import get_db_client
from src.db.relational import SessionLocal, ChunkRecord
from qdrant_client.models import Filter, FieldCondition, MatchValue

app = Server("vex-memory-hub")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """
    Declare the tools that Vex exposes to AI agents.
    """
    return [
        Tool(
            name="search_vex_skill",
            description=(
                "Searches the Vex memory hub for specific architectural, code, or documentation context. "
                "CRITICAL INSTRUCTION: Do not pass abstract human concepts. Translate the user's need into "
                "likely code keywords, function names, classes, or variable names (e.g., instead of 'login logic', "
                "use 'def login auth verify password'). Use this tool ONLY ONCE per query."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tenant_id": {
                        "type": "string", 
                        "description": "The tenant ID (e.g., 'tnt_alpha')"
                    },
                    "skill_id": {
                        "type": "string", 
                        "description": "The skill ID (e.g., 'core_documentation')"
                    },
                    "query": {
                        "type": "string", 
                        "description": "The semantic question or concept to search for"
                    }
                },
                "required": ["tenant_id", "skill_id", "query"]
            }
        ),
        Tool(
            name="compare_skill_versions",
            description=(
                "Compares how a specific concept or logic changed between two versions (commits) of a Vex skill. "
                "CRITICAL INSTRUCTION: Always translate the abstract concept into exact code syntax or technical "
                "keywords before searching (e.g., 'def login_user', 'class Auth', 'import jwt')."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "The tenant ID"},
                    "skill_id": {"type": "string", "description": "The skill ID"},
                    "query": {"type": "string", "description": "The specific logic or concept to compare (e.g. 'authentication login')"},
                    "version_a": {"type": "string", "description": "The old/base version or commit hash"},
                    "version_b": {"type": "string", "description": "The new version or commit hash"}
                },
                "required": ["tenant_id", "skill_id", "query", "version_a", "version_b"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Executes logic when an agent invokes the tool.
    """
    if name not in ["search_vex_skill", "compare_skill_versions"]:
        raise ValueError(f"Unknown tool: {name}")

    tenant_id = arguments.get("tenant_id")
    skill_id = arguments.get("skill_id")
    query = arguments.get("query")

    try:
        enhanced_query = f"source code, function definition, class, method, technical implementation of: {query}"
        response = ollama.embeddings(model="nomic-embed-text", prompt=enhanced_query)
        query_vector = response["embedding"]
        vector_db = get_db_client()

        # Helper to extract heavy text from SQLite
        def extract_text_from_hits(hits, db_session):
            if not hits:
                return "No relevant context found in Vex for this version."
            results_text = []
            for hit in hits:
                record = db_session.query(ChunkRecord).filter(ChunkRecord.chunk_id == str(hit.id)).first()
                if record:
                    results_text.append(f"--- File: {record.file_path} ---\n{record.raw_content}\n")
            return "\n".join(results_text)

        if name == "search_vex_skill":
            search_filter = Filter(
                must=[
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                    FieldCondition(key="skill_id", match=MatchValue(value=skill_id))
                ]
            )
            qdrant_response = vector_db.query_points(
                collection_name="vex_skills",
                query=query_vector,
                query_filter=search_filter,
                limit=3,
                score_threshold=0.70  # Umbral de seguridad
            )
            
            db = SessionLocal()
            try:
                compiled_context = extract_text_from_hits(qdrant_response.points, db)
                return [TextContent(type="text", text=compiled_context)]
            finally:
                db.close()

        elif name == "compare_skill_versions":
            version_a = arguments.get("version_a")
            version_b = arguments.get("version_b")

            # Search Qdrant for a specific version
            def search_by_version(v: str):
                v_filter = Filter(
                    must=[
                        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                        FieldCondition(key="skill_id", match=MatchValue(value=skill_id)),
                        FieldCondition(key="version", match=MatchValue(value=v))
                    ]
                )
                res = vector_db.query_points(
                    collection_name="vex_skills",
                    query=query_vector,
                    query_filter=v_filter,
                    limit=3,
                    score_threshold=0.70
                )
                return res.points

            hits_a = search_by_version(version_a)
            hits_b = search_by_version(version_b)

            db = SessionLocal()
            try:
                context_a = extract_text_from_hits(hits_a, db)
                context_b = extract_text_from_hits(hits_b, db)
            finally:
                db.close()

            # And the story is packaged in such a way that the LLM can cross-reference the variables.
            compiled_context = (
                f"=== CONTEXT FROM VERSION {version_a} ===\n{context_a}\n\n"
                f"=== CONTEXT FROM VERSION {version_b} ===\n{context_b}"
            )
            return [TextContent(type="text", text=compiled_context)]

    except Exception as e:
        return [TextContent(type="text", text=f"Error accessing Vex memory: {str(e)}")]

async def main():
    # All logging MUST go through stderr. If we do normal print(), 
    # will corrupt the JSON of stdio and the agent will disconnect.
    print("[Vex MCP] Starting headless standard I/O server...", file=sys.stderr)
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())