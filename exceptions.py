from typing import Optional

class MCPError(Exception):
    """Base exception for all MCP-related errors."""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)

class AuthenticationError(MCPError):
    """Raised when authentication fails."""
    def __init__(self, message: str = "Authentication failed", status_code: int = 401):
        super().__init__(message, status_code)

class AuthorizationError(MCPError):
    """Raised when authorization fails."""
    def __init__(self, message: str = "Not authorized", status_code: int = 403):
        super().__init__(message, status_code)

class ValidationError(MCPError):
    """Raised when input validation fails."""
    def __init__(self, message: str = "Invalid input", status_code: int = 400):
        super().__init__(message, status_code)

class ResourceNotFoundError(MCPError):
    """Raised when a requested resource is not found."""
    def __init__(self, message: str = "Resource not found", status_code: int = 404):
        super().__init__(message, status_code)

class ServiceUnavailableError(MCPError):
    """Raised when a service is unavailable."""
    def __init__(self, message: str = "Service unavailable", status_code: int = 503):
        super().__init__(message, status_code) 