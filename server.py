#!/usr/bin/env python
"""
Master Control Program (MCP) Runner
Runs all Google MCP modules (Gmail, Calendar, Search) in a single process.
"""

import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("mcp_runner")

# Import all MCP modules
try:
    from google.mail.mcp_google_gmail import main as gmail_main
    from google.calender.mcp_google_calendar import main as calendar_main
    from google.search.mcp_google_search_api import main as search_main
except ImportError as e:
    logger.error(f"Error importing MCP modules: {e}")
    logger.error("Please ensure all required packages are installed")
    exit(1)

async def run_mcp_services():
    """Run all MCP services concurrently."""
    logger.info("Starting all MCP services...")
    
    # Create tasks for each MCP service
    tasks = []
    
    # Gmail MCP
    logger.info("Starting Gmail MCP...")
    tasks.append(gmail_main())
    
    # Calendar MCP
    logger.info("Starting Calendar MCP...")
    tasks.append(calendar_main())
    
    # Search MCP
    logger.info("Starting Search MCP...")
    tasks.append(search_main())
    
    # Run all services concurrently
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Shutting down services...")
    except Exception as e:
        logger.error(f"Error running MCP services: {e}")
    finally:
        logger.info("All MCP services stopped")

def main():
    """Entry point for the combined MCP runner."""
    try:
        # Use asyncio to run the async main function
        asyncio.run(run_mcp_services())
    except KeyboardInterrupt:
        logger.info("MCP Runner terminated by user")
    except Exception as e:
        logger.error(f"Error in MCP Runner: {e}")

if __name__ == "__main__":
    main() 