import os
import json
import datetime
import secrets
import asyncio
from typing import List, Dict, Optional, Tuple, Union
from pydantic import BaseModel, Field, field_validator
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
from google_services.auth.google_auth import GoogleUnifiedAuth, CALENDAR_SCOPE

# Initialize FastMCP server
app = FastMCP('google-calendar')

# Schema definitions
class EventTime(BaseModel):
    dateTime: Optional[str] = None
    date: Optional[str] = None
    timeZone: Optional[str] = None

class EventReminder(BaseModel):
    method: str = Field(..., pattern='^(email|popup)$')
    minutes: int = Field(..., gt=0)

class EventReminders(BaseModel):
    useDefault: bool = True
    overrides: Optional[List[EventReminder]] = None

class EventAttendee(BaseModel):
    email: str
    displayName: Optional[str] = None
    responseStatus: Optional[str] = Field(None, pattern='^(accepted|declined|tentative)$')

class CalendarEvent(BaseModel):
    summary: str
    description: Optional[str] = None
    start: EventTime
    end: EventTime
    location: Optional[str] = None
    reminders: Optional[EventReminders] = None
    recurrence: Optional[List[str]] = None
    attendees: Optional[List[EventAttendee]] = None
    conferenceData: Optional[Dict] = None

    @field_validator('start', 'end')
    @classmethod
    def validate_time(cls, v):
        if not v.dateTime and not v.date:
            raise ValueError('Either dateTime or date must be provided')
        return v

@app.tool()
async def get_auth_status_calender(session_id: str) -> str:
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

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
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

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
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
            
        return events
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def create_calendar_event(
    session_id: str,
    event_data: Dict,
) -> Dict:
    """
    Create a new calendar event with comprehensive event details and optional features.
    
    Usage:
    - Schedule meetings, appointments, or any calendar events
    - Create events with detailed information
    - Set up recurring events
    - Add Google Meet integration
    - Manage event attendees and their responses
    - Configure custom reminders
    - Set event visibility and access controls
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_data (dict): Complete event details including:
            - summary (str): Event title/name
            - description (str, optional): Detailed event description
            - start (dict): Start time information
                - dateTime (str): ISO 8601 datetime (e.g., "2024-03-20T10:00:00Z")
                - timeZone (str): Timezone (e.g., "Asia/Jakarta")
            - end (dict): End time information
                - dateTime (str): ISO 8601 datetime
                - timeZone (str): Timezone
            - location (str, optional): Physical or virtual location
            - reminders (dict, optional): Reminder settings
                - useDefault (bool): Use default reminders
                - overrides (list): Custom reminders
                    - method (str): "email" or "popup"
                    - minutes (int): Minutes before event
            - recurrence (list, optional): Recurrence rules
                Example: ["RRULE:FREQ=WEEKLY;COUNT=5"]
            - attendees (list, optional): List of attendees
                - email (str): Email address
                - displayName (str, optional): Display name
                - responseStatus (str, optional): "accepted", "declined", "tentative"
            - conferenceData (dict, optional): Google Meet settings
                - createRequest (dict): Request to create a conference link.
                    - requestId (str): Unique request ID for creating the conference.
            - visibility (str, optional): "default", "public", "private"
            - guestsCanModify (bool, optional): Whether guests can modify
            - guestsCanInviteOthers (bool, optional): Whether guests can invite others
            - guestsCanSeeOtherGuests (bool, optional): Whether guests can see other guests
    
    Returns:
        dict: Created event details including:
            - id (str): Event ID
            - summary (str): Event title
            - start (str): Start time
            - end (str): End time
            - timezone (str): Event timezone
            - description (str): Event description
            - location (str): Event location
            - attendees (list): List of attendees
            - meet_link (str): Google Meet link if added
            - status (str): Event status
            - created (str): Creation timestamp
            - updated (str): Last update timestamp
    
    Example:
        event_data = {
            "summary": "Team Meeting",
            "description": "Weekly team sync",
            "start": {
                "dateTime": "2024-03-20T10:00:00Z",
                "timeZone": "Asia/Jakarta"
            },
            "end": {
                "dateTime": "2024-03-20T11:00:00Z",
                "timeZone": "Asia/Jakarta"
            },
            "attendees": [
                {"email": "colleague@example.com"}
            ],
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "email", "minutes": 30}
                ]
            }
        }
        result = await create_calendar_event("abc123", event_data)
    """
    if not session_id:
        return {"error": "Session ID is required"}

    # Validate event data
    try:
        validated_event = CalendarEvent(**event_data)
        event_data = validated_event.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Invalid event data: {str(e)}"}

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
    if not creds:
        return {"error": "Unauthenticated", "auth_url": auth_url}
    
    
    try:
        service = build('calendar', 'v3', credentials=creds)

        if 'conferenceData' in event_data:
            event_data['conferenceData']['createRequest']['requestId'] = f"mcp-{secrets.token_hex(16)}"
            event_data['conferenceData']['createRequest']['conferenceType'] = 'hangoutsMeet'

        print(event_data)
        
        # Create event
        event = service.events().insert(
            calendarId='primary',
            body=event_data,
            conferenceDataVersion=1 if 'conferenceData' in event_data else 0,
            sendUpdates='all' if 'attendees' in event_data else 'none'
        ).execute()
        
        # Format response
        response = {
            'id': event['id'],
            'summary': event.get('summary', 'No title'),
            'start': event['start'].get('dateTime', event['start'].get('date')),
            'end': event['end'].get('dateTime', event['end'].get('date')),
            'timezone': event.get('timeZone', 'UTC'),
            'description': event.get('description', ''),
            'location': event.get('location', ''),
            'attendees': event.get('attendees', []),
            'meet_link': event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', ''),
            'status': event.get('status', 'confirmed'),
            'created': event.get('created', ''),
            'updated': event.get('updated', '')
        }
        
        return response
    except Exception as e:
        return {"error": str(e)}

@app.tool()
async def update_calendar_event(
    session_id: str,
    event_id: str,
    event_data: Dict,
) -> Dict:
    """
    Update an existing calendar event with comprehensive event details and optional features.
    
    Usage:
    - Modify existing event details
    - Update event timing
    - Change event location
    - Add or remove attendees
    - Update reminder settings
    - Modify recurrence rules
    - Add or remove Google Meet
    - Change event visibility
    - Update access controls
    
    Workflow:
    1. First, list events to find the event ID you want to update
    2. Get the current event details if needed
    3. Prepare the update data with only the fields you want to change
    4. Call update_calendar_event with the event ID and update data
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_id (str): ID of the event to update
        event_data (dict): Updated event details including:
            - summary (str, optional): New event title
            - description (str, optional): New event description
            - start (dict, optional): New start time
                - dateTime (str): ISO 8601 datetime
                - timeZone (str): Timezone
            - end (dict, optional): New end time
                - dateTime (str): ISO 8601 datetime
                - timeZone (str): Timezone
            - location (str, optional): New location
            - reminders (dict, optional): New reminder settings
            - recurrence (list, optional): New recurrence rules
            - attendees (list, optional): Updated attendee list
            - conferenceData (dict, optional): Google Meet settings
            - visibility (str, optional): New visibility setting
            - guestsCanModify (bool, optional): New guest modification setting
            - guestsCanInviteOthers (bool, optional): New guest invitation setting
            - guestsCanSeeOtherGuests (bool, optional): New guest visibility setting
    
    Returns:
        dict: Updated event details including:
            - id (str): Event ID
            - summary (str): Updated event title
            - start (str): Updated start time
            - end (str): Updated end time
            - timezone (str): Event timezone
            - description (str): Updated description
            - location (str): Updated location
            - attendees (list): Updated attendee list
            - meet_link (str): Google Meet link if present
            - status (str): Event status
            - created (str): Creation timestamp
            - updated (str): Last update timestamp
    
    Example:
        # Workflow example:
        # 1. List events to find the event ID
        events = await list_calendar_events("abc123", start_date="2024-03-01", end_date="2024-03-31")
        # 2. Find the specific event
        target_event = next((e for e in events if "Team Meeting" in e.get('summary', '')), None)
        if target_event:
            # 3. Prepare update data
            update_data = {
                "summary": "Updated Team Meeting",
                "start": {
                    "dateTime": "2024-03-21T11:00:00Z",
                    "timeZone": "Asia/Jakarta"
                },
                "end": {
                    "dateTime": "2024-03-21T12:00:00Z",
                    "timeZone": "Asia/Jakarta"
                },
                "attendees": [
                    {"email": "newcolleague@example.com"}
                ]
            }
            # 4. Update the event
            result = await update_calendar_event("abc123", target_event.get('id'), update_data)
    """
    if not session_id:
        return {"error": "Session ID is required"}
    
    if not event_id:
        return {"error": "Event ID is required"}

    # Validate event data
    try:
        validated_event = CalendarEvent(**event_data)
        event_data = validated_event.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Invalid event data: {str(e)}"}

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
    if not creds:
        return {"error": "Unauthenticated", "auth_url": auth_url}
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Get current event
        current_event = service.events().get(
            calendarId='primary',
            eventId=event_id
        ).execute()
        
        # Merge current event with updates
        updated_event = current_event.copy()
        if 'conferenceData' not in current_event and 'conferenceData' in event_data:
            event_data['conferenceData'] = {
                'createRequest': {
                    'requestId': f"mcp-{secrets.token_hex(16)}",
                    'conferenceType': 'hangoutsMeet'
                }
            }
        updated_event.update(event_data)
        
        # Update event
        event = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=updated_event,
            conferenceDataVersion=1 if 'conferenceData' in updated_event else 0,
            sendUpdates='all' if 'attendees' in updated_event else 'none'
        ).execute()
        
        # Format response
        response = {
            'id': event['id'],
            'summary': event.get('summary', 'No title'),
            'start': event['start'].get('dateTime', event['start'].get('date')),
            'end': event['end'].get('dateTime', event['end'].get('date')),
            'timezone': event.get('timeZone', 'UTC'),
            'description': event.get('description', ''),
            'location': event.get('location', ''),
            'attendees': event.get('attendees', []),
            'meet_link': event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', ''),
            'status': event.get('status', 'confirmed'),
            'created': event.get('created', ''),
            'updated': event.get('updated', '')
        }
        
        return response
    except Exception as e:
        return {"error": str(e)}

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
    
    Workflow:
    1. First, list events to find the event ID you want to delete
    2. Verify the event details to ensure it's the correct one
    3. Call delete_calendar_event with the event ID
    4. Confirm the deletion was successful
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_id (str): ID of the event to delete
        
    Returns:
        str: Success message or error
        Format:
        -----
        Status: [status]
        Event ID: [id]
        Message: [message]
        -----
        
    Example:
        # Workflow example:
        # 1. List events to find the event ID
        events = await list_calendar_events("abc123", start_date="2024-03-01", end_date="2024-03-31")
        # 2. Find the specific event
        target_event = next((e for e in events if "Team Meeting" in e.get('summary', '')), None)
        if target_event:
            # 3. Delete the event
            result = await delete_calendar_event("abc123", target_event.get('id'))
            # 4. Verify deletion
            if "successfully" in result:
                print("Event deleted successfully")
            else:
                print("Failed to delete event:", result)
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
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

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
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

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
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

@app.tool()
async def get_calendar_details(
    session_id: str,
    calendar_id: str = "primary"
) -> str:
    """
    Get detailed information about a specific calendar.
    
    Usage:
    - View calendar settings and properties
    - Check calendar access permissions
    - Get calendar metadata
    - Verify calendar configuration
    
    Workflow:
    1. First, get list of calendars using list_calendars()
    2. Note the calendar ID you want to inspect
    3. Use get_calendar_details() with that ID
    4. Review the calendar settings and permissions
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        calendar_id (str): ID of the calendar to get details for (default: "primary")
        
    Returns:
        str: Formatted text response with calendar details
        Format:
        -----
        Calendar ID: [id]
        Summary: [name]
        Description: [description]
        Timezone: [timezone]
        Primary: [is_primary]
        Access Role: [access_role]
        Background Color: [bg_color]
        Foreground Color: [fg_color]
        -----
        
    Example:
        # Get details of primary calendar
        details = await get_calendar_details("abc123")
        
        # Get details of specific calendar
        details = await get_calendar_details("abc123", "work@group.calendar.google.com")
        
        # Workflow example:
        # 1. List calendars
        calendars = await list_calendars("abc123")
        # 2. Find work calendar
        work_calendar = next((c for c in calendars if "work" in c.get('summary', '').lower()), None)
        # 3. Get its details
        if work_calendar:
            details = await get_calendar_details("abc123", work_calendar.get('id'))
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        calendar = service.calendars().get(calendarId=calendar_id).execute()
        
        return calendar
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def update_event_attendance(
    session_id: str,
    event_id: str,
    response: str = "accepted",
    comment: Optional[str] = None
) -> str:
    """
    Update your attendance status for a calendar event.
    
    Usage:
    - Accept event invitations
    - Decline event invitations
    - Mark attendance as tentative
    - Add comments to your response
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_id (str): ID of the event to update attendance for
        response (str): Attendance response - "accepted", "declined", or "tentative"
        comment (str, optional): Optional comment to include with your response
        
    Returns:
        str: Success message or error
        Format:
        -----
        Status: [status]
        Event: [event_title]
        Response: [your_response]
        Comment: [your_comment]
        -----
        
    Example:
        # Accept an event
        result = await update_event_attendance("abc123", "event123", "accepted")
        
        # Decline with comment
        result = await update_event_attendance(
            "abc123",
            "event123",
            "declined",
            "I have a conflicting meeting"
        )
    """
    if not session_id:
        return "Error: Session ID is required"
        
    if response not in ["accepted", "declined", "tentative"]:
        return "Error: Response must be 'accepted', 'declined', or 'tentative'"

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Get the event first to check if it exists and get the title
        event = service.events().get(
            calendarId='primary',
            eventId=event_id
        ).execute()
        
        # Update attendance
        updated_event = service.events().patch(
            calendarId='primary',
            eventId=event_id,
            body={
                'attendees': [
                    {
                        'email': creds.id_token['email'],
                        'responseStatus': response
                    }
                ]
            }
        ).execute()
        
        response_text = "Attendance Updated:\n\n"
        response_text += f"-----\n"
        response_text += f"Status: Success\n"
        response_text += f"Event: {event.get('summary', 'Untitled Event')}\n"
        response_text += f"Response: {response}\n"
        if comment:
            response_text += f"Comment: {comment}\n"
        response_text += f"-----\n"
        
        return response_text
    except Exception as e:
        return f"Error: {str(e)}"

@app.tool()
async def list_calendars(
    session_id: str,
    max_results: int = 100,
    min_access_role: Optional[str] = None,
    show_deleted: bool = False,
    show_hidden: bool = False
) -> str:
    """
    List calendars in the user's calendar list.
    
    Usage:
    - View all accessible calendars
    - Find calendar IDs for use in other operations
    - Check calendar access levels
    - Get primary calendar information
    
    Workflow:
    1. First, get list of calendars to find the calendar ID you need
    2. Use the calendar ID in other operations like listing events or creating events
    3. For primary calendar operations, use 'primary' as the calendar ID
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        max_results (int, optional): Maximum number of results to return (default: 100)
        min_access_role (str, optional): Minimum access role for returned entries
        show_deleted (bool, optional): Whether to include deleted entries (default: False)
        show_hidden (bool, optional): Whether to show hidden entries (default: False)
        
    Returns:
        str: Formatted text response with calendar list
        Format:
        -----
        ID: [id]
        Summary: [name]
        Description: [description]
        Access Role: [access_role]
        Primary: [is_primary]
        -----
        
    Example:
        # Get list of all calendars
        calendars = await list_calendars("abc123")
        
        # Get only calendars where user is owner
        calendars = await list_calendars("abc123", min_access_role="owner")
        
        # Get primary calendar ID
        calendars = await list_calendars("abc123")
        primary_calendar = next((c for c in calendars if c.get('primary')), None)
        primary_id = primary_calendar.get('id') if primary_calendar else 'primary'
    """
    if not session_id:
        return "Error: Session ID is required"

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    
    if not creds:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"
    
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Build query
        query = {
            'calendarId': 'primary',
            'maxResults': max_results,
            'showDeleted': show_deleted,
            'showHidden': show_hidden,
            'minAccessRole': min_access_role
        }
        
        calendars_result = service.calendars().list(**query).execute()
        calendars = calendars_result.get('items', [])
        
        if not calendars:
            return "No calendars found in the specified criteria."
            
        response = "Calendars:\n\n"
        for calendar in calendars:
            response += f"-----\n"
            response += f"ID: {calendar['id']}\n"
            response += f"Summary: {calendar['summary']}\n"
            response += f"Description: {calendar['description']}\n"
            response += f"Access Role: {calendar['accessRole']}\n"
            response += f"Primary: {calendar['primary']}\n"
            response += f"-----\n\n"
        
        return response
    except Exception as e:
        return f"Error: {str(e)}"

def route_mcp(debug: bool = False):
    """Create a Starlette application that can serve the provided mcp server with SSE."""
    sse = SseServerTransport("/gcalendar/messages/")

    async def handle_sse(request: StarletteRequest) -> None:
        async with sse.connect_sse(
                request.scope,
                request.receive,
                request._send,  # noqa: SLF001
        ) as (read_stream, write_stream):
            await app._mcp_server.run(
                read_stream,
                write_stream,
                app._mcp_server.create_initialization_options(),
            )

    return [
        Route("/gcalendar/sse", endpoint=handle_sse),
        Mount("/gcalendar/messages/", app=sse.handle_post_message),
    ]
