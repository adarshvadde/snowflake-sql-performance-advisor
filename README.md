# Snowflake SQL Performance Advisor

Python [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers that let AI assistants analyze Snowflake query performance, warehouse usage, and ETL pipeline health, plus a basic server for reading and writing Snowflake data.

## Repository layout

```
.
├── snowflake_cost/          Performance, cost and ETL advisor (read-only)
│   ├── server.py            MCP server (streamable HTTP), 17 tools
│   ├── optimizer.py         Rule-based SQL optimization checks (sqlglot)
│   ├── sql_validator.py     Single-statement, SELECT-only validation
│   ├── snowflake_client.py  Connection and read-query helpers
│   └── requirements.txt
├── snowflake_mcp/
│   └── snowflake_server.py  Basic Snowflake server with CRUD tools (stdio)
├── .bob/skills/data-mapping-agent/
│   └── SKILL.md             Bob skill: generates SQL from source-to-target mappings
└── log/python_testing.py    Scratch file
```

## Servers

### 1. `snowflake_cost`: Snowflake AI & SQL Performance Advisor

Runs over streamable HTTP at `http://127.0.0.1:8000/mcp` by default.

| Area | Tools |
|---|---|
| Query analysis | `test_and_explain_query`, `estimate_query_cost_and_time`, `check_query_optimization`, `evaluate_good_run_metrics`, `generate_bob_snowflake_report` |
| Query history and performance | `get_query_history`, `find_slow_queries`, `get_query_details`, `get_query_statistics`, `get_warehouse_usage`, `compare_query_performance`, `get_query_profile` |
| ETL monitoring | `get_etl_task_history`, `get_failed_etl_tasks`, `get_etl_task_details`, `get_etl_pipeline_health`, `analyze_etl_task_failure`, `generate_etl_monitoring_report` |

Query-accepting tools only allow a single `SELECT` statement.

### 2. `snowflake_mcp`: Basic Snowflake server

Runs over stdio. Tools: `test_connection`, `list_tables`, `read_data` (single `SELECT` only), `create_record`, `update_record`, `delete_record`.

> **Warning:** `create_record`, `update_record` and `delete_record` modify data. Use a role with the minimum required privileges.

### 3. `data-mapping-agent`: Bob skill for source-to-target mapping SQL

Located at [`.bob/skills/data-mapping-agent/SKILL.md`](.bob/skills/data-mapping-agent/SKILL.md). It is a Bob skill (an AI agent), not an MCP server. It reads source-to-target mapping requirements and generates SQL for each transformation in Oracle (the default), Snowflake or MySQL.

- Processes each target column or transformation separately and never merges them.
- Doesn't guess source columns or tables. It asks you when information is missing.
- Generates only `SELECT` or `INSERT...SELECT`, and doesn't execute the SQL.
- Writes the result to an Excel workbook named `<file name>SQL_generated.xlsx`.

Use it by asking Bob to generate SQL from a mapping document, or run `/data-mapping-agent`. Start a new Bob task after cloning so the skill is picked up.


## Setup

Requires Python 3.10+ and a Snowflake account.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r snowflake_cost\requirements.txt
```

### Configuration

Credentials are read from environment variables. Create a `.env` file in each server folder (`snowflake_cost/` and `snowflake_mcp/`). `.env` files are git-ignored, so never commit them.

```
SNOWFLAKE_USER=
SNOWFLAKE_PASSWORD=
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_WAREHOUSE=COMPUTE_WH
SNOWFLAKE_DATABASE=
SNOWFLAKE_SCHEMA=PUBLIC
SNOWFLAKE_ROLE=
```

Optional, for `snowflake_cost` only:

| Variable | Default |
|---|---|
| `MCP_HOST` | `127.0.0.1` |
| `MCP_PORT` | `8000` |

## Running

```powershell
# Performance advisor (HTTP)
python snowflake_cost\server.py

# Basic server (stdio, normally launched by the MCP client)
python snowflake_mcp\snowflake_server.py
```

### Connecting an MCP client

Example `mcp.json` entries (keep this file out of version control if it contains credentials):

```json
{
  "mcpServers": {
    "snowflake-cost": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    },
    "snowflake-mcp": {
      "command": "python",
      "args": ["snowflake_mcp/snowflake_server.py"]
    }
  }
}
```

## Security notes

- Use a dedicated Snowflake user with least-privilege access.
- Keep the advisor bound to `127.0.0.1`.
- Never commit `.env` or MCP client configs containing tokens.
