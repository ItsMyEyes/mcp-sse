# MCP Server

A modern, maintainable server implementation for handling Google API integrations with proper authentication and error handling.

## Features

- Google OAuth2 authentication
- Gmail API integration
- Calendar API integration
- Proper error handling and logging
- Configuration management
- Type hints and documentation
- CORS support
- Docker support

## Prerequisites

- Python 3.8+
- Docker (optional)
- Google Cloud Platform account with API credentials

## Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/mcp-sse.git
cd mcp-sse
```

2. Create a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the root directory:
```env
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
```

## Configuration

The application uses `pydantic-settings` for configuration management. You can configure the following settings in your `.env` file:

- `MCP_HOST`: Host to bind the MCP server to (default: "0.0.0.0")
- `MCP_PORT`: Port for the MCP server (default: 8000)
- `AUTH_PORT`: Port for the auth server (default: 8001)
- `GOOGLE_CLIENT_ID`: Your Google OAuth client ID
- `GOOGLE_CLIENT_SECRET`: Your Google OAuth client secret
- `SESSION_EXPIRY`: Session expiry time in seconds (default: 3600)

## Running the Server

### Development

```bash
python server.py
```

### Docker

```bash
docker-compose up
```

## API Endpoints

### Authentication

- `GET /auth/start`: Start OAuth flow
- `GET /auth/status`: Check authentication status
- `GET /auth/callback`: OAuth callback endpoint

### Gmail

- `GET /gmail/messages`: List messages
- `GET /gmail/messages/{message_id}`: Get message details
- `POST /gmail/messages`: Send message

### Calendar

- `GET /calendar/events`: List events
- `POST /calendar/events`: Create event
- `GET /calendar/events/{event_id}`: Get event details

## Error Handling

The application uses custom exceptions for better error handling:

- `MCPError`: Base exception for all MCP-related errors
- `AuthenticationError`: Raised when authentication fails
- `AuthorizationError`: Raised when authorization fails
- `ValidationError`: Raised when input validation fails
- `ResourceNotFoundError`: Raised when a requested resource is not found
- `ServiceUnavailableError`: Raised when a service is unavailable

## Logging

The application uses structured logging with the following features:

- Console and file logging
- JSON format for machine readability
- Different log levels (INFO, ERROR, etc.)
- Contextual information in log messages

## Development

### Code Style

The project uses:
- Black for code formatting
- isort for import sorting
- flake8 for linting
- mypy for type checking

### Running Tests

```bash
pytest
```

### Type Checking

```bash
mypy .
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 