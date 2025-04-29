# Google Services MCP Framework

A clean architecture framework for building Model Context Protocol (MCP) services with Google APIs.

## Overview

This project provides a robust, modular architecture for building services that interact with Google APIs (Gmail, Calendar, etc.) through standardized MCP tools. The implementation follows clean architecture principles, separating concerns into layers:

- **Core Layer**: Core business entities, use cases, and repository interfaces
- **Infrastructure Layer**: Implementation of repository interfaces and external services
- **Interfaces Layer**: API and SSE implementations for exposing MCP tools

## Features

- **Clean Architecture**: Separation of concerns with domain-driven design
- **Structured Logging**: Comprehensive tracing and monitoring
- **Repository Pattern**: Abstract data access for testability and flexibility
- **Dependency Injection**: Loose coupling between components
- **OAuth Authentication**: Support for Google OAuth flows
- **SSE Streaming**: Real-time updates through Server-Sent Events
- **Type Safety**: Full type annotations for better development experience

## Project Structure

```
src/
  core/                      # Core domain layer
    entities/                # Domain entities
      google_auth.py         # Auth entities
      gmail.py               # Gmail entities
      calendar.py            # Calendar entities
    repositories/            # Repository interfaces
      auth_repository.py     # Auth repository interface
      gmail_repository.py    # Gmail repository interface
      calendar_repository.py # Calendar repository interface
    use_cases/               # Application use cases
      authenticate.py        # Auth use cases
      gmail.py               # Gmail use cases
      calendar.py            # Calendar use cases
    logging/                 # Logging utilities
      logger.py              # Logger configuration and utilities
  infrastructure/            # Infrastructure layer
    repositories/            # Repository implementations
      google_auth_repository.py  # Google Auth implementation
      gmail_repository.py    # Gmail API implementation
      calendar_repository.py # Calendar API implementation
  interfaces/                # Interface layer
    api/                     # API interfaces
      gmail.py               # Gmail API interface
      calendar.py            # Calendar API interface
      oauth.py               # OAuth API interface
templates/                  # HTML templates
  oauth_callback.html       # OAuth callback template
server.py                   # Main server entry point
```

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/mcp-sse.git
   cd mcp-sse
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up Google OAuth credentials:
   - Create a project in the [Google Cloud Console](https://console.cloud.google.com/)
   - Enable the APIs you need (Gmail, Calendar, etc.)
   - Create OAuth credentials and download as `credentials.json`
   - Place `credentials.json` in the project root

## Running the Server

Run the main server with default settings:

```bash
python server.py
```

### Command Line Options

The server supports various command line options:

```bash
python server.py --help
```

Available options:

- `--host`: Host to bind to (default: 0.0.0.0)
- `--port`: Port for MCP server (default: 8000)
- `--debug`: Enable debug mode
- `--log-level`: Logging level (TRACE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
- `--log-format`: Logging format (json or text)
- `--log-file`: Log file path (logs to console if not specified)

Examples:

```bash
# Run with debug mode and detailed logging
python server.py --debug --log-level=DEBUG --log-format=text

# Specify host, port and log to file
python server.py --host=127.0.0.1 --port=9000 --log-file=logs/server.log
```

## Logging and Tracing

The application uses structured logging to provide detailed information about operations. When using JSON logging format, logs contain contextual information like:

- Timestamp and level
- Function name and module
- Trace IDs for request correlation
- Detailed request/response information
- Performance metrics (execution time)

### Log Levels

- **TRACE**: Detailed tracing information (function entry/exit, parameters)
- **DEBUG**: Debug information useful during development
- **INFO**: General operational information
- **WARNING**: Warning events that might need attention
- **ERROR**: Error events that might still allow the application to continue
- **CRITICAL**: Critical events that might prevent the application from continuing

## Using Docker

Build and run the services using Docker:

```bash
docker build -t google-mcp-services .
docker run -p 8000:8000 google-mcp-services
```

Or use Docker Compose:

```bash
docker-compose up
```

## MCP Tool Integration

The framework exposes MCP tools for integration with AI assistants. Available tools include:

### Gmail Tools

- `get_auth_status`: Check OAuth authentication status
- `list_emails`: List emails with optional filtering
- `get_email`: Get details of a specific email
- `get_labels`: List available Gmail labels
- `search_emails`: Search for emails using Gmail query syntax
- `get_attachment`: Get email attachment data

### Calendar Tools (Coming Soon)

- `list_calendar_events`: List calendar events
- `get_event`: Get details of a specific event
- `create_event`: Create a new event
- `update_event`: Update an existing event
- `delete_event`: Delete an event
- `search_events`: Search for events

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature-name`
3. Commit your changes: `git commit -am 'Add some feature'`
4. Push to the branch: `git push origin feature-name`
5. Submit a pull request

## License

MIT 