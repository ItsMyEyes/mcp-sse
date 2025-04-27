# MCP-SSE (Server-Sent Events)

A server application that implements Server-Sent Events (SSE) for real-time data streaming.

## Features

- Server-Sent Events (SSE) implementation
- Callback endpoint for external services
- Templated HTML interface
- Secure credential storage

## Setup

1. Create a virtual environment and activate it:
   ```
   python -m venv .venv
   # Windows
   .\.venv\Scripts\activate
   # Linux/Mac
   source .venv/bin/activate
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Configure your credentials in the `secrets` folder (See Configuration section)

## Configuration

Create a `secrets` folder and add your credentials:

1. Create the folder: `mkdir secrets`
2. Add your API keys and other sensitive information to appropriate files in this folder
3. Make sure to add `secrets/` to your `.gitignore` file

## Running the Application

Start the server:

```
python server.py
```

By default, the server runs on:
- Main SSE endpoint: http://localhost:5000
- Callback endpoint: http://localhost:5001

## API Endpoints

- `/stream` - SSE endpoint for real-time data streaming
- `/callback` - Endpoint for receiving callbacks from external services

## Templates

The application uses HTML templates located in the `templates` folder:

- `start.html` - Initial page
- `success.html` - Success page
- `error.html` - Error page 