import os
import logging
import httpx

logger = logging.getLogger(__name__)

MCP_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")

_session_id = None
_tools_cache = None


async def _mcp_request(method: str, params: dict = None) -> dict:
    """Send a JSON-RPC request to the MCP server."""
    global _session_id

    headers = {"Content-Type": "application/json"}
    if _session_id:
        headers["mcp-session-id"] = _session_id

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }
    if params:
        payload["params"] = params

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{MCP_URL}/mcp", json=payload, headers=headers)
        resp.raise_for_status()

        # Store session ID from response
        if "mcp-session-id" in resp.headers:
            _session_id = resp.headers["mcp-session-id"]

        return resp.json()


async def initialize():
    """Initialize the MCP connection."""
    global _session_id
    _session_id = None
    result = await _mcp_request("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "website-cloner", "version": "1.0.0"},
    })
    logger.info(f"MCP initialized: {result}")
    return result


async def list_tools() -> list:
    """List available MCP tools, returns them in OpenAI tool format."""
    global _tools_cache

    if _tools_cache is not None:
        return _tools_cache

    try:
        await initialize()
        result = await _mcp_request("tools/list")

        mcp_tools = result.get("result", {}).get("tools", [])
        openai_tools = []

        for tool in mcp_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })

        _tools_cache = openai_tools
        logger.info(f"MCP tools loaded: {[t['function']['name'] for t in openai_tools]}")
        return openai_tools

    except Exception as e:
        logger.warning(f"Failed to list MCP tools: {e}")
        return []


async def call_tool(name: str, arguments: dict) -> str:
    """Call an MCP tool and return the text result."""
    try:
        result = await _mcp_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        content = result.get("result", {}).get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts)

    except Exception as e:
        logger.error(f"MCP tool call failed ({name}): {e}")
        return f"Error calling tool: {e}"
