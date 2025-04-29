import os
import json
import datetime
from typing import Dict, Optional, Tuple, List, Set, Union

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from google_auth_oauthlib.flow import Flow

# Available scopes
GMAIL_SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar'

# Default redirect URI
DEFAULT_REDIRECT_URI = "https://oauthlaptop.kiyora.dev/auth/callback"

class GoogleUnifiedAuth:
    def __init__(self, credentials_file: str = 'credentials.json', sessions_file: str = 'sessions.json'):
        self.credentials_file = credentials_file
        self.sessions_file = sessions_file
        self.sessions: Dict[str, Dict] = {}
        self.REDIRECT_URI = DEFAULT_REDIRECT_URI
        self._load_sessions()

    def _load_sessions(self) -> None:
        """Load sessions from the JSON file."""
        if os.path.exists(self.sessions_file):
            with open(self.sessions_file, 'r') as f:
                self.sessions = json.load(f)
                
                # Migration for backward compatibility: convert single scope to list
                for session_id, session in self.sessions.items():
                    if 'scope' in session and isinstance(session['scope'], str):
                        session['scopes'] = [session.pop('scope')]

    def _save_sessions(self) -> None:
        """Save sessions to the JSON file."""
        with open(self.sessions_file, 'w') as f:
            json.dump(self.sessions, f, indent=2)

    def create_session(self, session_id: str, scopes: Union[str, List[str]]) -> str:
        """
        Create a new session with specific scopes.
        
        Args:
            session_id: Unique identifier for the session
            scopes: OAuth scope(s) for this session (e.g. GMAIL_SCOPE or CALENDAR_SCOPE)
                   Can be a single scope string or a list of scopes
            
        Returns:
            session_id: The session ID
        """
        # Convert single scope to list if needed
        if isinstance(scopes, str):
            scopes = [scopes]
            
        self.sessions[session_id] = {
            'created_at': datetime.datetime.utcnow().isoformat(),
            'status': 'pending',
            'redirect_uri': self.REDIRECT_URI,
            'scopes': scopes,
            'token_data': None
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

    def get_credentials(self, session_id: str, required_scopes: Optional[Union[str, List[str]]] = None) -> Optional[Credentials]:
        """
        Get credentials for a specific session with optional scope filtering.
        
        Args:
            session_id: Session ID
            required_scopes: Specific scopes needed for this operation (str or list of str)
                            If specified, will check if these scopes are authorized
        
        Returns:
            Credentials if successful and all required scopes are authorized, None otherwise
        """
        session = self.sessions.get(session_id)
        if not session or not session.get('token_data'):
            return None

        # Get authorized scopes for this session
        authorized_scopes = session.get('scopes', [])
        
        # Check if required scopes are authorized
        if required_scopes:
            # Convert to list if it's a string
            if isinstance(required_scopes, str):
                required_scopes = [required_scopes]
                
            # Check if all required scopes are in authorized scopes
            if not all(scope in authorized_scopes for scope in required_scopes):
                return None
            
            # Use only the required scopes when creating credentials
            scopes_to_use = required_scopes
        else:
            # Use all authorized scopes if no specific scopes are required
            scopes_to_use = authorized_scopes

        token_data = session.get('token_data')

        creds = Credentials.from_authorized_user_info(
            {
                'token': token_data['token'],
                'refresh_token': token_data.get('refresh_token'),
                'token_uri': 'https://oauth2.googleapis.com/token',
                'client_id': token_data.get('client_id'),
                'client_secret': token_data.get('client_secret'),
                'scopes': scopes_to_use
            }
        )

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            # Update token in session
            self.sessions[session_id]['token_data']['token'] = creds.token
            self._save_sessions()

        return creds

    def get_auth_url(self, session_id: str, new_scopes: Optional[List[str]] = None) -> str:
        """
        Get OAuth2 authorization URL for a session.
        
        Args:
            session_id: Session ID
            new_scopes: New scopes to add to existing session (if None, use session's scopes)
            
        Returns:
            Auth URL string
        """
        session = self.sessions.get(session_id)
        if not session:
            return ""
            
        scopes = new_scopes if new_scopes else session.get('scopes', [])
        
        # Load client configuration
        with open(self.credentials_file, 'r') as f:
            client_config = json.load(f)
        
        # Create flow instance
        flow = Flow.from_client_config(
            client_config,
            scopes=scopes,
            redirect_uri=self.REDIRECT_URI
        )
        
        # Generate authorization URL
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            state=session_id,
            prompt='consent',
        )
        return auth_url

    def authenticate(self, session_id: str, scope: Union[str, List[str]]) -> Tuple[Optional[Credentials], Optional[str]]:
        """
        Authenticate a session and return credentials and auth URL if needed.
        
        Args:
            session_id: Unique identifier for the session
            scope: OAuth scope(s) for this session - can be a single scope or list of scopes
            
        Returns:
            Tuple of (credentials, auth_url)
            - If already authenticated: (credentials, None)
            - If not authenticated: (None, auth_url)
        """
        # Convert single scope to list if needed
        if isinstance(scope, str):
            scope = [scope]
            
        # Check if session exists
        session = self.get_session(session_id)
        
        # If session exists with credentials
        if session and session.get('token_data'):
            current_scopes = set(session.get('scopes', []))
            requested_scopes = set(scope)
            
            # Check if we already have all requested scopes
            if requested_scopes.issubset(current_scopes):
                creds = self.get_credentials(session_id, required_scopes=scope)
                if creds:
                    return creds, None
            
            # New scopes needed - update session with combined scopes
            new_scopes = list(current_scopes.union(requested_scopes))
            self.sessions[session_id]['scopes'] = new_scopes
            self.sessions[session_id]['status'] = 'pending_additional_scopes'
            self._save_sessions()
            
            # Get auth URL for all scopes
            auth_url = self.get_auth_url(session_id, new_scopes)
            return None, auth_url
        
        # Create a new session if it doesn't exist
        if not session:
            self.create_session(session_id, scope)
        
        auth_url = self.get_auth_url(session_id)
        return None, auth_url

    def handle_oauth_callback(self, session_id: str, code: str) -> Optional[Credentials]:
        """
        Handle OAuth callback.
        
        Args:
            session_id: Session ID
            code: Authorization code from OAuth provider
            
        Returns:
            Credentials if successful, None otherwise
        """
        session = self.get_session(session_id)
        if not session:
            return None

        try:
            scopes = session.get('scopes', [])
            previous_status = session.get('status', '')
            is_adding_scopes = previous_status == 'pending_additional_scopes'
            
            # Store previous token data to preserve refresh token if possible
            previous_token_data = session.get('token_data', {})
            
            # Load client configuration
            with open(self.credentials_file, 'r') as f:
                client_config = json.load(f)
            
            # Create flow instance
            flow = Flow.from_client_config(
                client_config,
                scopes=scopes,
                redirect_uri=self.REDIRECT_URI
            )
            
            # Exchange code for tokens
            flow.fetch_token(code=code)
            creds = flow.credentials

            # Prepare token data, potentially preserving previous refresh token
            token_data = {
                'token': creds.token,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret
            }
            
            # If adding scopes and no new refresh token but had one before, keep the old refresh token
            if is_adding_scopes and not creds.refresh_token and previous_token_data and previous_token_data.get('refresh_token'):
                token_data['refresh_token'] = previous_token_data.get('refresh_token')
            else:
                token_data['refresh_token'] = creds.refresh_token
            
            # Update last_updated timestamp
            token_data['last_updated'] = datetime.datetime.utcnow().isoformat()
            
            # Add information about the authorized scopes
            token_data['authorized_scopes'] = scopes
            
            # Update session
            self.sessions[session_id]['token_data'] = token_data
            self.sessions[session_id]['status'] = 'completed'
            
            # Record when we last successfully authorized
            self.sessions[session_id]['last_authorized'] = datetime.datetime.utcnow().isoformat()
            
            if is_adding_scopes:
                # Record that we successfully added scopes
                self.sessions[session_id]['scopes_history'] = self.sessions[session_id].get('scopes_history', [])
                self.sessions[session_id]['scopes_history'].append({
                    'date': datetime.datetime.utcnow().isoformat(),
                    'action': 'added_scopes',
                    'scopes': scopes
                })
            
            self._save_sessions()
            
            return creds
        except Exception as e:
            error_info = {
                'error_time': datetime.datetime.utcnow().isoformat(),
                'error_type': type(e).__name__,
                'error_message': str(e)
            }
            
            # Update session with error information
            if session_id in self.sessions:
                self.sessions[session_id]['status'] = 'failed'
                self.sessions[session_id]['last_error'] = error_info
                self._save_sessions()
                
            # Re-raise the exception
            raise e
            
    def has_scope(self, session_id: str, scope: str) -> bool:
        """
        Check if a session has a specific scope authorized.
        
        Args:
            session_id: Session ID
            scope: Scope to check
            
        Returns:
            True if scope is authorized, False otherwise
        """
        session = self.get_session(session_id)
        if not session or not session.get('token_data'):
            return False
            
        return scope in session.get('scopes', []) 