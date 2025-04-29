from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
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
from typing import Dict, Any, Optional

from config import settings
from logger import logger
from exceptions import MCPError, AuthenticationError, ValidationError

# Setup Jinja2 templates
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Initialize auth
auth = GoogleUnifiedAuth()

# Create FastAPI app for auth routes
auth_app = FastAPI(title="Google OAuth Auth API")

@auth_app.get("/auth/callback")
async def fastapi_auth_callback(
    request: Request,
    code: str = Query(None), 
    state: str = Query(None),
    error: str = Query(None),
    scope: str = Query(None)
):
    """Handle OAuth callback from Google using FastAPI."""
    try:
        if error:
            raise AuthenticationError(error)
        
        if not code or not state:
            raise ValidationError("Missing code or state parameter")
        
        # Parse new scopes if provided
        new_scopes = None
        if scope:
            new_scopes = [s.strip() for s in scope.split(' ')]
        
        # Handle OAuth callback
        creds = auth.handle_oauth_callback(state, code, new_scopes)
        if not creds:
            raise AuthenticationError("Failed to obtain credentials")
        
        return templates.TemplateResponse(
            "success.html",
            {"request": request}
        )
    except MCPError as e:
        logger.error(f"Auth callback error: {str(e)}")
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": str(e)}
        )
    except Exception as e:
        logger.error(f"Unexpected error in auth callback: {str(e)}")
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "error": "An unexpected error occurred"}
        )
    
def run_auth_server():
    """Run the FastAPI auth server for OAuth callbacks."""
    # Add CORS middleware
    auth_app.add_middleware(
        FastAPICORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=settings.CORS_METHODS,
        allow_headers=settings.CORS_HEADERS,
    )
    
    # Run the auth server
    logger.info(f"Starting auth server on port {settings.AUTH_PORT}")
    uvicorn.run(auth_app, host=settings.MCP_HOST, port=settings.AUTH_PORT)

async def run_mcp_server():
    """Run the MCP server."""
    try:
        routes = [
            *gmail_routes(),
            *calendar_routes(),
        ]        
        starlette_app = Starlette(routes=routes, debug=True)
        
        # Add CORS middleware
        starlette_app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=settings.CORS_METHODS,
            allow_headers=settings.CORS_HEADERS,
        )
        
        config = uvicorn.Config(starlette_app, host=settings.MCP_HOST, port=settings.MCP_PORT)
        server = uvicorn.Server(config)
        
        logger.info(f"Starting MCP server on port {settings.MCP_PORT}")
        await server.serve()
    except Exception as e:
        logger.error(f"Error running MCP server: {str(e)}")
        raise

async def main():
    """Run MCP server and auth server."""
    try:
        # Start auth server in a separate thread
        auth_thread = threading.Thread(target=run_auth_server, daemon=True)
        auth_thread.start()
        
        # Run MCP server
        await run_mcp_server()
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(main())