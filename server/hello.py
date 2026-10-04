# A tiny MCP server with one tool, just to prove the connection works.
from mcp.server.mcpserver import MCPServer

# Create the server and give it a name.
mcp = MCPServer("incentive-coach")

# @mcp.tool() turns the function below into a tool the agent can call.
# The text in triple quotes is the description the model reads.
@mcp.tool()
def ping(name: str) -> str:
    """Health check. Returns a greeting to confirm the server is connected."""
    return f"Hello {name}, the Incentive Coach server is connected."

# Start the server.
if __name__ == "__main__":
    mcp.run()
