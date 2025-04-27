import os
import json
import datetime
import secrets
import asyncio
import base64
from typing import List, Dict, Optional, Tuple, Any
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
fastapi_app = FastAPI(title="Google Gmail OAuth")

# Initialize FastMCP server
app = FastMCP('google-gmail')

# OAuth configuration
REDIRECT_URI = "https://oauth.kiyora.dev/oauth/callback/google-gmail"
OAUTH_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# MCP tools
FASTAPI_PORT = 8001
MCP_PORT = 8081

# Templates
templates = Jinja2Templates(directory="templates")

class GoogleGmailAuth:
    def __init__(self, credentials_file: str = 'credentials.json', token_file: str = 'gmail_tokens.json', sessions_file: str = 'gmail_sessions.json'):
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

class GmailAPI:
    """
    Gmail API interface.
    
    This class provides methods to interact with Gmail API.
    """
    
    def __init__(self):
        """Initialize the Gmail API interface."""
        self.auth = GoogleGmailAuth()
    
    async def list_messages(
        self, 
        session_id: str, 
        query: str = "",
        max_results: int = 10,
        include_labels: bool = True
    ) -> Dict[str, Any]:
        """
        List messages in the user's Gmail based on query.
        
        Args:
            session_id: Session ID for authentication
            query: Gmail search query
            max_results: Maximum number of results
            include_labels: Whether to include labels in results
            
        Returns:
            Dict containing message list results
        """
        creds, auth_url = self.auth.authenticate(session_id)
        
        if not creds:
            return {
                "status": "unauthenticated",
                "auth_url": auth_url
            }
        
        try:
            service = build('gmail', 'v1', credentials=creds)
            
            # List messages
            results = service.users().messages().list(
                userId='me',
                q=query,
                maxResults=max_results
            ).execute()
            
            messages = results.get('messages', [])
            
            if not messages:
                return {
                    "status": "success",
                    "messages": []
                }
            
            # Get detailed message info
            detailed_messages = []
            for msg in messages:
                message_id = msg['id']
                message = service.users().messages().get(
                    userId='me', 
                    id=message_id,
                    format='metadata' if not include_labels else 'full'
                ).execute()
                
                # Process headers to extract common fields
                headers = {}
                if 'payload' in message and 'headers' in message['payload']:
                    for header in message['payload']['headers']:
                        name = header['name'].lower()
                        if name in ['from', 'to', 'subject', 'date']:
                            headers[name] = header['value']
                
                # Create message summary
                message_summary = {
                    'id': message['id'],
                    'threadId': message['threadId'],
                    'snippet': message.get('snippet', ''),
                    'date': headers.get('date', 'Unknown date'),
                    'from': headers.get('from', 'Unknown sender'),
                    'to': headers.get('to', 'Unknown recipient'),
                    'subject': headers.get('subject', 'No subject')
                }
                
                if include_labels:
                    message_summary['labels'] = message.get('labelIds', [])
                
                detailed_messages.append(message_summary)
            
            return {
                "status": "success",
                "messages": detailed_messages,
                "nextPageToken": results.get('nextPageToken')
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def get_message(
        self, 
        session_id: str, 
        message_id: str,
        format: str = 'full'
    ) -> Dict[str, Any]:
        """
        Get a specific message with all details.
        
        Args:
            session_id: Session ID for authentication
            message_id: ID of the message to retrieve
            format: Format to return the message in (full, minimal, or raw)
            
        Returns:
            Dict containing message details
        """
        creds, auth_url = self.auth.authenticate(session_id)
        
        if not creds:
            return {
                "status": "unauthenticated",
                "auth_url": auth_url
            }
        
        try:
            service = build('gmail', 'v1', credentials=creds)
            
            # Get message
            message = service.users().messages().get(
                userId='me',
                id=message_id,
                format=format
            ).execute()
            
            # Extract headers
            headers = {}
            body_text = ""
            body_html = ""
            attachments = []
            
            if 'payload' in message:
                # Process headers
                if 'headers' in message['payload']:
                    for header in message['payload']['headers']:
                        name = header['name'].lower()
                        if name in ['from', 'to', 'subject', 'date', 'cc', 'bcc']:
                            headers[name] = header['value']
                
                # Process parts to get body and attachments
                def process_parts(parts, level=0):
                    nonlocal body_text, body_html, attachments
                    
                    for part in parts:
                        if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                            data = part['body']['data']
                            body_text += base64.urlsafe_b64decode(data).decode('utf-8')
                        
                        elif part.get('mimeType') == 'text/html' and 'data' in part.get('body', {}):
                            data = part['body']['data']
                            body_html += base64.urlsafe_b64decode(data).decode('utf-8')
                        
                        elif part.get('mimeType', '').startswith('image/') or part.get('filename'):
                            attachment = {
                                'id': part.get('body', {}).get('attachmentId'),
                                'filename': part.get('filename'),
                                'mimeType': part.get('mimeType'),
                                'size': part.get('body', {}).get('size', 0)
                            }
                            if attachment['id']:  # Only add if it has an attachment ID
                                attachments.append(attachment)
                        
                        # Recursive processing of nested parts
                        if 'parts' in part:
                            process_parts(part['parts'], level + 1)
                
                # Handle single part
                if 'body' in message['payload'] and 'data' in message['payload']['body']:
                    data = message['payload']['body']['data']
                    body_text = base64.urlsafe_b64decode(data).decode('utf-8')
                
                # Handle multipart
                if 'parts' in message['payload']:
                    process_parts(message['payload']['parts'])
            
            # Prepare the response
            response = {
                "status": "success",
                "id": message['id'],
                "threadId": message['threadId'],
                "snippet": message.get('snippet', ''),
                "labels": message.get('labelIds', []),
                "headers": headers,
                "body": {
                    "text": body_text,
                    "html": body_html
                },
                "attachments": attachments,
                "internalDate": message.get('internalDate'),
                "sizeEstimate": message.get('sizeEstimate', 0)
            }
            
            return response
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    async def get_labels(self, session_id: str) -> Dict[str, Any]:
        """
        Get all labels in the user's Gmail account.
        
        Args:
            session_id: Session ID for authentication
            
        Returns:
            Dict containing labels
        """
        creds, auth_url = self.auth.authenticate(session_id)
        
        if not creds:
            return {
                "status": "unauthenticated",
                "auth_url": auth_url
            }
        
        try:
            service = build('gmail', 'v1', credentials=creds)
            
            # Get labels
            results = service.users().labels().list(userId='me').execute()
            
            labels = results.get('labels', [])
            
            return {
                "status": "success",
                "labels": labels
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    async def get_attachment(
        self, 
        session_id: str, 
        message_id: str,
        attachment_id: str
    ) -> Dict[str, Any]:
        """
        Get a specific attachment from a message.
        
        Args:
            session_id: Session ID for authentication
            message_id: ID of the message containing the attachment
            attachment_id: ID of the attachment to retrieve
            
        Returns:
            Dict containing attachment data
        """
        creds, auth_url = self.auth.authenticate(session_id)
        
        if not creds:
            return {
                "status": "unauthenticated",
                "auth_url": auth_url
            }
        
        try:
            service = build('gmail', 'v1', credentials=creds)
            
            # Get attachment
            attachment = service.users().messages().attachments().get(
                userId='me',
                messageId=message_id,
                id=attachment_id
            ).execute()
            
            # Decode attachment data
            data = attachment['data']
            file_data = base64.urlsafe_b64decode(data)
            
            return {
                "status": "success",
                "data": base64.b64encode(file_data).decode('utf-8'),
                "size": attachment.get('size', 0)
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

# Create a shared API instance
gmail_api = GmailAPI()

@fastapi_app.get("/oauth/callback/google-gmail")
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
        auth = GoogleGmailAuth()
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
    auth = GoogleGmailAuth()
    session = auth.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "status": session['status'],
        "email": session.get('email', 'Unknown')
    }

@fastapi_app.get("/oauth/start")
async def start_auth(request: StarletteRequest):
    """
    Start OAuth flow for a user.
    """
    auth = GoogleGmailAuth()
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
    Check the authentication status for Gmail access and provide OAuth URL if needed.
    
    Usage:
    - First step in the authentication flow
    - Check if a user is already authenticated
    - Get authentication URL if needed
    
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

    auth = GoogleGmailAuth()
    creds, auth_url = auth.authenticate(session_id)
    
    if creds:
        return "Status: Authenticated"
    else:
        return f"Status: Unauthenticated\nPlease authenticate here: {auth_url}"

@app.tool()
async def list_emails(
    session_id: str,
    query: str = "",
    max_results: int = 10,
    include_labels: bool = True
) -> str:
    """
    List emails from Gmail based on search query.
    
    Usage:
    - Search for emails in Gmail
    - List recent emails
    - Find emails by sender, subject, or content
    - Filter emails using Gmail search operators
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        query (str, optional): Gmail search query (same format as Gmail search box)
                              Examples: "from:example@gmail.com", "subject:meeting", "is:unread"
        max_results (int, optional): Maximum number of results to return (default: 10)
        include_labels (bool, optional): Include labels in results (default: True)
        
    Returns:
        str: Formatted text response with email list
        Format:
        -----
        ID: [id]
        Thread ID: [thread_id]
        From: [sender]
        Subject: [subject]
        Date: [date]
        Snippet: [short preview of content]
        Labels: [label1, label2, ...]
        -----
        
    Example:
        emails = await list_emails("abc123", "from:newsletter@example.com")
        # Returns list of emails from newsletter@example.com
    """
    if not session_id:
        return "Error: Session ID is required"

    result = await gmail_api.list_messages(session_id, query, max_results, include_labels)
    
    if result.get("status") == "unauthenticated":
        return f"Status: Unauthenticated\nPlease authenticate here: {result.get('auth_url')}"
    
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    messages = result.get("messages", [])
    
    if not messages:
        return "No emails found matching your query."
    
    response = "Gmail Messages:\n\n"
    
    for msg in messages:
        response += f"-----\n"
        response += f"ID: {msg.get('id')}\n"
        response += f"Thread ID: {msg.get('threadId')}\n"
        response += f"From: {msg.get('from', 'Unknown')}\n"
        response += f"Subject: {msg.get('subject', 'No subject')}\n"
        response += f"Date: {msg.get('date', 'Unknown date')}\n"
        response += f"Snippet: {msg.get('snippet', '')}\n"
        
        if include_labels and 'labels' in msg:
            response += f"Labels: {', '.join(msg.get('labels', []))}\n"
            
        response += f"-----\n\n"
    
    return response

@app.tool()
async def get_email(
    session_id: str,
    message_id: str
) -> str:
    """
    Get complete details of a specific email.
    
    Usage:
    - Read full email content
    - View email headers
    - Check email attachments
    - Get detailed information about a specific message
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        message_id (str): ID of the message to retrieve (from list_emails)
        
    Returns:
        str: Formatted text response with complete email details
        Format:
        -----
        ID: [id]
        Thread ID: [thread_id]
        From: [sender]
        To: [recipient]
        CC: [carbon copy recipients]
        Subject: [subject]
        Date: [date]
        Labels: [label1, label2, ...]
        
        Body:
        [full email body text]
        
        Attachments:
        - [filename1] ([mime_type], [size] bytes)
        - [filename2] ([mime_type], [size] bytes)
        -----
        
    Example:
        email = await get_email("abc123", "18af56bd92c371")
        # Returns complete details of email with ID 18af56bd92c371
    """
    if not session_id:
        return "Error: Session ID is required"
    
    if not message_id:
        return "Error: Message ID is required"

    result = await gmail_api.get_message(session_id, message_id)
    
    if result.get("status") == "unauthenticated":
        return f"Status: Unauthenticated\nPlease authenticate here: {result.get('auth_url')}"
    
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    # Format the response
    headers = result.get("headers", {})
    response = "Email Details:\n\n"
    response += f"-----\n"
    response += f"ID: {result.get('id')}\n"
    response += f"Thread ID: {result.get('threadId')}\n"
    response += f"From: {headers.get('from', 'Unknown')}\n"
    response += f"To: {headers.get('to', 'Unknown')}\n"
    
    if 'cc' in headers:
        response += f"CC: {headers.get('cc')}\n"
    
    response += f"Subject: {headers.get('subject', 'No subject')}\n"
    response += f"Date: {headers.get('date', 'Unknown date')}\n"
    
    labels = result.get("labels", [])
    if labels:
        response += f"Labels: {', '.join(labels)}\n"
    
    # Add body
    body = result.get("body", {})
    body_text = body.get("text", "").strip()
    
    if body_text:
        response += f"\nBody:\n{body_text}\n"
    else:
        response += f"\nBody: [No text content available]\n"
    
    # Add attachments
    attachments = result.get("attachments", [])
    if attachments:
        response += f"\nAttachments:\n"
        for attachment in attachments:
            size = attachment.get('size', 0)
            size_str = f"{size} bytes"
            if size > 1024:
                size_str = f"{size/1024:.1f} KB"
            if size > 1024*1024:
                size_str = f"{size/(1024*1024):.1f} MB"
                
            response += f"- {attachment.get('filename')} ({attachment.get('mimeType')}, {size_str})\n"
    
    response += f"-----\n"
    
    return response

@app.tool()
async def get_labels(session_id: str) -> str:
    """
    Get all labels from the user's Gmail account.
    
    Usage:
    - List all Gmail labels
    - Find system and custom labels
    - Get label IDs for use in search queries
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        
    Returns:
        str: Formatted text response with all labels
        Format:
        -----
        System Labels:
        - [name] (ID: [id])
        - [name] (ID: [id])
        
        User Labels:
        - [name] (ID: [id])
        - [name] (ID: [id])
        -----
        
    Example:
        labels = await get_labels("abc123")
        # Returns list of all Gmail labels
    """
    if not session_id:
        return "Error: Session ID is required"

    result = await gmail_api.get_labels(session_id)
    
    if result.get("status") == "unauthenticated":
        return f"Status: Unauthenticated\nPlease authenticate here: {result.get('auth_url')}"
    
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    labels = result.get("labels", [])
    
    if not labels:
        return "No labels found in the Gmail account."
    
    # Separate system and user labels
    system_labels = []
    user_labels = []
    
    for label in labels:
        label_type = label.get('type', '')
        if label_type == 'system':
            system_labels.append(label)
        else:
            user_labels.append(label)
    
    # Sort labels by name
    system_labels.sort(key=lambda x: x.get('name', ''))
    user_labels.sort(key=lambda x: x.get('name', ''))
    
    response = "Gmail Labels:\n\n"
    
    # Add system labels
    response += "System Labels:\n"
    for label in system_labels:
        response += f"- {label.get('name')} (ID: {label.get('id')})\n"
    
    # Add user labels if any
    if user_labels:
        response += "\nUser Labels:\n"
        for label in user_labels:
            response += f"- {label.get('name')} (ID: {label.get('id')})\n"
    
    return response

@app.tool()
async def search_emails(
    session_id: str,
    query: str,
    max_results: int = 10
) -> str:
    """
    Search for emails in Gmail using Google's search syntax.
    
    Usage:
    - Find emails with specific content
    - Search by sender, recipient, subject, or content
    - Use Gmail's powerful search operators
    - Filter emails by date, attachments, or labels
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        query (str): Gmail search query using Gmail search operators
                     Examples: 
                     - "from:example@gmail.com has:attachment"
                     - "subject:\"meeting notes\" after:2023/01/01"
                     - "is:important is:unread"
                     - "label:work filename:pdf"
        max_results (int, optional): Maximum number of results to return (default: 10)
        
    Returns:
        str: Formatted text response with matching emails
        Format:
        -----
        ID: [id]
        From: [sender]
        Subject: [subject]
        Date: [date]
        Snippet: [short preview of content]
        Labels: [label1, label2, ...]
        -----
        
    Example:
        emails = await search_emails(
            "abc123",
            "from:support@company.com has:attachment after:2023/01/01",
            max_results=5
        )
        # Returns up to 5 emails from support@company.com with attachments from 2023
    """
    # This is essentially the same as list_emails, but with a more search-focused description
    if not session_id:
        return "Error: Session ID is required"
    
    if not query:
        return "Error: Search query is required"

    result = await gmail_api.list_messages(session_id, query, max_results, include_labels=True)
    
    if result.get("status") == "unauthenticated":
        return f"Status: Unauthenticated\nPlease authenticate here: {result.get('auth_url')}"
    
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    messages = result.get("messages", [])
    
    if not messages:
        return "No emails found matching your search query."
    
    response = f"Search Results for: '{query}'\n\n"
    
    for msg in messages:
        response += f"-----\n"
        response += f"ID: {msg.get('id')}\n"
        response += f"From: {msg.get('from', 'Unknown')}\n"
        response += f"Subject: {msg.get('subject', 'No subject')}\n"
        response += f"Date: {msg.get('date', 'Unknown date')}\n"
        response += f"Snippet: {msg.get('snippet', '')}\n"
        
        if 'labels' in msg:
            response += f"Labels: {', '.join(msg.get('labels', []))}\n"
            
        response += f"-----\n\n"
    
    return response

@app.tool()
async def get_attachment(
    session_id: str,
    message_id: str,
    attachment_id: str
) -> Dict[str, Any]:
    """
    Get a specific attachment from an email.
    
    Usage:
    - Download email attachments
    - Get attachment metadata
    - Retrieve file data from emails
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        message_id (str): ID of the message (from list_emails or search_emails)
        attachment_id (str): ID of the attachment (from get_email)
        
    Returns:
        dict: Dictionary with attachment information:
        - status: "success" or "error"
        - data: Base64-encoded attachment data (if successful)
        - size: Size of the attachment in bytes (if successful)
        - error: Error message (if error)
        
    Example:
        attachment = await get_attachment("abc123", "18af56bd92c371", "attachment123")
        # Returns attachment data which can be decoded to a file
    """
    if not session_id:
        return {"status": "error", "error": "Session ID is required"}
    
    if not message_id:
        return {"status": "error", "error": "Message ID is required"}
    
    if not attachment_id:
        return {"status": "error", "error": "Attachment ID is required"}

    result = await gmail_api.get_attachment(session_id, message_id, attachment_id)
    
    return result

def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can serve the provided mcp server with SSE."""
    sse = SseServerTransport("/google-gmail/messages/")

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
            Route("/google-gmail/sse", endpoint=handle_sse),
            Mount("/google-gmail/messages/", app=sse.handle_post_message),
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
    
    parser = argparse.ArgumentParser(description='Run Google Gmail MCP and FastAPI servers')
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