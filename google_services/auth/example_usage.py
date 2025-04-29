"""
Example of how to use the unified authentication system.

This file demonstrates how to use the GoogleUnifiedAuth class to authenticate
with different Google services.
"""
import json
import asyncio
from typing import Optional

from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse

# Import our unified auth system
from google_services.auth import GoogleUnifiedAuth, GMAIL_SCOPE, CALENDAR_SCOPE, oauth_router
from google_services.mail.mcp_google_gmail_adapters import GmailAuthAdapter
from google_services.calender.mcp_google_calendar_adapters import CalendarAuthAdapter

# Create FastAPI app
app = FastAPI()

# Include OAuth routes
app.include_router(oauth_router, prefix="/api")

# Auth adapters
gmail_auth = GmailAuthAdapter()
calendar_auth = CalendarAuthAdapter()

@app.get("/api/auth/gmail/{session_id}")
async def gmail_auth_status(session_id: str):
    """Get Gmail authentication status for a session."""
    creds, auth_url = await gmail_auth.authenticate(session_id)
    
    if creds:
        return {
            "authenticated": True,
            "service": "gmail",
            "session_id": session_id
        }
    else:
        return {
            "authenticated": False,
            "service": "gmail",
            "session_id": session_id,
            "auth_url": auth_url
        }

@app.get("/api/auth/calendar/{session_id}")
async def calendar_auth_status(session_id: str):
    """Get Calendar authentication status for a session."""
    creds, auth_url = await calendar_auth.authenticate(session_id)
    
    if creds:
        return {
            "authenticated": True,
            "service": "calendar",
            "session_id": session_id
        }
    else:
        return {
            "authenticated": False,
            "service": "calendar",
            "session_id": session_id,
            "auth_url": auth_url
        }

@app.get("/api/sessions/{session_id}")
async def get_session_info(session_id: str):
    """Get session information from the unified auth system."""
    # We can use either adapter to get session info since they both use the same GoogleUnifiedAuth instance
    session = await gmail_auth.get_session(session_id)
    
    if not session:
        return JSONResponse(
            status_code=404,
            content={"error": "Session not found"}
        )
    
    return session

# Example usage in a non-FastAPI context
async def example_authenticate():
    """Example of how to authenticate with Gmail and Calendar."""
    auth = GoogleUnifiedAuth()
    
    # Generate a session ID (in a real app, you'd use something like uuid4)
    session_id = "example-session-123"
    
    # Gmail authentication
    gmail_creds, gmail_auth_url = auth.authenticate(session_id, GMAIL_SCOPE)
    if gmail_creds:
        print("Already authenticated with Gmail!")
    else:
        print(f"Please authenticate with Gmail: {gmail_auth_url}")
    
    # After the user authenticates via the URL, the callback will update the session
    
    # Calendar authentication (would require a new session ID in practice)
    calendar_session_id = "example-session-456"
    calendar_creds, calendar_auth_url = auth.authenticate(calendar_session_id, CALENDAR_SCOPE)
    if calendar_creds:
        print("Already authenticated with Calendar!")
    else:
        print(f"Please authenticate with Calendar: {calendar_auth_url}")

if __name__ == "__main__":
    # If running directly, run the example
    asyncio.run(example_authenticate()) 