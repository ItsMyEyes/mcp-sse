from fastapi import APIRouter, HTTPException, Request as FastAPIRequest, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import os

from .google_auth import GoogleUnifiedAuth, GMAIL_SCOPE, CALENDAR_SCOPE

# Initialize templates
templates_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../templates")
templates = Jinja2Templates(directory=templates_dir)

# Create router
router = APIRouter()

# Get unified auth instance
auth = GoogleUnifiedAuth()

@router.get("/oauth/callback/{service_name}")
async def oauth_callback(
    request: FastAPIRequest, 
    service_name: str, 
    code: str, 
    state: str,
    scope: str = Query(None)
):
    """
    Handle OAuth callback from services.
    
    Args:
        service_name: Service name (gmail or calendar)
        code: Authorization code from OAuth provider
        state: Session ID
    """
    if not state:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": "Session ID is required"
            }
        )

    try:
        creds = auth.handle_oauth_callback(state, code, scope)
        session = auth.get_session(state)
        
        if not session or not creds:
            return templates.TemplateResponse(
                "error.html",
                {
                    "request": request,
                    "error": "Invalid session or authentication failed"
                }
            )
        
        auth.reload_sessions()
        # Return success page
        return templates.TemplateResponse(
            "success.html",
            {
                "request": request,
                "status": "success",
                "service": service_name
            }
        )
    except Exception as e:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": str(e)
            }
        )

@router.get("/oauth/status/{session_id}")
async def check_auth_status(session_id: str):
    """
    Check authentication status for a session.
    
    Args:
        session_id: Session ID to check
    """
    session = auth.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "status": session['status'],
        "scope": session.get('scope', ''),
        "created_at": session.get('created_at', '')
    } 