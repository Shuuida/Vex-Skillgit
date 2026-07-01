import asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    print("🚀 [Client] Arrancando el cliente de pruebas MCP...")
    
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp.server"]
    )
    
    print("🔌 [Client] Conectando al servidor Vex MCP vía stdio...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            
            await session.initialize()
            print("✅ [Client] Conexión establecida. Solicitando Viaje en el Tiempo...\n")
            
            try:
                result = await session.call_tool(
                    "list_skills",
                    arguments={"tenant_id": "tnt_alpha"}
                )
                
                print("================ RESULTADO MCP ================")
                for content in result.content:
                    print(content.text)
                print("===============================================")
                
            except Exception as e:
                print(f"❌ [Client] Error al llamar a la herramienta: {e}")

if __name__ == "__main__":
    asyncio.run(main())