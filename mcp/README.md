# MCP Setup Guide

This guide explains how to use the shared MCP Toolbox configuration in this repo so any team member can test and use the current database tools without repeating setup/debugging.

## What this setup covers

Right now the MCP config supports the shared DAB databases currently running on the team server:

- **PostgreSQL** → `bookreview_db`
- **MongoDB** → `yelp_db`

This is the current working scope. SQLite and DuckDB are not part of this MCP config yet.

---

## Files

- **Toolbox binary:** `bin/toolbox`
- **MCP config:** `mcp/tools.yml`

---

## Current database connections

### PostgreSQL
- Host: `team-dab-postgres`
- Port: `5432`
- Database: `bookreview_db`

### MongoDB
- Host: `team-dab-mongo`
- Port: `27017`
- Database: `yelp_db`

---

## Current tools

### PostgreSQL tools
- `list_tables`
- `describe_books_info`
- `preview_books_info`

### MongoDB tools
- `find_yelp_businesses`
- `find_yelp_checkins`

---

## Current config

The working config is stored in `mcp/tools.yml`.

## How to test in the SSH instance

From the repo root:

```bash
cd ~/data-agent-challenge
## To test try this command
  ./bin/toolbox invoke --config mcp/tools.yaml list_tables
  ./bin/toolbox invoke --config mcp/tools.yaml describe_books_info
  ./bin/toolbox invoke --config mcp/tools.yaml preview_books_info
  ./bin/toolbox invoke --config mcp/tools.yaml find_yelp_businesses
  ./bin/toolbox invoke --config mcp/tools.yaml find_yelp_checkins
