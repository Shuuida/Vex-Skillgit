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
                "CRITICAL RULE: Use this tool ONLY ONCE per user query. Do not invoke this tool in a loop. "
                "Read the returned results and immediately synthesize your final response for the user."
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
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Executes logic when an agent invokes the tool.
    """
    if name != "search_vex_skill":
        raise ValueError(f"Unknown tool: {name}")

    tenant_id = arguments.get("tenant_id")
    skill_id = arguments.get("skill_id")
    query = arguments.get("query")

    try:
        # We vectorize the question (Using local Ollama)
        response = ollama.embeddings(model="nomic-embed-text", prompt=query)
        query_vector = response["embedding"]

        vector_db = get_db_client()
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
            limit=3
        )
        hits = qdrant_response.points

        if not hits:
            return [TextContent(type="text", text="No relevant context found in Vex.")]

        # Extracting heavy database (SQLite)
        db = SessionLocal()
        results_text = []
        try:
            for hit in hits:
                record = db.query(ChunkRecord).filter(ChunkRecord.chunk_id == str(hit.id)).first()
                if record:
                    results_text.append(f"--- File: {record.file_path} ---\n{record.raw_content}\n")
        finally:
            db.close()

        # Here package all the knowledge in a single block of text for the LLM
        compiled_context = "\n".join(results_text)
        return [TextContent(type="text", text=compiled_context)]

    except Exception as e:
        # Errors should be returned as text for the agent to understand
        return [TextContent(type="text", text=f"Error accessing Vex memory: {str(e)}")]

async def main():
    # All logging MUST go through stderr. If we do normal print(), 
    # will corrupt the JSON of stdio and the agent will disconnect.
    print("[Vex MCP] Starting headless standard I/O server...", file=sys.stderr)
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())