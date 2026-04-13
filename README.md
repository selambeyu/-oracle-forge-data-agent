# data-agent-challenge

## MCP Toolbox

The repo now uses a single MCP Toolbox config at `mcp/tools.yaml`.

- Start the local Toolbox server with `./setup_dab.sh`
- Configure the server URL with `TOOLBOX_URL` in `.env`
- Use `MCPToolbox` from `agent/mcp_toolbox.py` for hybrid routing:
  non-DuckDB tools go through the Toolbox CLI and DuckDB uses a direct driver path

Quick verification command:

```bash
./bin/toolbox invoke --config mcp/tools.yaml list_tables
```
