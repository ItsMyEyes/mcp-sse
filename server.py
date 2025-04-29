from starlette.applications import Starlette
from google_services.mail.mcp_google_gmail import route_mcp as gmail_routes
from google_services.calender.mcp_google_calendar import route_mcp as calendar_routes
import asyncio
import uvicorn

MCP_PORT = 8000

async def run_mcp_server(host: str = "0.0.0.0", port: int = MCP_PORT):
    """Run the MCP server."""

    routes = [
        *gmail_routes(),
        # alendar_routes,
    ]
    starlette_app = Starlette(routes=routes, debug=True)
    config = uvicorn.Config(starlette_app, host=host, port=port)
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    """Run MCP server."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run Google Gmail MCP server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--mcp-port', type=int, default=MCP_PORT, help='Port for MCP server')
    args = parser.parse_args()

    # Run MCP server
    await run_mcp_server(args.host, args.mcp_port)

if __name__ == "__main__":
    asyncio.run(main())