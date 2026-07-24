# Client wiring templates (IDE stdio + runtime HTTP)

## IDE (stdio) — after `ast-intel install cursor`

`.cursor/mcp.json` (paths must be absolute):

```json
{
  "mcpServers": {
    "ast-intel": {
      "command": "ast-intel",
      "args": ["serve", "/absolute/path/to/service/repo"]
    }
  }
}
```

## Runtime agent (Streamable HTTP) — same container

Point the MCP client / agent SDK at:

```text
http://127.0.0.1:7500/mcp
```

Environment for the service process (optional convenience):

```bash
export AST_INTEL_MCP_URL=http://127.0.0.1:7500/mcp
```

## Remote IDE (only with auth in front)

```json
{
  "mcpServers": {
    "ast-intel": {
      "url": "https://my-service.example.com/mcp"
    }
  }
}
```

Do not expose port 7500 on the public internet without Easy Auth, mTLS, or an API gateway.
