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
    timezone: str = "Asia/Jakarta",
    add_google_meet: bool = False,
    attendees: Optional[List[Dict]] = None,
    send_notifications: bool = True
) -> Dict:
    """
    Create a new calendar event with optional Google Meet integration.
    
    Usage:
    - Schedule new meetings or appointments
    - Add events to user's calendar
    - Set up recurring events
    - Create Google Meet meetings
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
            - reminders (dict, optional): Reminder settings
                - useDefault (bool): Whether to use default reminders
                - overrides (list, optional): Custom reminders
                    - method (str): "email" or "popup"
                    - minutes (int): Minutes before event
            - recurrence (list, optional): List of recurrence rules
                Example: ["RRULE:FREQ=WEEKLY;COUNT=5"]
        timezone (str): Timezone for the event (default: "Asia/Jakarta")
        add_google_meet (bool): Whether to add Google Meet to the event
        attendees (list, optional): List of attendee objects
            - email (str): Email address
            - displayName (str, optional): Display name
            - responseStatus (str, optional): "accepted", "declined", "tentative"
        send_notifications (bool): Whether to send email notifications to attendees
        
    Returns:
        dict: Created event details including Meet link if added
    """
    if not session_id:
        return {"error": "Session ID is required"}

    print("here is the event data", event_data)
    # Validate event data
    try:
        validated_event = CalendarEvent(**event_data)
        event_data = validated_event.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Invalid event data: {str(e)}"}
    print("here is the event data 2")

    # Validate attendees if provided
    if attendees:
        try:
            validated_attendees = [EventAttendee(**attendee) for attendee in attendees]
            attendees = [attendee.model_dump(exclude_none=True) for attendee in validated_attendees]
        except Exception as e:
            return {"error": f"Invalid attendee data: {str(e)}"}

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, CALENDAR_SCOPE)
    print("here is the event data 3")
    
    if not creds:
        return {"error": "Unauthenticated", "auth_url": auth_url}
    
    try:
        print("here is the event data 4")
        service = build('calendar', 'v3', credentials=creds)
        print("here is the event data 5")
        # Prepare event data
        event_body = event_data.copy()
        
        # Add Google Meet if requested
        if add_google_meet:
            event_body['conferenceData'] = {
                'createRequest': {
                    'requestId': f"meet_{secrets.token_hex(16)}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        
        # Add attendees if provided
        if attendees:
            event_body['attendees'] = attendees
        
        # Set timezone
        event_body['timeZone'] = timezone
        
        # Create event
        event = service.events().insert(
            calendarId='primary',
            body=event_body,
            conferenceDataVersion=1 if add_google_meet else 0,
            sendUpdates='all' if send_notifications else 'none'
        ).execute()
        
        # Format response
        response = {
            'id': event['id'],
            'summary': event.get('summary', 'No title'),
            'start': event['start'].get('dateTime', event['start'].get('date')),
            'end': event['end'].get('dateTime', event['end'].get('date')),
            'timezone': timezone,
            'description': event.get('description', ''),
            'location': event.get('location', ''),
            'attendees': event.get('attendees', []),
            'meet_link': event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', '')
        }
        
        return response
    except Exception as e:
        return {"error": str(e)}

@app.tool()
async def update_calendar_event(
    session_id: str,
    event_id: str,
    event_data: Dict,
    timezone: str = "Asia/Jakarta",
    add_google_meet: bool = False,
    attendees: Optional[List[Dict]] = None,
    send_notifications: bool = True
) -> Dict:
    """
    Update an existing calendar event with optional Google Meet integration.
    
    Usage:
    - Modify event details
    - Add or remove attendees
    - Add Google Meet to existing event
    - Update event time or location
    - Change reminder settings
    - Update recurrence rules
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        event_id (str): ID of the event to update
        event_data (dict): Updated event details including:
            - summary (str, optional): Event title
            - description (str, optional): Event description
            - start (dict, optional): Start time with dateTime or date
            - end (dict, optional): End time with dateTime or date
            - location (str, optional): Event location
            - reminders (dict, optional): Reminder settings
            - recurrence (list, optional): List of recurrence rules
        timezone (str): Timezone for the event (default: "Asia/Jakarta")
        add_google_meet (bool): Whether to add Google Meet to the event
        attendees (list, optional): List of attendee objects
            - email (str): Email address
            - displayName (str, optional): Display name
            - responseStatus (str, optional): "accepted", "declined", "tentative"
        send_notifications (bool): Whether to send email notifications to attendees
        
    Returns:
        dict: Updated event details including Meet link if added
    """
    if not session_id:
        return {"error": "Session ID is required"}

    # Validate event data
    try:
        validated_event = CalendarEvent(**event_data)
        event_data = validated_event.model_dump(exclude_none=True)
    except Exception as e:
        return {"error": f"Invalid event data: {str(e)}"}

    # Validate attendees if provided
    if attendees:
        try:
            validated_attendees = [EventAttendee(**attendee) for attendee in attendees]
            attendees = [attendee.model_dump(exclude_none=True) for attendee in validated_attendees]
        except Exception as e:
            return {"error": f"Invalid attendee data: {str(e)}"}

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
        
        # Prepare update data
        update_data = current_event.copy()
        update_data.update(event_data)
        
        # Add Google Meet if requested
        if add_google_meet and 'conferenceData' not in update_data:
            update_data['conferenceData'] = {
                'createRequest': {
                    'requestId': f"meet_{secrets.token_hex(16)}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            }
        
        # Update attendees if provided
        if attendees:
            update_data['attendees'] = attendees
        
        # Set timezone
        update_data['timeZone'] = timezone
        
        # Update event
        event = service.events().update(
            calendarId='primary',
            eventId=event_id,
            body=update_data,
            conferenceDataVersion=1 if add_google_meet else 0,
            sendUpdates='all' if send_notifications else 'none'
        ).execute()
        
        # Format response
        response = {
            'id': event['id'],
            'summary': event.get('summary', 'No title'),
            'start': event['start'].get('dateTime', event['start'].get('date')),
            'end': event['end'].get('dateTime', event['end'].get('date')),
            'timezone': timezone,
            'description': event.get('description', ''),
            'location': event.get('location', ''),
            'attendees': event.get('attendees', []),
            'meet_link': event.get('conferenceData', {}).get('entryPoints', [{}])[0].get('uri', '')
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
        details = await get_calendar_details("abc123")
        # Returns details of the primary calendar
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
