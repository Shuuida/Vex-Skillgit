import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from src.core.search import search_skill
from src.tasks import process_rollback_task
from src.db.relational import SessionLocal, SkillRecord, ChunkRecord, init_relational_db
from src.db.vector import VectorDBManager
from src.logger import get_logger

log = get_logger("mcp")

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
        ),
        Tool(
            name="list_skills",
            description="Lists all registered Vex skills, optionally filtered by tenant. Returns skill IDs, names, and descriptions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tenant_id": {
                        "type": "string",
                        "description": "Optional tenant ID to filter skills. If omitted, returns all skills."
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_skill_versions",
            description="Lists all known versions (commit hashes) for a specific skill and tenant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "The tenant ID"},
                    "skill_id": {"type": "string", "description": "The skill ID"}
                },
                "required": ["tenant_id", "skill_id"]
            }
        ),

        Tool(
            name="rollback_skill",
            description="Restores a skill's memory pointers to a historical version. Use this autonomously if the 'latest' code breaks the system or fails tests.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tenant_id": {"type": "string", "description": "The tenant ID"},
                    "skill_id": {"type": "string", "description": "The skill ID"},
                    "target_version": {"type": "string", "description": "The historical commit hash to rollback to"}
                },
                "required": ["tenant_id", "skill_id", "target_version"]
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """
    Executes logic when an agent invokes the tool.
    """
    try:
        if name == "search_vex_skill":
            results = search_skill(
                tenant_id=arguments["tenant_id"],
                skill_id=arguments["skill_id"],
                query=arguments["query"]
            )
            if not results:
                return [TextContent(type="text", text="No relevant context found in Vex for this query.")]
            
            compiled = []
            for r in results:
                compiled.append(f"--- File: {r['file_path']} ---\n{r['content']}\n")
            return [TextContent(type="text", text="\n".join(compiled))]

        elif name == "compare_skill_versions":
            version_a = arguments["version_a"]
            version_b = arguments["version_b"]

            results_a = search_skill(
                tenant_id=arguments["tenant_id"],
                skill_id=arguments["skill_id"],
                query=arguments["query"],
                version=version_a
            )
            results_b = search_skill(
                tenant_id=arguments["tenant_id"],
                skill_id=arguments["skill_id"],
                query=arguments["query"],
                version=version_b
            )

            def format_results(results):
                if not results:
                    return "No relevant context found in Vex for this version."
                parts = []
                for r in results:
                    parts.append(f"--- File: {r['file_path']} ---\n{r['content']}\n")
                return "\n".join(parts)

            compiled_context = (
                f"=== CONTEXT FROM VERSION {version_a} ===\n{format_results(results_a)}\n\n"
                f"=== CONTEXT FROM VERSION {version_b} ===\n{format_results(results_b)}"
            )
            return [TextContent(type="text", text=compiled_context)]

        elif name == "list_skills":
            db = SessionLocal()
            try:
                query = db.query(SkillRecord)
                tenant_id = arguments.get("tenant_id")
                if tenant_id:
                    query = query.filter(SkillRecord.tenant_id == tenant_id)
                skills = query.all()
                
                if not skills:
                    return [TextContent(type="text", text="No skills found.")]
                
                lines = [f"Found {len(skills)} skill(s):\n"]
                for s in skills:
                    lines.append(f"- {s.skill_id} | {s.name} | tenant: {s.tenant_id} | {s.description or 'No description'}")
                return [TextContent(type="text", text="\n".join(lines))]
            finally:
                db.close()

        elif name == "get_skill_versions":
            db = SessionLocal()
            try:
                versions = db.query(ChunkRecord.version).filter(
                    ChunkRecord.tenant_id == arguments["tenant_id"],
                    ChunkRecord.skill_id == arguments["skill_id"]
                ).distinct().all()
                
                version_list = [v[0] for v in versions]
                if not version_list:
                    return [TextContent(type="text", text="No versions found for this skill.")]
                
                return [TextContent(type="text", text=f"Available versions ({len(version_list)}): {', '.join(version_list)}")]
            finally:
                db.close()

        elif name == "rollback_skill":
            tenant_id = arguments["tenant_id"]
            skill_id = arguments["skill_id"]
            target_version = arguments["target_version"]
            
            process_rollback_task(tenant_id, skill_id, target_version)
            
            return [TextContent(
                type="text", 
                text=f"✅ Cognitive rollback initiated for skill '{skill_id}' to version '{target_version}'. Background processing started."
            )]

        else:
            raise ValueError(f"Unknown tool: {name}")

    except Exception as e:
        log.error(f"Error in MCP tool '{name}': {e}")
        return [TextContent(type="text", text=f"Error accessing Vex memory: {str(e)}")]

async def main():
    log.info("Starting headless standard I/O server...")
    init_relational_db()
    VectorDBManager.get_client()
    
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())