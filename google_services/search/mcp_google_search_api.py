import os
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
import httpx
from pydantic import BaseModel, Field, HttpUrl
from fastmcp import FastMCP, Context
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import uvicorn
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode, LLMConfig
from crawl4ai.extraction_strategy import LLMExtractionStrategy

from starlette.applications import Starlette
from mcp.server.sse import SseServerTransport
from starlette.requests import Request as StarletteRequest
from starlette.routing import Mount, Route

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
app = FastMCP('google-search')

# Server port configuration
MCP_PORT = 8082

class SearchParams(BaseModel):
    """Parameters for a Google search query."""
    query: str = Field(..., description="The search query string")
    num_results: int = Field(5, description="Number of results to return (max 10)", ge=1, le=10)
    safe_search: bool = Field(True, description="Whether to enable safe search")

class SearchResult(BaseModel):
    """A single search result item."""
    title: str = Field(..., description="Title of the search result")
    link: HttpUrl = Field(..., description="URL of the search result")
    snippet: str = Field(..., description="Text snippet from the search result")
    display_link: str = Field(..., description="Display URL of the search result")

class SearchResponse(BaseModel):
    """Response from a Google search query."""
    query: str = Field(..., description="The original search query")
    results: List[SearchResult] = Field(..., description="List of search results")
    total_results: int = Field(..., description="Total number of results available")

class WebScrapingParams(BaseModel):
    """Parameters for web scraping with crawl4ai."""
    url: HttpUrl = Field(..., description="URL to scrape")
    extract_schema: Optional[str] = Field(None, description="Optional JSON schema for structured extraction")
    extract_instruction: Optional[str] = Field(None, description="Instructions for extraction")
    headless: bool = Field(True, description="Whether to run browser in headless mode")
    wait_for_selector: Optional[str] = Field(None, description="CSS selector to wait for before scraping")
    timeout: int = Field(30000, description="Timeout in milliseconds", ge=1000, le=60000)

class ScrapingResult(BaseModel):
    """Result from web scraping."""
    url: HttpUrl = Field(..., description="URL that was scraped")
    success: bool = Field(..., description="Whether scraping was successful")
    content: Optional[str] = Field(None, description="Scraped content in Markdown format")
    extracted_data: Optional[Dict[str, Any]] = Field(None, description="Structured data if schema was provided")
    screenshot: Optional[str] = Field(None, description="Base64 encoded screenshot if enabled")
    error: Optional[str] = Field(None, description="Error message if scraping failed")

class SearchHistoryItem(BaseModel):
    """A single search history item."""
    id: str = Field(..., description="Unique identifier for the search")
    query: str = Field(..., description="The search query")
    timestamp: str = Field(..., description="ISO format timestamp of the search")
    num_results: int = Field(..., description="Number of results requested")

class SearchHistoryResponse(BaseModel):
    """Response containing search history."""
    history: List[SearchHistoryItem] = Field(..., description="List of search history items")

class SearchAndScrapeResult(BaseModel):
    """Result from search and scrape operation."""
    title: str = Field(..., description="Title of the search result")
    link: HttpUrl = Field(..., description="URL of the search result")
    scrape_content: str = Field(..., description="Scraped content from the webpage")

class SearchAndScrapeResponse(BaseModel):
    """Response from search and scrape operation."""
    query: str = Field(..., description="The original search query")
    results: List[SearchAndScrapeResult] = Field(..., description="List of results with scraped content")

class APIInfo(BaseModel):
    """Information about the API configuration."""
    configured: bool = Field(..., description="Whether the API is properly configured")
    features: List[str] = Field(..., description="List of available features")
    max_results_per_query: int = Field(..., description="Maximum number of results per query")

class GoogleSearchAPI:
    """
    Google Search API interface.
    
    This class provides methods to interact with Google's Custom Search JSON API.
    """
    
    def __init__(
        self, 
        api_key: str = None,
        search_engine_id: str = None,
    ):
        """
        Initialize the Google Search API.
        
        Args:
            api_key: Google API key
            search_engine_id: Google Custom Search Engine ID
        """
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "AIzaSyBby3hAZ9LvXxfuCj6tTlTeaQkiv_uRF3M")
        self.search_engine_id = search_engine_id or os.environ.get("GOOGLE_CSE_ID", "172276e543c774536")
        self.search_history = []
        self.history_id_counter = 1

    async def perform_search(
        self, 
        query: str, 
        num_results: int = 10, 
        safe_search: bool = True
    ) -> Dict[str, Any]:
        """
        Perform a Google search and return the results.
        
        Args:
            query: Search query string
            num_results: Number of results to return (max 10)
            safe_search: Whether to enable safe search
            
        Returns:
            Dict containing search results
        """
        if not self.api_key or not self.search_engine_id:
            return {"error": "API key or Search Engine ID not configured."}
        
        try:
            search_url = "https://www.googleapis.com/customsearch/v1"
            
            # Prepare query parameters
            query_params = {
                "key": self.api_key,
                "cx": self.search_engine_id,
                "q": query,
                "num": num_results,
                "safe": "active" if safe_search else "off",
            }
            
            # Make the API request
            async with httpx.AsyncClient() as client:
                response = await client.get(search_url, params=query_params)
                response.raise_for_status()
                data = response.json()
            
            # Process the results
            results = []
            if "items" in data:
                for item in data["items"]:
                    results.append(
                        SearchResult(
                            title=item.get("title", ""),
                            link=item.get("link", ""),
                            snippet=item.get("snippet", ""),
                            display_link=item.get("displayLink", "")
                        )
                    )
            
            # Record in search history
            self._add_to_history(query, num_results)
            
            # Build the response
            search_response = SearchResponse(
                query=query,
                results=results,
                total_results=int(data.get("searchInformation", {}).get("totalResults", 0))
            )
            
            return search_response.model_dump()
                
        except Exception as e:
            error_msg = f"Error performing search: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}
    
    def _add_to_history(self, query: str, num_results: int) -> None:
        """Add a search to the history."""
        import datetime
        
        history_entry = SearchHistoryItem(
            id=str(self.history_id_counter),
            query=query,
            timestamp=datetime.datetime.now().isoformat(),
            num_results=num_results
        )
        
        self.search_history.append(history_entry.model_dump())
        self.history_id_counter += 1
        
        # Keep only the last 50 searches
        if len(self.search_history) > 50:
            self.search_history = self.search_history[-50:]
    
    def get_search_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent search history."""
        return self.search_history[-limit:]
    
    def clear_search_history(self) -> None:
        """Clear search history."""
        self.search_history = []
        
    async def scrape_webpage(
        self,
        url: str,
        extract_schema: Optional[str] = None,
        extract_instruction: Optional[str] = None,
        headless: bool = True,
        wait_for_selector: Optional[str] = None,
        timeout: int = 30000
    ) -> Dict[str, Any]:
        """
        Scrape a webpage using crawl4ai.
        
        Args:
            url: URL to scrape
            extract_schema: Optional JSON schema for structured extraction
            extract_instruction: Instructions for extraction
            headless: Whether to run browser in headless mode
            wait_for_selector: CSS selector to wait for before scraping
            timeout: Timeout in milliseconds
            
        Returns:
            Dict containing the scraped content
        """
        try:
            # Configure browser
            browser_cfg = BrowserConfig(
                headless=headless,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )
            
            # Run the crawler
            print("Running crawler url", url)
            browser_cfg = BrowserConfig(
                headless=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS
            )

            # 4. Run the crawler
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                result = await crawler.arun(url=url, config=crawler_config)
                
                if result.success:
                    return ScrapingResult(
                        url=url,
                        success=True,
                        content=result.markdown,
                        extracted_data=result.extracted_data if hasattr(result, 'extracted_data') else None,
                        screenshot=result.screenshot if hasattr(result, 'screenshot') else None
                    ).model_dump()
                else:
                    return ScrapingResult(
                        url=url,
                        success=False,
                        error=result.error_message
                    ).model_dump()
                    
        except Exception as e:
            error_msg = f"Error scraping webpage: {str(e)}"
            logger.error(error_msg)
            return ScrapingResult(
                url=url,
                success=False,
                error=error_msg
            ).model_dump()
            
    async def search_and_scrape(
        self,
        query: str,
        num_results: int = 1,
        extract_schema: Optional[str] = None,
        extract_instruction: Optional[str] = None,
        headless: bool = True
    ) -> Dict[str, Any]:
        """
        Search for information and scrape the top result.
        
        Args:
            query: Search query
            num_results: Number of search results to get (defaults to 1)
            extract_schema: Optional JSON schema for structured extraction
            extract_instruction: Instructions for extraction
            headless: Whether to run browser in headless mode
            
        Returns:
            Dict containing both search results and scraped content
        """
        # First, search for the query
        search_response = await self.perform_search(query, num_results, safe_search=True)
        
        if "error" in search_response:
            return search_response
            
        results = search_response.get("results", [])
        if not results:
            return {
                "query": query,
                "error": "No search results found"
            }
            
        # Create the new results format that will include scraped content
        new_results = []
        
        # Process each result (up to num_results)
        for i, result in enumerate(results[:num_results]):
            # Get the URL for this result
            url = result["link"]
            
            # Scrape the page
            scrape_result = await self.scrape_webpage(
                url=url,
                extract_schema=extract_schema,
                extract_instruction=extract_instruction,
                headless=headless
            )
            
            # Create a new result object with combined data
            new_result = SearchAndScrapeResult(
                title=result["title"],
                link=result["link"],
                scrape_content=scrape_result.get("content", "") if scrape_result.get("success", False) else ""
            )
            
            # Add to our results list
            new_results.append(new_result.model_dump())
        
        # Return in the requested format
        return SearchAndScrapeResponse(
            query=query,
            results=new_results
        ).model_dump()

# Create a shared API instance
search_api = GoogleSearchAPI()

@app.tool()
async def search(
    query: str,
    num_results: int = 5,
    safe_search: bool = True,
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Perform a Google search and return the results.
    
    Usage:
    - Search the web for information
    - Get links and snippets for a query
    - Research a topic using web results
    
    Parameters:
        query (str): The search query to look for
        num_results (int, optional): Number of results to return (default: 5, max: 10)
        safe_search (bool, optional): Whether to enable safe search (default: True)
        
    Returns:
        dict: Search results including:
        - query: The original search query
        - results: List of found items with title, link, snippet, display_link
        - total_results: Total number of results available
        
    Example:
        result = await search("latest AI news")
        # Returns dictionary with search results
    """
    if ctx:
        await ctx.info(f"Searching for: {query}")
        await ctx.report_progress(progress=0, total=100)
    
    result = await search_api.perform_search(query, num_results, safe_search)
    
    if ctx:
        if "error" in result:
            await ctx.error(result["error"])
        else:
            await ctx.report_progress(progress=100, total=100)
            await ctx.info(f"Search complete. Found {len(result.get('results', []))} results.")
    
    return result

@app.tool()
async def search_and_scrape(
    query: str,
    num_results: int = 3,
    extract_schema: Optional[str] = None,
    extract_instruction: Optional[str] = "Extract all relevant information from the page content.",
    headless: bool = True,
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Search for information and scrape the top results' webpage content.
    
    Usage:
    - Get detailed information from web pages
    - Extract structured data from search results
    - Perform deep research on topics
    - Analyze webpage content beyond search snippets
    
    Parameters:
        query (str): The search query to look for
        num_results (int, optional): Number of results to scrape (default: 3)
        extract_schema (str, optional): JSON schema for structured extraction
        extract_instruction (str, optional): Instructions for extraction
        headless (bool, optional): Whether to run browser in headless mode (default: True)
        
    Returns:
        dict: Combined results including:
        - query: The original search query
        - results: List of results, each containing:
          - title: The title of the search result
          - link: The URL of the search result
          - scrape_content: The scraped content from the webpage
        
    Example:
        # Simple search and scrape
        result = await search_and_scrape("climate change latest research")
        
        # With multiple results
        result = await search_and_scrape("python tutorial", num_results=3)
    """
    if ctx:
        await ctx.info(f"Searching and scraping content for: {query}")
        await ctx.report_progress(progress=0, total=100)
    
    # First stage: Search
    if ctx:
        await ctx.info(f"Searching for '{query}' and scraping top {num_results} results...")
        await ctx.report_progress(progress=10, total=100)
    
    result = await search_api.search_and_scrape(
        query=query,
        num_results=num_results,
        extract_schema=extract_schema,
        extract_instruction=extract_instruction,
        headless=headless
    )
    
    if ctx:
        if "error" in result:
            await ctx.error(result["error"])
            return result
        
        await ctx.report_progress(progress=100, total=100)
        await ctx.info(f"Search and scrape complete. Found {len(result.get('results', []))} results with content.")
    
    return result

@app.tool()
async def scrape_webpage(
    params: WebScrapingParams,
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Scrape content from a specific webpage.
    
    Usage:
    - Extract content from a specific URL
    - Get structured data from webpages
    - Analyze webpage content in detail
    
    Parameters:
        params (WebScrapingParams): Parameters for web scraping including:
          - url (str): URL to scrape
          - extract_schema (str, optional): JSON schema for structured extraction
          - extract_instruction (str, optional): Instructions for extraction
          - headless (bool, optional): Whether to run browser in headless mode
          - wait_for_selector (str, optional): CSS selector to wait for
          - timeout (int, optional): Timeout in milliseconds
        
    Returns:
        dict: Scraping results including:
        - url: The scraped URL
        - success: Whether scraping was successful
        - content: The full Markdown text of the page
        - extracted_data: Structured data if schema was provided
        
    Example:
        result = await scrape_webpage({
            "url": "https://example.com/article",
            "headless": True,
            "timeout": 30000
        })
    """
    if ctx:
        await ctx.info(f"Scraping webpage: {params.url}")
        await ctx.report_progress(progress=0, total=100)
    
    result = await search_api.scrape_webpage(
        url=params.url,
        extract_schema=params.extract_schema,
        extract_instruction=params.extract_instruction,
        headless=params.headless,
        wait_for_selector=params.wait_for_selector,
        timeout=params.timeout
    )
    
    if ctx:
        if not result.get("success", False):
            error_msg = result.get("error", "Unknown scraping error")
            await ctx.error(f"Error scraping webpage: {error_msg}")
        else:
            await ctx.report_progress(progress=100, total=100)
            await ctx.info(f"Webpage scraping complete for {params.url}")
    
    return result

@app.tool()
async def get_search_history(
    limit: int = 10,
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Get recent search history.
    
    Usage:
    - View previous searches
    - Review search patterns
    
    Parameters:
        limit (int, optional): Maximum number of history items to return (default: 10)
        
    Returns:
        dict: Search history including:
        - history: List of search history items with id, query, timestamp, num_results
        
    Example:
        history = await get_search_history(5)
        # Returns dictionary with recent 5 search history items
    """
    if ctx:
        await ctx.info(f"Retrieving search history (limit: {limit})")
    
    history = search_api.get_search_history(limit)
    
    if ctx:
        await ctx.info(f"Retrieved {len(history)} history items")
    
    return SearchHistoryResponse(history=history).model_dump()

@app.tool()
async def clear_search_history(
    ctx: Context = None
) -> Dict[str, Any]:
    """
    Clear search history.
    
    Usage:
    - Clear all search history records
    - Reset tracking of past searches
    
    Returns:
        dict: Status of the operation
        
    Example:
        result = await clear_search_history()
        # Returns dictionary with success status
    """
    if ctx:
        await ctx.info("Clearing search history")
    
    search_api.clear_search_history()
    
    if ctx:
        await ctx.info("Search history cleared")
    
    return {"status": "success", "message": "Search history cleared"}

@app.resource("search://info")
def get_search_info() -> Dict[str, Any]:
    """
    Get information about the search service configuration.
    """
    return APIInfo(
        configured=bool(search_api.api_key and search_api.search_engine_id),
        features=["web_search", "safe_search", "history_tracking", "web_scraping"],
        max_results_per_query=10
    ).model_dump()

def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can serve the provided mcp server with SSE."""
    sse = SseServerTransport("/google-search/messages/")

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
            Route("/google-search/sse", endpoint=handle_sse),
            Mount("/google-search/messages/", app=sse.handle_post_message),
        ],
    )

async def run_mcp_server(host: str = "0.0.0.0", port: int = MCP_PORT):
    """Run the MCP server."""
    mcp_server = app._mcp_server  # noqa: WPS437
    starlette_app = create_starlette_app(mcp_server, debug=True)
    config = uvicorn.Config(starlette_app, host=host, port=port)
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    """Run the MCP server."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Run Google Search MCP server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=MCP_PORT, help='Port for MCP server')
    parser.add_argument('--api-key', help='Google API key')
    parser.add_argument('--search-engine-id', help='Google Custom Search Engine ID')
    args = parser.parse_args()
    
    # Set API credentials if provided
    if args.api_key:
        search_api.api_key = args.api_key
    if args.search_engine_id:
        search_api.search_engine_id = args.search_engine_id
    
    logger.info("Starting Google Search MCP server")
    logger.info(f"MCP server will run on http://{args.host}:{args.port}/google-search/sse")
    
    # Run the MCP server
    await run_mcp_server(args.host, args.port)

if __name__ == "__main__":
    asyncio.run(main()) 