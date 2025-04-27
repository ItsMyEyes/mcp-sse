import os
import json
import datetime
import secrets
import asyncio
from typing import List, Dict, Optional, Tuple
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build
from mcp.server import FastMCP
from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request as StarletteRequest
from starlette.routing import Mount, Route
from mcp.server import Server
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.requests import Request as FastAPIRequest
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx

# Initialize FastAPI app
fastapi_app = FastAPI(title="Google Calendar OAuth")

# Initialize FastMCP server
app = FastMCP('google-calendar')

# OAuth configuration
REDIRECT_URI = "https://oauth.kiyora.dev/oauth/callback"
OAUTH_SCOPES = ['https://www.googleapis.com/auth/calendar']


# Modify the MCP tools to use the correct FastAPI port
FASTAPI_PORT = 8000
MCP_PORT = 8080

# Templates
templates = Jinja2Templates(directory="templates")

class GoogleCalendarAuth:
    def __init__(self, credentials_file: str = 'credentials.json', token_file: str = 'tokens.json', sessions_file: str = 'sessions.json'):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.sessions_file = sessions_file
        self.tokens: Dict[str, Dict] = {}  # Changed to use session_id as key
        self.sessions: Dict[str, Dict] = {}
        self._load_tokens()
        self._load_sessions()

    def _load_tokens(self) -> None:
        """Load tokens from the JSON file."""
        if os.path.exists(self.token_file):
            with open(self.token_file, 'r') as f:
                self.tokens = json.load(f)

    def _save_tokens(self) -> None:
        """Save tokens to the JSON file."""
        with open(self.token_file, 'w') as f:
            json.dump(self.tokens, f, indent=2)

    def _load_sessions(self) -> None:
        """Load sessions from the JSON file."""
        if os.path.exists(self.sessions_file):
            with open(self.sessions_file, 'r') as f:
                self.sessions = json.load(f)

    def _save_sessions(self) -> None:
        """Save sessions to the JSON file."""
        with open(self.sessions_file, 'w') as f:
            json.dump(self.sessions, f, indent=2)

    def create_session(self, session_id: str) -> str:
        """Create a new session."""
        self.sessions[session_id] = {
            'created_at': datetime.datetime.utcnow().isoformat(),
            'status': 'pending',
            'redirect_uri': REDIRECT_URI
        }
        self._save_sessions()
        return session_id

    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session information."""
        return self.sessions.get(session_id)

    def update_session(self, session_id: str, status: str, token_data: Optional[Dict] = None) -> None:
        """Update session status and token data."""
        if session_id in self.sessions:
            self.sessions[session_id]['status'] = status
            if token_data:
                self.sessions[session_id]['token_data'] = token_data
            self._save_sessions()

    def get_credentials(self, session_id: str) -> Optional[Credentials]:
        """Get credentials for a specific session."""
        token_data = self.tokens.get(session_id)
        if not token_data:
            return None

        creds = Credentials.from_authorized_user_info(
            {
                'token': token_data['token'],
                'refresh_token': token_data.get('refresh_token'),
                'token_uri': 'https://oauth2.googleapis.com/token',
                'client_id': token_data.get('client_id'),
                'client_secret': token_data.get('client_secret'),
                'scopes': OAUTH_SCOPES
            }
        )

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            token_data['token'] = creds.token
            self._save_tokens()

        return creds

    def get_auth_url(self, session_id: str) -> str:
        """Get OAuth2 authorization URL for a session."""
        # Load client configuration
        with open(self.credentials_file, 'r') as f:
            client_config = json.load(f)
        
        # Create flow instance
        flow = Flow.from_client_config(
            client_config,
            scopes=OAUTH_SCOPES,
            redirect_uri=REDIRECT_URI
        )
        
        # Generate authorization URL
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            state=session_id,
            prompt='consent',
        )
        return auth_url

    def authenticate(self, session_id: str) -> Tuple[Optional[Credentials], Optional[str]]:
        """Authenticate a session and return credentials and auth URL if needed."""
        creds = self.get_credentials(session_id)
        if creds:
            return creds, None

        # Create a new session if it doesn't exist
        if session_id not in self.sessions:
            self.create_session(session_id)
        auth_url = self.get_auth_url(session_id)
        return None, auth_url

    def handle_oauth_callback(self, session_id: str, code: str) -> Optional[Credentials]:
        """Handle OAuth callback from oauth.kiyora.dev."""
        session = self.get_session(session_id)
        if not session:
            return None

        try:
            # Load client configuration
            with open(self.credentials_file, 'r') as f:
                client_config = json.load(f)
            
            # Create flow instance
            flow = Flow.from_client_config(
                client_config,
                scopes=OAUTH_SCOPES,
                redirect_uri=REDIRECT_URI
            )
            
            # Exchange code for tokens
            flow.fetch_token(code=code)
            creds = flow.credentials

            # Save the credentials
            token_data = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret
            }
            
            # Save token data
            self.tokens[session_id] = token_data
            self._save_tokens()

            # Update session status
            self.update_session(session_id, 'completed', token_data)
            
            return creds
        except Exception as e:
            self.update_session(session_id, 'failed')
            raise e

@fastapi_app.get("/oauth/callback")
async def oauth_callback(request: FastAPIRequest, code: str, state: str):
    """
    Handle OAuth callback from Google.
    
    Args:
        code: Authorization code from Google
        state: Session ID from our application
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
        auth = GoogleCalendarAuth()
        creds = auth.handle_oauth_callback(state, code)
        session = auth.get_session(state)
        
        if not session or not creds:
            return templates.TemplateResponse(
                "error.html",
                {
                    "request": request,
                    "error": "Invalid session or authentication failed"
                }
            )
        
        # Return success page
        return templates.TemplateResponse(
            "success.html",
            {
                "request": request,
                "status": "success"
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

@fastapi_app.get("/oauth/status/{session_id}")
async def check_auth_status(session_id: str):
    """
    Check authentication status for a session.
    
    Args:
        session_id: Session ID to check
    """
    auth = GoogleCalendarAuth()
    session = auth.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "status": session['status'],
        "email": session['email']
    }

@fastapi_app.get("/oauth/start")
async def start_auth(request: StarletteRequest):
    """
    Start OAuth flow for a user.
    """
    auth = GoogleCalendarAuth()
    auth_url = auth.get_auth_url("")
    
    return templates.TemplateResponse(
            "start.html",
            {
                "request": request,
                "auth_url": auth_url
            }
        )

@app.tool()
async def get_auth_status(session_id: str) -> str:
    """
    Check the authentication status of a session and get the OAuth URL if needed.
    
    Usage:
    - First step in the authentication flow
    - Use this to check if a session is already authenticated
    - If not authenticated, use the returned auth_url to start OAuth flow
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        
    Returns:
        str: Text response with authentication status
        - If authenticated: "Status: Authenticated"
        - If unauthenticated: "Status: Unauthenticated\nPlease authenticate here: [auth_url]"
        - If error: "Error: [error_message]"
        
    Example:
        status = await get_auth_status("abc123")
        if "unauthenticated" in status:
            # Get auth_url from status and redirect user
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if creds:
        return "Status: Authenticated"
    else:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"

@app.tool()
async def list_calendar_events(
    session_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_results: int = 10
) -> str:
    """
    List calendar events within a specified date range.
    
    Usage:
    - View all events in a user's calendar
    - Filter events by date range
    - Get a summary of upcoming events
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        start_date (str, optional): Start date in YYYY-MM-DD format
        end_date (str, optional): End date in YYYY-MM-DD format
        max_results (int): Maximum number of events to return (default: 10)
        
    Returns:
        str: Formatted text response with events
        Format:
        -----
        EventID: [id]
        Title: [title]
        Start: [start_time] | End: [end_time]
        Content: [description]
        -----
        
    Example:
        events = await list_calendar_events("abc123", "2024-03-01", "2024-03-31")
        # Returns formatted list of events in March 2024
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Set up time range
        if start_date:
            time_min = f"{start_date}T00:00:00Z"
        else:
            time_min = datetime.datetime.utcnow().isoformat() + 'Z'
            
        if end_date:
            time_max = f"{end_date}T23:59:59Z"
        else:
            time_max = None
        
        # Build query
        query = {
            'calendarId': 'primary',
            'timeMin': time_min,
            'maxResults': max_results,
            'singleEvents': True,
            'orderBy': 'startTime'
        }
        
        if time_max:
            query['timeMax'] = time_max
        
        events_result = service.events().list(**query).execute()
        events = events_result.get('items', [])
        
        if not events:
            return "No events found in the specified time range."
            
        response = "Calendar Events:\n\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            response += f"-----\n"
            response += f"EventID: {event['id']}\n"
            response += f"Title: {event.get('summary', 'No title')}\n"
            response += f"Start: {start} | End: {end}\n"
            if event.get('description'):
                response += f"Content: {event['description']}\n"
            response += f"-----\n\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def list_colors(session_id: str) -> str:
    """
    List available calendar colors and their IDs.
    
    Usage:
    - Get available color options for events
    - Find color IDs for event customization
    - View color names and their corresponding IDs
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        
    Returns:
        str: Formatted text response with available colors
        Format:
        -----
        Color ID: [id]
        Name: [name]
        Background: [background_color]
        Foreground: [foreground_color]
        -----
        
    Example:
        colors = await list_colors("abc123")
        # Returns list of available colors with their IDs
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        colors = service.colors().get().execute()
        
        # response = "Available Calendar Colors:\n\n"
        response = ""
        for color_id, color_info in colors['event'].items():
            response += f"-----\n"
            response += f"Color ID: {color_id}\n"
            response += f"Background: {color_info['background']}\n"
            response += f"Foreground: {color_info['foreground']}\n"
            response += f"-----\n\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def create_calendar_event(
    session_id: str,
    event_data: Dict,
    timezone: str = "Asia/Jakarta"
) -> str:
    """
    Create a new calendar event.
    
    Usage:
    - Schedule new meetings or appointments
    - Add events to user's calendar
    - Set up recurring events
    - Customize event colors
    - Set event timezone
    - Manage event attendees
    - Set up event reminders
    - Configure event recurrence
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_data (dict): Event details including:
            - summary (str): Event title
            - description (str, optional): Event description
            - start (dict): Start time with dateTime or date
            - end (dict): End time with dateTime or date
            - location (str, optional): Event location
            - attendees (list, optional): List of attendee objects
                - email (str): Email address of the attendee
            - colorId (str, optional): Color ID for the event (use list_colors to see available IDs)
            - reminders (dict, optional): Reminder settings
                - useDefault (bool): Whether to use default reminders
                - overrides (list, optional): Custom reminders
                    - method (str): "email" or "popup" (defaults to popup)
                    - minutes (int): Minutes before event to trigger reminder
            - recurrence (list, optional): List of recurrence rules in RFC5545 format
                Example: ["RRULE:FREQ=WEEKLY;COUNT=5"]
        timezone (str): Timezone for the event (default: "Asia/Jakarta")
            Examples: "Asia/Jakarta", "UTC", "America/New_York"
            
    Returns:
        str: Formatted text response with created event details
        Format:
        -----
        EventID: [id]
        Title: [title]
        Start: [start_time] | End: [end_time]
        Timezone: [timezone]
        Content: [description]
        Color: [color_name]
        Attendees: [attendee_emails]
        Recurrence: [recurrence_rules]
        -----
        
    Example:
        event = {
            'summary': 'Team Meeting',
            'start': {'dateTime': '2024-03-20T10:00:00'},
            'end': {'dateTime': '2024-03-20T11:00:00'},
            'attendees': [
                {'email': 'team.member1@example.com'},
                {'email': 'team.member2@example.com'}
            ],
            'colorId': '1',  # Lavender color
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 30}        # 30 minutes before
                ]
            },
            'recurrence': [
                'RRULE:FREQ=WEEKLY;COUNT=5'  # Weekly for 5 occurrences
            ]
        }
        result = await create_calendar_event("abc123", event)
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        event = service.events().insert(
            calendarId='primary',
            body=event_data
        ).execute()
        
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        
        response = "Event created successfully:\n\n"
        response += f"-----\n"
        response += f"EventID: {event['id']}\n"
        response += f"Title: {event.get('summary', 'No title')}\n"
        response += f"Start: {start} | End: {end}\n"
        response += f"Timezone: {timezone}\n"
        if event.get('description'):
            response += f"Content: {event['description']}\n"
        response += f"-----\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def update_calendar_event(
    session_id: str,
    event_id: str,
    event_data: Dict,
    timezone: str = "Asia/Jakarta"
) -> str:
    """
    Update an existing calendar event.
    
    Usage:
    - Modify event details (time, title, description)
    - Add or remove attendees
    - Change event location
    - Update reminder settings
    - Change event color
    - Update event timezone
    - Modify recurrence rules
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_id (str): ID of the event to update
        event_data (dict): Updated event details including:
            - summary (str, optional): Event title
            - description (str, optional): Event description
            - start (dict, optional): Start time with dateTime or date
            - end (dict, optional): End time with dateTime or date
            - location (str, optional): Event location
            - attendees (list, optional): List of attendee objects
                - email (str): Email address of the attendee
            - colorId (str, optional): Color ID for the event (use list_colors to see available IDs)
            - reminders (dict, optional): Reminder settings
                - useDefault (bool): Whether to use default reminders
                - overrides (list, optional): Custom reminders
                    - method (str): "email" or "popup" (defaults to popup)
                    - minutes (int): Minutes before event to trigger reminder
            - recurrence (list, optional): List of recurrence rules in RFC5545 format
                Example: ["RRULE:FREQ=WEEKLY;COUNT=5"]
        timezone (str): Timezone for the event (default: "Asia/Jakarta")
            Examples: "Asia/Jakarta", "UTC", "America/New_York"
        
    Returns:
        str: Formatted text response with updated event details
        Format:
        -----
        EventID: [id]
        Title: [title]
        Start: [start_time] | End: [end_time]
        Timezone: [timezone]
        Content: [description]
        Color: [color_name]
        Attendees: [attendee_emails]
        Recurrence: [recurrence_rules]
        -----
        
    Example:
        updates = {
            'summary': 'Updated Team Meeting',
            'start': {'dateTime': '2024-03-20T11:00:00'},
            'attendees': [
                {'email': 'new.member@example.com'}
            ],
            'colorId': '2',  # Sage color
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 15}  # 15 minutes before
                ]
            },
            'recurrence': [
                'RRULE:FREQ=WEEKLY;COUNT=3'  # Weekly for 3 occurrences
            ]
        }
        result = await update_calendar_event("abc123", "event123", updates)
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        event = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=event_data
        ).execute()
        
        start = event['start'].get('dateTime', event['start'].get('date'))
        end = event['end'].get('dateTime', event['end'].get('date'))
        
        response = "Event updated successfully:\n\n"
        response += f"-----\n"
        response += f"EventID: {event['id']}\n"
        response += f"Title: {event.get('summary', 'No title')}\n"
        response += f"Start: {start} | End: {end}\n"
        response += f"Timezone: {timezone}\n"
        if event.get('description'):
            response += f"Content: {event['description']}\n"
        response += f"-----\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def delete_calendar_event(
    session_id: str,
    event_id: str
) -> str:
    """
    Delete a calendar event.
    
    Usage:
    - Remove unwanted or cancelled events
    - Clean up old events
    - Cancel scheduled meetings
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_id (str): ID of the event to delete
        
    Returns:
        str: Success message or error
        - Success: "Event [id] deleted successfully"
        - Error: "Error: [error_message]"
        
    Example:
        result = await delete_calendar_event("abc123", "event123")
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        service.events().delete(
            calendarId='primary',
            eventId=event_id
        ).execute()
        
        return f"Event {event_id} deleted successfully"
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def search_events_with_attachments(
    session_id: str,
    query: str = "",
    max_results: int = 10
) -> str:
    """
    Search for calendar events that have file attachments.
    
    Usage:
    - Find events with attached documents
    - Locate meetings with shared materials
    - Search for events with specific attachments
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        query (str): Optional search query to filter events
        max_results (int): Maximum number of events to return (default: 10)
        
    Returns:
        str: Formatted text response with events that have attachments
        Format:
        -----
        EventID: [id]
        Title: [title]
        Start: [start_time] | End: [end_time]
        Content: [description]
        Attachments:
        - [attachment_title] ([mime_type])
        - [another_attachment] ([mime_type])
        -----
        
    Example:
        # Find all events with attachments
        events = await search_events_with_attachments("abc123")
        
        # Search for events with specific attachments
        events = await search_events_with_attachments("abc123", "presentation")
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Build query
        query_params = {
            'calendarId': 'primary',
            'maxResults': max_results,
            'singleEvents': True,
            'orderBy': 'startTime',
            'q': query
        }
        
        events_result = service.events().list(**query_params).execute()
        events = events_result.get('items', [])
        
        # Filter events with attachments
        events_with_attachments = []
        for event in events:
            if 'attachments' in event:
                events_with_attachments.append(event)
        
        if not events_with_attachments:
            return "No events with attachments found."
            
        response = "Events with Attachments:\n\n"
        for event in events_with_attachments:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            response += f"-----\n"
            response += f"EventID: {event['id']}\n"
            response += f"Title: {event.get('summary', 'No title')}\n"
            response += f"Start: {start} | End: {end}\n"
            if event.get('description'):
                response += f"Content: {event['description']}\n"
            response += "Attachments:\n"
            for attachment in event.get('attachments', []):
                response += f"- {attachment.get('title', 'Untitled')} ({attachment.get('mimeType', 'Unknown type')})\n"
            response += f"-----\n\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def search_calendar_events(
    session_id: str,
    query: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_results: int = 10,
    order_by: str = "startTime",
    timezone: str = "Asia/Jakarta",
    include_deleted: bool = False,
    single_events: bool = True
) -> str:
    """
    Search calendar events with flexible criteria.
    
    Usage:
    - Search events by keywords in title or description
    - Filter events by date range
    - Sort events by different criteria
    - Include or exclude deleted events
    - Handle recurring events
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        query (str): Search terms to find events (searches in title and description)
        start_date (str, optional): Start date in YYYY-MM-DD format
        end_date (str, optional): End date in YYYY-MM-DD format
        max_results (int): Maximum number of events to return (default: 10)
        order_by (str): How to order events - "startTime" (default) or "updated"
        timezone (str): Timezone for the event times (default: "Asia/Jakarta")
        include_deleted (bool): Whether to include deleted events (default: False)
        single_events (bool): Whether to expand recurring events (default: True)
        
    Returns:
        str: Formatted text response with matching events
        Format:
        -----
        EventID: [id]
        Title: [title]
        Start: [start_time] | End: [end_time]
        Status: [status]
        Location: [location]
        Content: [description]
        Created: [created_time]
        Updated: [updated_time]
        -----
        
    Example:
        # Search for all meetings in March 2024
        events = await search_calendar_events(
            "abc123",
            query="meeting",
            start_date="2024-03-01",
            end_date="2024-03-31"
        )
        
        # Search for recently updated events
        events = await search_calendar_events(
            "abc123",
            max_results=5,
            order_by="updated"
        )
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleCalendarAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Set up time range
        if start_date:
            time_min = f"{start_date}T00:00:00Z"
        else:
            time_min = datetime.datetime.utcnow().isoformat() + 'Z'
            
        if end_date:
            time_max = f"{end_date}T23:59:59Z"
        else:
            time_max = None
        
        # Build query parameters
        query_params = {
            'calendarId': 'primary',
            'timeMin': time_min,
            'maxResults': max_results,
            'orderBy': order_by,
            'singleEvents': single_events,
            'showDeleted': include_deleted,
            'q': query
        }
        
        if time_max:
            query_params['timeMax'] = time_max
        
        events_result = service.events().list(**query_params).execute()
        events = events_result.get('items', [])
        
        if not events:
            return "No events found matching the search criteria."
            
        response = "Search Results:\n\n"
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            end = event['end'].get('dateTime', event['end'].get('date'))
            response += f"-----\n"
            response += f"EventID: {event['id']}\n"
            response += f"Title: {event.get('summary', 'No title')}\n"
            response += f"Start: {start} | End: {end}\n"
            response += f"Status: {event.get('status', 'confirmed')}\n"
            if event.get('location'):
                response += f"Location: {event['location']}\n"
            if event.get('description'):
                response += f"Content: {event['description']}\n"
            response += f"Created: {event.get('created', 'unknown')}\n"
            response += f"Updated: {event.get('updated', 'unknown')}\n"
            response += f"-----\n\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can serve the provided mcp server with SSE."""
    sse = SseServerTransport("/google-calendar/messages/")

    async def handle_sse(request: StarletteRequest) -> None:
        async with sse.connect_sse(
                request.scope,
                request.receive,
                request._send,  # noqa: SLF001
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=debug,
        routes=[
            Route("/google-calendar/sse", endpoint=handle_sse),
            Mount("/google-calendar/messages/", app=sse.handle_post_message),
        ],
    )

async def run_fastapi_server(host: str = "0.0.0.0", port: int = FASTAPI_PORT):
    """Run the FastAPI server."""
    config = uvicorn.Config(fastapi_app, host=host, port=port)
    server = uvicorn.Server(config)
    await server.serve()

async def run_mcp_server(host: str = "0.0.0.0", port: int = MCP_PORT):
    """Run the MCP server."""
    mcp_server = app._mcp_server  # noqa: WPS437
    starlette_app = create_starlette_app(mcp_server, debug=True)
    config = uvicorn.Config(starlette_app, host=host, port=port)
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    """Run both FastAPI and MCP servers concurrently."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run Google Calendar MCP and FastAPI servers')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--fastapi-port', type=int, default=FASTAPI_PORT, help='Port for FastAPI server')
    parser.add_argument('--mcp-port', type=int, default=MCP_PORT, help='Port for MCP server')
    args = parser.parse_args()

    # Create tasks for both servers
    fastapi_task = asyncio.create_task(run_fastapi_server(args.host, args.fastapi_port))
    mcp_task = asyncio.create_task(run_mcp_server(args.host, args.mcp_port))

    # Run both servers concurrently
    await asyncio.gather(fastapi_task, mcp_task)

if __name__ == "__main__":
    asyncio.run(main()) 