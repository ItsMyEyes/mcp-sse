from starlette.applications import Starlette
from starlette.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse as FastAPIHTMLResponse
from fastapi.middleware.cors import CORSMiddleware as FastAPICORSMiddleware
from fastapi.templating import Jinja2Templates
from google_services.mail.mcp_google_gmail import route_mcp as gmail_routes
from google_services.calender.mcp_google_calendar import route_mcp as calendar_routes
from google_services.auth.google_auth import GoogleUnifiedAuth
import asyncio
import uvicorn
import uuid
import threading
import os

MCP_PORT = 8000
AUTH_PORT = 8001  # Separate port for auth server
auth = GoogleUnifiedAuth()

# Update redirect URI to use the auth port
# REDIRECT_URI = f"http://localhost:{AUTH_PORT}/auth/callback"

# Setup Jinja2 templates - get the directory where the script is running
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Create FastAPI app for auth routes
auth_app = FastAPI(title="Google OAuth Auth API")

@auth_app.get("/auth/callback")
async def fastapi_auth_callback(
    request: Request,
    code: str = Query(None), 
    state: str = Query(None),
    error: str = Query(None)
):
    """
    Handle OAuth callback from Google using FastAPI.
    """
    if error:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": error}
        )
    
    if not code or not state:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": "Missing code or state parameter"}
        )
    
    try:
        # Handle OAuth callback
        creds = auth.handle_oauth_callback(state, code)
        if creds:
            return templates.TemplateResponse(
                "success.html",
                {"request": request}
            )
        else:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "error": "Failed to obtain credentials"}
            )
    except Exception as e:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": str(e)}
        )

async def auth_start(request: Request):
    """
    Start OAuth flow. Creates a session ID and redirects to Google auth.
    """
    scope = request.query_params.get('scope', 'https://www.googleapis.com/auth/gmail.readonly')
    session_id = str(uuid.uuid4())
    
    # Create a new session
    auth.create_session(session_id, scope)
    
    # Get auth URL
    auth_url = auth.get_auth_url(session_id)
    
    return JSONResponse({
        "session_id": session_id,
        "auth_url": auth_url
    })

async def auth_status(request: Request):
    """
    Check authentication status for a session.
    """
    session_id = request.query_params.get('session_id')
    scope = request.query_params.get('scope', '')
    
    if not session_id:
        return JSONResponse({"status": "error", "message": "Missing session_id parameter"})
    
    session = auth.get_session(session_id)
    if not session:
        return JSONResponse({"status": "not_found", "message": "Session not found"})
    
    if session.get('status') == 'completed' and session.get('token_data'):
        if scope and not auth.has_scope(session_id, scope):
            # Need additional scope
            _, auth_url = auth.authenticate(session_id, scope)
            return JSONResponse({
                "status": "needs_additional_scope",
                "message": "Additional scope needed",
                "auth_url": auth_url
            })
        
        return JSONResponse({"status": "authenticated", "message": "User is authenticated"})
    
    return JSONResponse({
        "status": session.get('status', 'unknown'),
        "message": "Authentication pending or incomplete"
    })

def run_auth_server():
    """
    Run the FastAPI auth server for OAuth callbacks.
    """
    # Add CORS middleware
    auth_app.add_middleware(
        FastAPICORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Run the auth server
    uvicorn.run(auth_app, host="0.0.0.0", port=AUTH_PORT)

async def run_mcp_server(host: str = "0.0.0.0", port: int = MCP_PORT):
    """Run the MCP server."""

    routes = [
        *gmail_routes(),
        # calendar_routes,
    ]
    
    # Add OAuth routes (except callback which is handled by FastAPI)
    from starlette.routing import Route
    routes.extend([
        Route('/auth/start', auth_start),
        Route('/auth/status', auth_status),
    ])
    
    starlette_app = Starlette(routes=routes, debug=True)
    
    # Add CORS middleware
    starlette_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    config = uvicorn.Config(starlette_app, host=host, port=port)
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    """Run MCP server and auth server."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run Google Gmail MCP server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--mcp-port', type=int, default=MCP_PORT, help='Port for MCP server')
    parser.add_argument('--auth-port', type=int, default=AUTH_PORT, help='Port for auth server')
    args = parser.parse_args()
    
    # Start auth server in a separate thread
    auth_thread = threading.Thread(target=run_auth_server, daemon=True)
    auth_thread.start()
    
    # Run MCP server
    await run_mcp_server(args.host, args.mcp_port)

if __name__ == "__main__":
    asyncio.run(main())