import os
import json
import datetime
import secrets
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
from google_services.auth.google_auth import GoogleUnifiedAuth, GMAIL_SCOPE
from typing import Sequence
from starlette.routing import BaseRoute

# Initialize FastMCP server
app = FastMCP('google-gmail')

class GmailAPI:
    """
    Gmail API interface.
    
    This class provides methods to interact with Gmail API.
    """
    
    def __init__(self):
        """Initialize the Gmail API interface."""
        self.auth = GoogleUnifiedAuth()
    
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
        # Get schema definition for this operation
        
        creds, auth_url = self.auth.authenticate(session_id, scope=GMAIL_SCOPE)
        
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
        # Get schema definition for this operation
        
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
        # Get schema definition for this operation
        
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
            
    async def send_email(
        self,
        session_id: str,
        recipient: str,
        body: str,
        subject: str = "",
        cc: List[str] = None,
        bcc: List[str] = None
    ) -> Dict[str, Any]:
        """
        Send an email on behalf of the user.
        
        Args:
            session_id: Session ID for authentication
            recipient: Email address of the recipient
            body: Body content of the email
            subject: Subject of the email
            cc: List of CC recipients
            bcc: List of BCC recipients
            
        Returns:
            Dict with status and message information
        """
        # Get schema definition for this operation
        
        creds, auth_url = self.auth.authenticate(session_id)
        
        if not creds:
            return {
                "status": "unauthenticated", 
                "auth_url": auth_url
            }
        
        try:
            service = build('gmail', 'v1', credentials=creds)
            
            # Create email message
            message = self._create_message(
                sender="me",  # Use authenticated user
                to=recipient,
                subject=subject,
                message_text=body,
                cc=cc or [],
                bcc=bcc or []
            )
            
            # Send the message
            sent_message = service.users().messages().send(
                userId="me", 
                body=message
            ).execute()
            
            return {
                "status": "success",
                "message_id": sent_message.get("id"),
                "thread_id": sent_message.get("threadId")
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _create_message(
        self,
        sender: str,
        to: str,
        subject: str,
        message_text: str,
        cc: List[str] = None,
        bcc: List[str] = None
    ) -> Dict[str, Any]:
        """
        Create an email message for sending.
        
        Args:
            sender: Email sender
            to: Email recipient
            subject: Email subject
            message_text: Email body text
            cc: List of CC recipients
            bcc: List of BCC recipients
            
        Returns:
            Dict with raw base64 encoded email
        """
        from email.mime.text import MIMEText
        import base64
        
        message = MIMEText(message_text)
        message['to'] = to
        message['from'] = sender
        message['subject'] = subject
        
        if cc:
            message['cc'] = ", ".join(cc)
        if bcc:
            message['bcc'] = ", ".join(bcc)
            
        # Encode as base64 URL-safe string
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        return {'raw': raw_message}

# Create a shared API instance
gmail_api = GmailAPI()

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

    auth = GoogleUnifiedAuth()
    creds, auth_url = auth.authenticate(session_id, scope=GMAIL_SCOPE)
    
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

@app.tool()
async def send_email(
    session_id: str,
    recipient: str,
    body: str,
    subject: str = "",
    cc: List[str] = None,
    bcc: List[str] = None
) -> str:
    """
    Send an email through Gmail on behalf of the user.
    
    Usage:
    - Send emails from your Gmail account
    - Compose and send new messages
    - Communicate with others via email
    
    Parameters:
        session_id (str): Unique identifier for the user's session
        recipient (str): Email address of the recipient
        body (str): Content of the email
        subject (str, optional): Subject line of the email
        cc (list, optional): List of email addresses to CC
        bcc (list, optional): List of email addresses to BCC
        
    Returns:
        str: Text response with send status
        - If successful: "Email sent successfully! Message ID: [id]"
        - If unauthenticated: "Status: Unauthenticated\nPlease authenticate here: [auth_url]"
        - If error: "Error: [error_message]"
        
    Example:
        result = await send_email(
            "abc123",
            "recipient@example.com",
            "Hello, this is a test email.",
            "Test Email",
            cc=["cc@example.com"]
        )
        # Sends an email and returns status
    """
    if not session_id:
        return "Error: Session ID is required"
    
    if not recipient:
        return "Error: Recipient email is required"
    
    if not body:
        return "Error: Email body is required"

    result = await gmail_api.send_email(
        session_id=session_id,
        recipient=recipient,
        body=body,
        subject=subject,
        cc=cc,
        bcc=bcc
    )
    
    if result.get("status") == "unauthenticated":
        return f"Status: Unauthenticated\nPlease authenticate here: {result.get('auth_url')}"
    
    if result.get("status") == "error":
        return f"Error: {result.get('error')}"
    
    return f"Email sent successfully! Message ID: {result.get('message_id')}"

def route_mcp() -> Sequence[BaseRoute]:
    """Create routes for the Gmail MCP server with SSE."""
    route = "gmail"
    sse = SseServerTransport(f"/{route}/messages/")

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
        Route(f"/{route}/sse", endpoint=handle_sse),
        Mount(f"/{route}/messages/", app=sse.handle_post_message),
    ]