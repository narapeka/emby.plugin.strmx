"""
EmbyFast Proxy - Intercepts Emby PlaybackInfo requests and bypasses server-side probing for strm files.

This proxy sits between Emby clients and the server, intercepting API calls to modify
playback behavior for strm files - returning the stream URL immediately instead of waiting
for server-side media analysis.
"""

import asyncio
import json
import re
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs

import aiohttp
from aiohttp import web


class EmbyProxyHandler:
    """Handles HTTP requests to Emby server."""
    
    def __init__(self, emby_server_url: str, emby_api_key: str):
        self.emby_server = emby_server_url
        self.api_key = emby_api_key
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def initialize(self):
        """Initialize HTTP session."""
        # Disable automatic decompression so we can forward responses as-is
        self.session = aiohttp.ClientSession(auto_decompress=False)
    
    async def close(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
    
    def is_strm_file(self, item_info: dict) -> bool:
        """Check if an item is a strm file based on its metadata."""
        # Check by file extension in path
        path = item_info.get('Path', '')
        return path.lower().endswith('.strm')
    
    def is_playback_info_request(self, path: str) -> bool:
        """Check if request is for PlaybackInfo endpoint."""
        return path and '/Items/' in path and '/PlaybackInfo' in path
    
    def extract_item_id(self, path: str) -> Optional[str]:
        """Extract item ID from path like /Items/{id}/PlaybackInfo."""
        if not path:
            return None
        match = re.search(r'/Items/([^/]+)/PlaybackInfo', path)
        return match.group(1) if match else None
    
    def get_path(self, request: web.Request) -> str:
        """Get the full path with query string from request."""
        return str(request.rel_url)
    
    async def fetch_item_info(self, item_id: str) -> Optional[dict]:
        """Fetch basic item information from Emby."""
        url = f"{self.emby_server}/Items/{item_id}?api_key={self.api_key}"
        async with self.session.get(url) as resp:
            if resp.status == 200:
                return await resp.json()
        return None
    
    async def fetch_strm_content(self, item_path: str, item_id: str) -> Optional[str]:
        """Read URL from strm file."""
        # Try to fetch via Emby's API
        try:
            url = f"{self.emby_server}/Items/{item_id}/Download?api_key={self.api_key}"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    return content.strip()
        except Exception as e:
            print(f"[DEBUG] Could not fetch via API: {e}")
        
        # Fallback: the path should contain the strm file path
        # In a real implementation, we'd read from the file system
        return None
    
    def create_minimal_playback_info(self, item_info: dict, stream_url: str) -> dict:
        """Create a minimal PlaybackInfo response that bypasses server probing."""
        # This is the key: return minimal info and let player handle it
        return {
            "MediaSources": [{
                "Id": item_info.get('Id'),
                "Protocol": "Http",
                "Type": "Default",
                "Container": "",  # Player will detect
                "IsRemote": True,
                "SupportsTranscoding": True,
                "IsInfiniteStream": False,
                "IsIO": False,
                "DefaultAudioStreamIndex": None,
                "DefaultSubtitleStreamIndex": None,
                "DirectStreamUrl": stream_url,
            }],
            "PlaySessionId": f"play_{item_info.get('Id')}",
        }
    
    async def handle_playback_info(self, request: web.Request):
        """Intercept and modify PlaybackInfo requests for strm files."""
        # Get the path
        path = str(request.rel_url)
        # Extract item ID
        item_id = self.extract_item_id(path)
        if not item_id:
            # Not a playback info request or malformed, pass through
            return await self.pass_through(request)
        
        # Fetch item info
        item_info = await self.fetch_item_info(item_id)
        if not item_info:
            return await self.pass_through(request)
        
        # Check if it's a strm file
        if not self.is_strm_file(item_info):
            # Not a strm file, pass through normally
            return await self.pass_through(request)
        
        # For strm files, bypass server probing
        print(f"[FAST] Bypassing probe for strm file: {item_info.get('Name')}")
        
        # Read strm file content
        path = item_info.get('Path', '')
        strm_url = await self.fetch_strm_content(path, item_id)
        if not strm_url:
            print(f"[WARN] Could not read strm content, passing through")
            return await self.pass_through(request)
        
        # Create minimal playback info
        playback_info = self.create_minimal_playback_info(item_info, strm_url)
        
        # Forward original body if present
        body = await request.read() if request.content_length else b''
        
        # Return modified response
        return web.json_response(playback_info)
    
    async def pass_through(self, request: web.Request):
        """Pass request through to Emby server unchanged."""
        # Get request data - need to check if it's already been read
        data = None
        if request.content_length and request.content_length > 0:
            data = await request.read()
        
        # Build destination URL - use rel_url to get path with query
        path = str(request.rel_url)
        dest_url = f"{self.emby_server}{path}"
        
        # Log requests but not all (reduce noise for static assets)
        if '/Users/' in path or '/Sessions/' in path or request.method in ['POST', 'PUT', 'DELETE']:
            print(f"[FORWARD] {request.method} {path}")
            if data:
                print(f"[DATA] {len(data)} bytes, Content-Type: {request.headers.get('Content-Type', 'none')}")
        
        # Forward headers (clean up)
        headers = dict(request.headers)
        # Remove connection-related headers but keep everything else
        headers.pop('Connection', None)
        headers.pop('Host', None)
        # Don't remove Content-Length - it needs to be accurate
        # The client sets this, we should forward it
        
        # Forward request
        try:
            # Use the same request method and pass data as-is
            async with self.session.request(
                request.method, dest_url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                body = await resp.read()
                
                print(f"[RESPONSE] {resp.status} for {request.method} {path}")
                
                # Copy all response headers (important for proper rendering)
                response_headers = dict(resp.headers)
                
                # Remove connection-specific headers that might cause issues
                response_headers.pop('Connection', None)
                response_headers.pop('Proxy-Connection', None)
                response_headers.pop('Keep-Alive', None)
                response_headers.pop('Transfer-Encoding', None)
                
                # Remove problematic headers
                response_headers.pop('Content-Length', None)  # Let it be recalculated
                
                # Keep Content-Encoding as-is so browser can decompress if needed
                # Keep Content-Type, Cache-Control, etc.
                
                return web.Response(
                    body=body,
                    status=resp.status,
                    headers=response_headers
                )
        except Exception as e:
            print(f"[ERROR] Failed to forward request to {dest_url}: {e}")
            import traceback
            traceback.print_exc()
            return web.Response(
                text=f"Proxy error: Could not connect to Emby server at {self.emby_server}\n\nMake sure:\n1. Emby server is running\n2. The URL is correct\n3. You can access Emby directly",
                status=503
            )
    
    async def handle_request(self, request):
        """Main request handler."""
        path = str(request.rel_url)
        
        # Debug logging
        print(f"[DEBUG] {request.method} {path}")
        
        if self.is_playback_info_request(path):
            try:
                return await self.handle_playback_info(request)
            except Exception as e:
                print(f"[ERROR] Failed to handle playback info: {e}")
                import traceback
                traceback.print_exc()
                # Fallback to pass-through
                return await self.pass_through(request)
        else:
            return await self.pass_through(request)


async def web_server(emby_server_url: str, emby_api_key: str, listen_port: int):
    """Start the proxy web server."""
    handler = EmbyProxyHandler(emby_server_url, emby_api_key)
    await handler.initialize()
    
    async def handle(request):
        return await handler.handle_request(request)
    
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handle)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, '0.0.0.0', listen_port)
    await site.start()
    
    print(f"[INFO] EmbyFast proxy started on port {listen_port}")
    print(f"[INFO] Proxying to Emby server at {emby_server_url}")
    print(f"[INFO] Configure your Emby client to use: http://localhost:{listen_port}")
    
    try:
        await asyncio.Future()  # Run forever
    finally:
        await runner.cleanup()
        await handler.close()


if __name__ == '__main__':
    import sys
    
    # Configuration (should come from config file or env)
    EMBY_SERVER = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:8096'
    API_KEY = sys.argv[2] if len(sys.argv) > 2 else ''
    PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 8097
    
    print(f"[CONFIG] Emby server: {EMBY_SERVER}")
    print(f"[CONFIG] API key: {'***' if API_KEY else 'NOT SET'}")
    print(f"[CONFIG] Listening on port: {PORT}")
    
    asyncio.run(web_server(EMBY_SERVER, API_KEY, PORT))

