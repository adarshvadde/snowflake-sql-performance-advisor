import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import snowflake.connector
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

load_dotenv()

mcp = MCPServer("Snowflake AI & SQL Performance Advisor")

SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "COMPUTE_WH")
SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE")
SNOWFLAKE_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")
SNOWFLAKE_ROLE = os.getenv("SNOWFLAKE_ROLE")

MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))


# ---------------------------------------------------------
# Snowflake connection and result helpers
# ---------------------------------------------------------

def get_snowflake_connection():
    """Create a fresh Snowflake connection for each tool call."""
    required = {
        "SNOWFLAKE_USER": SNOWFLAKE_USER,
        "SNOWFLAKE_PASSWORD": SNOWFLAKE_PASSWORD,
        "SNOWFLAKE_ACCOUNT": SNOWFLAKE_ACCOUNT,
        "SNOWFLAKE_WAREHOUSE": SNOWFLAKE_WAREHOUSE,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "Missing Snowflake environment variables: " + ", ".join(missing)
        )

    config = {
        "user": SNOWFLAKE_USER,
        "password": SNOWFLAKE_PASSWORD,
        "account": SNOWFLAKE_ACCOUNT,
        "warehouse": SNOWFLAKE_WAREHOUSE,
        "login_timeout": 30,
        "network_timeout": 60,
    }

    if SNOWFLAKE_DATABASE:
        config["database"] = SNOWFLAKE_DATABASE
    if SNOWFLAKE_SCHEMA:
        config["schema"] = SNOWFLAKE_SCHEMA
    if SNOWFLAKE_ROLE:
        config["role"] = SNOWFLAKE_ROLE

    return snowflake.connector.connect(**config)


def normalize_value(value: Any):
    """Convert Snowflake values into JSON-friendly Python values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(v) for v in value]
    return value


def execute_query(sql: str, params=None):
    """Execute SQL and return rows as dictionaries."""
    connection = get_snowflake_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params or ())
        if cursor.description is None:
            return {"status": "success", "rowcount": cursor.rowcount}

        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        return [
            {
                column: normalize_value(value)
                for column, value in zip(columns, row)
            }
            for row in rows
        ]
    finally:
        cursor.close()
        connection.close()


def validate_read_only_select(query: str):
    """
    Basic guard for tools that issue EXPLAIN.
    EXPLAIN compiles a statement; this tool does not execute the SELECT.
    Only a single SELECT/WITH statement is accepted.
    """
    if not query or not query.strip():
        raise ValueError("SQL query cannot be empty.")

    cleaned = query.strip()
    if len(cleaned) > 20000:
        raise ValueError("SQL query is too long (maximum 20000 characters).")

    # Permit one optional trailing semicolon, but no multi-statement input.
    without_trailing_semicolon = cleaned[:-1] if cleaned.endswith(";") else cleaned
    if ";" in without_trailing_semicolon:
        raise ValueError("Only one SQL statement is allowed.")

    if not re.match(r"^(SELECT|WITH)\b", without_trailing_semicolon, re.I):
        raise ValueError("Only SELECT or WITH ... SELECT statements are allowed.")

    forbidden = re.compile(
        r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|"
        r"GRANT|REVOKE|CALL|COPY|PUT|GET|REMOVE|EXECUTE)\b",
        re.I,
    )
    if forbidden.search(without_trailing_semicolon):
        raise ValueError("The supplied SQL contains a disallowed operation.")

    return without_trailing_semicolon


def validate_query_id(query_id: str):
    """Accept Snowflake's UUID-shaped query IDs only."""
    if not query_id or not re.fullmatch(
        r"[0-9a-fA-F-]{20,100}", query_id.strip()
    ):
        raise ValueError("Invalid Snowflake query ID.")
    return query_id.strip()


def fetch_query_history(hours: int = 24, limit: int = 100):
    """Shared recent-query-history retrieval."""
    if not isinstance(hours, int) or not 1 <= hours <= 168:
        raise ValueError("hours must be an integer between 1 and 168.")
    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit must be an integer between 1 and 10000.")

    # hours and limit are validated integers, not arbitrary user SQL.
    sql = f"""
        SELECT
            QUERY_ID,
            QUERY_TEXT,
            QUERY_TYPE,
            DATABASE_NAME,
            SCHEMA_NAME,
            USER_NAME,
            WAREHOUSE_NAME,
            WAREHOUSE_SIZE,
            EXECUTION_STATUS,
            START_TIME,
            END_TIME,
            TOTAL_ELAPSED_TIME,
            EXECUTION_TIME,
            COMPILATION_TIME,
            QUEUED_PROVISIONING_TIME,
            QUEUED_OVERLOAD_TIME,
            BYTES_SCANNED,
            PERCENTAGE_SCANNED_FROM_CACHE,
            PARTITIONS_SCANNED,
            PARTITIONS_TOTAL,
            BYTES_SPILLED_TO_LOCAL_STORAGE,
            BYTES_SPILLED_TO_REMOTE_STORAGE,
            ROWS_PRODUCED,
            ERROR_MESSAGE
        FROM TABLE(
            INFORMATION_SCHEMA.QUERY_HISTORY(
                END_TIME_RANGE_START =>
                    DATEADD('HOUR', -{hours}, CURRENT_TIMESTAMP()),
                RESULT_LIMIT => {limit}
            )
        )
        ORDER BY START_TIME DESC
    """
    return execute_query(sql)


def get_query_history_record(query_id: str):
    """Find a query ID within the recent seven-day Information Schema window."""
    query_id = validate_query_id(query_id)
    sql = """
        SELECT
            QUERY_ID,
            QUERY_TEXT,
            QUERY_TYPE,
            DATABASE_NAME,
            SCHEMA_NAME,
            USER_NAME,
            WAREHOUSE_NAME,
            WAREHOUSE_SIZE,
            EXECUTION_STATUS,
            START_TIME,
            END_TIME,
            TOTAL_ELAPSED_TIME,
            EXECUTION_TIME,
            COMPILATION_TIME,
            QUEUED_PROVISIONING_TIME,
            QUEUED_OVERLOAD_TIME,
            BYTES_SCANNED,
            PERCENTAGE_SCANNED_FROM_CACHE,
            PARTITIONS_SCANNED,
            PARTITIONS_TOTAL,
            BYTES_SPILLED_TO_LOCAL_STORAGE,
            BYTES_SPILLED_TO_REMOTE_STORAGE,
            ROWS_PRODUCED,
            ERROR_MESSAGE
        FROM TABLE(
            INFORMATION_SCHEMA.QUERY_HISTORY(
                END_TIME_RANGE_START =>
                    DATEADD('DAY', -7, CURRENT_TIMESTAMP()),
                RESULT_LIMIT => 10000
            )
        )
        WHERE QUERY_ID = %s
        LIMIT 1
    """
    rows = execute_query(sql, (query_id,))
    return rows[0] if rows else None


# ---------------------------------------------------------
#  TOOL 1: EXPLAIN query
# ---------------------------------------------------------

@mcp.tool()
def test_and_explain_query(query: str) -> str:
    """
    Validate a single SELECT query with Snowflake EXPLAIN
    and return its tabular execution plan. Does not execute the SELECT.
    """
    try:
        safe_query = validate_read_only_select(query)
        plan = execute_query(f"EXPLAIN USING TABULAR {safe_query}")
        return (
            "Query parsed successfully. EXPLAIN plan follows. "
            "This is a compile-time plan, not actual runtime profiling:\n"
            f"{plan}"
        )
    except Exception as exc:
        return f"Query validation or EXPLAIN failed: {exc}"


# ---------------------------------------------------------
#  TOOL 2: query estimate (corrected)
# ---------------------------------------------------------

@mcp.tool()
def estimate_query_cost_and_time(
    query: str,
    warehouse_size: str = "X-Small"
) -> str:
    """
    Return an EXPLAIN plan and a clearly labelled heuristic complexity note.
    Snowflake EXPLAIN does not provide guaranteed runtime or dollar cost.
    """
    try:
        safe_query = validate_read_only_select(query)
        allowed_sizes = {
            "X-Small", "Small", "Medium", "Large",
            "X-Large", "2X-Large", "3X-Large", "4X-Large"
        }
        if warehouse_size not in allowed_sizes:
            raise ValueError(
                "Unsupported warehouse size. Use a standard Snowflake size."
            )

        plan = execute_query(f"EXPLAIN USING TABULAR {safe_query}")
        upper = safe_query.upper()
        observations = []
        if re.search(r"\bSELECT\s+\*", upper):
            observations.append("SELECT * detected; consider selecting required columns.")
        if " JOIN " in f" {upper} ":
            observations.append("JOIN detected; inspect join cardinality and profile statistics.")
        if " ORDER BY " in f" {upper} ":
            observations.append("ORDER BY detected; check whether sorting all rows is necessary.")

        return (
            f"EXPLAIN completed for requested warehouse size {warehouse_size}.\n"
            "Actual runtime and USD cost cannot be reliably calculated from EXPLAIN alone.\n"
            f"Plan rows: {len(plan)}\n"
            f"SQL observations: {observations or ['No basic pattern flags detected.']}\n"
            f"EXPLAIN plan: {plan}"
        )
    except Exception as exc:
        return f"Could not analyze query: {exc}"


# ---------------------------------------------------------
#  TOOL 3: SQL pattern checks
# ---------------------------------------------------------

@mcp.tool()
def check_query_optimization(query: str) -> str:
    """
    Check SQL text for common performance patterns.
    This is a heuristic review, not a full SQL optimizer.
    """
    if not query or len(query) > 20000:
        return "Provide a SQL query between 1 and 20000 characters."

    query_upper = query.upper()
    issues = []

    if re.search(r"\bSELECT\s+\*", query_upper):
        issues.append(
            "SELECT * detected: select only required columns where practical."
        )
    if " JOIN " in f" {query_upper} " and " ON " not in f" {query_upper} ":
        issues.append(
            "JOIN detected without an obvious ON clause; inspect whether this is intentional."
        )
    if "LIKE '%" in query_upper:
        issues.append(
            "Leading wildcard LIKE detected; evaluate search requirements and available optimization options."
        )
    if "ORDER BY" in query_upper and "LIMIT" not in query_upper:
        issues.append(
            "ORDER BY without LIMIT detected; check whether sorting the full result is necessary."
        )
    if re.search(r"\b(YEAR|MONTH|DAY|DATE_PART|EXTRACT)\s*\(", query_upper):
        issues.append(
            "A date/time function appears in the SQL; check whether a direct range predicate is more efficient."
        )

    if not issues:
        return (
            "No common heuristic anti-patterns detected. "
            "Use actual query history and query profile metrics for deeper analysis."
        )

    return "SQL Optimization Audit (heuristic findings):\n- " + "\n- ".join(issues)


# ---------------------------------------------------------
#  TOOL 4: completed query run evaluation
# ---------------------------------------------------------

@mcp.tool()
def evaluate_good_run_metrics(query_id: str) -> str:
    """
    Evaluate a completed query using query-history execution,
    queue, cache and spill metrics.
    """
    try:
        row = get_query_history_record(query_id)
        if not row:
            return (
                f"Query ID {query_id} was not found in the accessible "
                "recent seven-day Information Schema history."
            )

        findings = []
        good_run = True

        status = row.get("EXECUTION_STATUS")
        if status == "SUCCESS":
            findings.append("Execution status: SUCCESS")
        else:
            good_run = False
            findings.append(f"Execution status: {status}")

        remote_spill = row.get("BYTES_SPILLED_TO_REMOTE_STORAGE") or 0
        local_spill = row.get("BYTES_SPILLED_TO_LOCAL_STORAGE") or 0
        queue_time = row.get("QUEUED_OVERLOAD_TIME") or 0
        scanned = row.get("BYTES_SCANNED") or 0
        cache_pct = row.get("PERCENTAGE_SCANNED_FROM_CACHE")

        if remote_spill > 0:
            good_run = False
            findings.append(
                f"Remote spill detected: {remote_spill} bytes. "
                "Investigate the query profile and memory-intensive operators."
            )
        if local_spill > 0:
            findings.append(
                f"Local spill detected: {local_spill} bytes. "
                "Investigate sorts, joins and aggregation operators."
            )
        if queue_time > 5000:
            findings.append(
                f"Warehouse overload queue time is {queue_time} ms; "
                "investigate concurrency and warehouse load."
            )
        if cache_pct is not None:
            findings.append(f"Percentage scanned from cache: {cache_pct}")
        findings.append(f"Bytes scanned: {scanned}")
        findings.append(
            f"Execution time: {row.get('EXECUTION_TIME')} ms; "
            f"total elapsed: {row.get('TOTAL_ELAPSED_TIME')} ms."
        )

        verdict = "GOOD RUN (based on selected checks)" if good_run else "NEEDS INVESTIGATION"
        return f"Evaluation: {verdict}\n" + "\n".join(findings)
    except Exception as exc:
        return f"Error evaluating query history: {exc}"


# ---------------------------------------------------------
#  TOOL 5: report (corrected, no invented runtime/cost)
# ---------------------------------------------------------

@mcp.tool()
def generate_bob_snowflake_report(query: str) -> str:
    """
    Generate a report from the actual Snowflake EXPLAIN plan
    and heuristic SQL text checks. Does not invent runtime or cost.
    """
    try:
        safe_query = validate_read_only_select(query)
        plan = execute_query(f"EXPLAIN USING TABULAR {safe_query}")
        audit = check_query_optimization(safe_query)

        return (
            "### Snowflake SQL Analysis Report\n"
            "**EXPLAIN status:** Successful\n"
            f"**EXPLAIN plan rows:** {len(plan)}\n\n"
            "### SQL pattern review\n"
            f"{audit}\n\n"
            "### EXPLAIN plan\n"
            f"{plan}\n\n"
            "### Important limitation\n"
            "EXPLAIN is not actual execution profiling. "
            "Run the query in your normal controlled workflow, then use its "
            "query ID with get_query_details and get_query_profile to inspect "
            "actual runtime metrics."
        )
    except Exception as exc:
        return f"Error generating Snowflake report: {exc}"


# ---------------------------------------------------------
#  TOOL 6: recent query history
# ---------------------------------------------------------

@mcp.tool()
def get_query_history(hours: int = 24, limit: int = 100):
    """
    Retrieve recent query history from the current database's Information Schema.
    hours: 1..168; limit: 1..10000.
    """
    try:
        return fetch_query_history(hours, limit)
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------
#  TOOL 7: find slow queries
# ---------------------------------------------------------

@mcp.tool()
def find_slow_queries(
    threshold_seconds: int = 10,
    hours: int = 24,
    limit: int = 100
):
    """
    Find queries whose execution time meets or exceeds the threshold.
    Snowflake EXECUTION_TIME is measured in milliseconds.
    """
    try:
        if not isinstance(threshold_seconds, int) or not 1 <= threshold_seconds <= 86400:
            raise ValueError("threshold_seconds must be between 1 and 86400.")

        rows = fetch_query_history(hours, limit)
        threshold_ms = threshold_seconds * 1000

        slow = [
            row for row in rows
            if (row.get("EXECUTION_TIME") or 0) >= threshold_ms
        ]
        slow.sort(
            key=lambda row: row.get("EXECUTION_TIME") or 0,
            reverse=True
        )
        return {
            "threshold_seconds": threshold_seconds,
            "hours": hours,
            "matching_queries": len(slow),
            "queries": slow
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------
#  TOOL 8: details for one query ID
# ---------------------------------------------------------

@mcp.tool()
def get_query_details(query_id: str):
    """Retrieve accessible recent execution details for a Snowflake query ID."""
    try:
        row = get_query_history_record(query_id)
        if not row:
            return {
                "query_id": query_id,
                "message": "Query not found in accessible recent history."
            }
        return row
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------
#  TOOL 9: query performance statistics
# ---------------------------------------------------------

@mcp.tool()
def get_query_statistics(hours: int = 24, limit: int = 1000):
    """Summarize execution, elapsed time, failures and bytes scanned."""
    try:
        rows = fetch_query_history(hours, limit)
        if not rows:
            return {"message": "No query history found for the requested period."}

        execution_times = [
            row.get("EXECUTION_TIME") or 0 for row in rows
        ]
        elapsed_times = [
            row.get("TOTAL_ELAPSED_TIME") or 0 for row in rows
        ]
        failed = sum(
            1 for row in rows
            if str(row.get("EXECUTION_STATUS", "")).upper()
            in {"FAIL", "FAILED", "FAILED_WITH_ERROR", "FAILED_WITH_INCIDENT"}
        )

        return {
            "hours": hours,
            "queries_returned": len(rows),
            "failed_queries": failed,
            "average_execution_ms": round(
                sum(execution_times) / len(execution_times), 2
            ),
            "maximum_execution_ms": max(execution_times),
            "minimum_execution_ms": min(execution_times),
            "average_total_elapsed_ms": round(
                sum(elapsed_times) / len(elapsed_times), 2
            ),
            "total_bytes_scanned": sum(
                row.get("BYTES_SCANNED") or 0 for row in rows
            ),
            "total_bytes_spilled_local": sum(
                row.get("BYTES_SPILLED_TO_LOCAL_STORAGE") or 0
                for row in rows
            ),
            "total_bytes_spilled_remote": sum(
                row.get("BYTES_SPILLED_TO_REMOTE_STORAGE") or 0
                for row in rows
            )
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------
#  TOOL 10: warehouse credit usage
# ---------------------------------------------------------

@mcp.tool()
def get_warehouse_usage(days: int = 7):
    """
    Retrieve account-level warehouse credit usage from ACCOUNT_USAGE.
    Data can be delayed and requires appropriate privileges.
    """
    try:
        if not isinstance(days, int) or not 1 <= days <= 30:
            raise ValueError("days must be between 1 and 30.")

        sql = f"""
            SELECT
                WAREHOUSE_NAME,
                SUM(CREDITS_USED) AS TOTAL_CREDITS,
                SUM(CREDITS_USED_COMPUTE) AS COMPUTE_CREDITS,
                SUM(CREDITS_USED_CLOUD_SERVICES) AS CLOUD_SERVICES_CREDITS
            FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
            WHERE START_TIME >= DATEADD(
                'DAY', -{days}, CURRENT_TIMESTAMP()
            )
            GROUP BY WAREHOUSE_NAME
            ORDER BY TOTAL_CREDITS DESC
        """
        return execute_query(sql)
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------
#  TOOL 11: compare two query executions
# ---------------------------------------------------------

@mcp.tool()
def compare_query_performance(
    first_query_id: str,
    second_query_id: str
):
    """Compare execution metrics for two accessible query IDs."""
    try:
        first = get_query_history_record(first_query_id)
        second = get_query_history_record(second_query_id)

        if not first:
            return {"error": f"First query not found: {first_query_id}"}
        if not second:
            return {"error": f"Second query not found: {second_query_id}"}

        first_ms = first.get("EXECUTION_TIME") or 0
        second_ms = second.get("EXECUTION_TIME") or 0

        if first_ms < second_ms:
            faster_id = first_query_id
        elif second_ms < first_ms:
            faster_id = second_query_id
        else:
            faster_id = "Equal execution time"

        difference = abs(first_ms - second_ms)
        percent = (
            round(difference / max(first_ms, second_ms) * 100, 2)
            if max(first_ms, second_ms) else 0
        )

        return {
            "first_query": first,
            "second_query": second,
            "execution_time_difference_ms": difference,
            "difference_percent_of_slower_query": percent,
            "faster_query": faster_id,
            "note": (
                "Compare similar SQL, data volume, warehouse size and cache "
                "conditions before attributing the difference to a code change."
            )
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------
#  TOOL 12: actual operator-level query profile
# ---------------------------------------------------------

@mcp.tool()
def get_query_profile(query_id: str):
    """
    Retrieve Snowflake operator statistics for a completed query.
    Requires supported query history and warehouse privileges.
    """
    try:
        query_id = validate_query_id(query_id)

        # query_id is restricted to hexadecimal characters and hyphens.
        sql = f"""
            SELECT
                QUERY_ID,
                STEP_ID,
                OPERATOR_ID,
                OPERATOR_TYPE,
                OPERATOR_STATISTICS,
                EXECUTION_TIME_BREAKDOWN,
                OPERATOR_ATTRIBUTES
            FROM TABLE(GET_QUERY_OPERATOR_STATS('{query_id}'))
            ORDER BY STEP_ID, OPERATOR_ID
        """
        rows = execute_query(sql)

        if not rows:
            return {
                "query_id": query_id,
                "message": "No operator statistics were returned."
            }

        return {
            "query_id": query_id,
            "operator_count": len(rows),
            "operators": rows
        }
    except Exception as exc:
        return {
            "query_id": query_id,
            "error": str(exc),
            "hint": (
                "Check that the query completed within the supported history "
                "window and that your role has the required warehouse privileges."
            )
        }

# =========================================================
# ETL PIPELINE MONITORING MODULE
# =========================================================

def fetch_etl_task_history(
    hours: int = 24,
    limit: int = 100,
    task_name: str = None,
    database_name: str = None,
    schema_name: str = None,
    error_only: bool = False
):
    """
    Retrieve Snowflake ETL task execution history.

    Uses Snowflake INFORMATION_SCHEMA.TASK_HISTORY.
    Supports a maximum seven-day execution history window.
    """

    if not isinstance(hours, int) or not 1 <= hours <= 168:
        raise ValueError("hours must be between 1 and 168.")

    if not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000.")

    if schema_name and not database_name:
        raise ValueError(
            "database_name is required when schema_name is supplied."
        )

    arguments = [
        f"SCHEDULED_TIME_RANGE_START => "
        f"DATEADD('HOUR', -{hours}, CURRENT_TIMESTAMP())",
        "SCHEDULED_TIME_RANGE_END => CURRENT_TIMESTAMP()",
        f"RESULT_LIMIT => {limit}",
        f"ERROR_ONLY => {'TRUE' if error_only else 'FALSE'}"
    ]

    if task_name:
        safe_task_name = task_name.replace("'", "''")
        arguments.append(f"TASK_NAME => '{safe_task_name}'")

    if database_name:
        safe_database = database_name.replace("'", "''")
        arguments.append(f"DATABASE_NAME => '{safe_database}'")

    if schema_name:
        safe_schema = schema_name.replace("'", "''")
        arguments.append(f"SCHEMA_NAME => '{safe_schema}'")

    sql = f"""
        SELECT
            NAME,
            DATABASE_NAME,
            SCHEMA_NAME,
            QUERY_ID,
            STATE,
            ERROR_CODE,
            ERROR_MESSAGE,
            SCHEDULED_TIME,
            QUERY_START_TIME,
            COMPLETED_TIME,
            ROOT_TASK_ID,
            GRAPH_VERSION,
            RUN_ID,
            ATTEMPT_NUMBER,
            QUERY_TEXT
        FROM TABLE(
            SNOWFLAKE.INFORMATION_SCHEMA.TASK_HISTORY(
                {", ".join(arguments)}
            )
        )
        ORDER BY SCHEDULED_TIME DESC
    """

    return execute_query(sql)


# =========================================================
# TOOL 13: GET ETL TASK HISTORY
# =========================================================

@mcp.tool()
def get_etl_task_history(
    hours: int = 24,
    limit: int = 100,
    task_name: str = None,
    database_name: str = None,
    schema_name: str = None
):
    """
    Retrieve Snowflake ETL task execution history.

    Use this tool to monitor recent pipeline executions,
    task status, execution time and error information.
    """

    try:
        rows = fetch_etl_task_history(
            hours=hours,
            limit=limit,
            task_name=task_name,
            database_name=database_name,
            schema_name=schema_name
        )

        return {
            "status": "success",
            "monitoring_period_hours": hours,
            "total_executions": len(rows),
            "executions": rows
        }

    except Exception as exc:
        return {"error": str(exc)}


# =========================================================
# TOOL 14: GET FAILED ETL TASKS
# =========================================================

@mcp.tool()
def get_failed_etl_tasks(
    hours: int = 24,
    limit: int = 100,
    database_name: str = None,
    schema_name: str = None
):
    """
    Find failed or cancelled Snowflake ETL task executions.
    """

    try:
        rows = fetch_etl_task_history(
            hours=hours,
            limit=limit,
            database_name=database_name,
            schema_name=schema_name,
            error_only=True
        )

        return {
            "status": "success",
            "monitoring_period_hours": hours,
            "failed_execution_count": len(rows),
            "failed_tasks": rows
        }

    except Exception as exc:
        return {"error": str(exc)}


# =========================================================
# TOOL 15: GET ETL TASK DETAILS
# =========================================================

@mcp.tool()
def get_etl_task_details(
    task_name: str,
    hours: int = 24,
    limit: int = 20,
    database_name: str = None,
    schema_name: str = None
):
    """
    Retrieve execution details for a particular Snowflake task.
    """

    try:
        if not task_name or not task_name.strip():
            raise ValueError("task_name cannot be empty.")

        rows = fetch_etl_task_history(
            hours=hours,
            limit=limit,
            task_name=task_name,
            database_name=database_name,
            schema_name=schema_name
        )

        return {
            "task_name": task_name,
            "total_executions": len(rows),
            "execution_history": rows
        }

    except Exception as exc:
        return {"error": str(exc)}


# =========================================================
# TOOL 16: ETL PIPELINE HEALTH
# =========================================================

@mcp.tool()
def get_etl_pipeline_health(
    hours: int = 24,
    limit: int = 1000,
    database_name: str = None,
    schema_name: str = None
):
    """
    Generate an ETL health summary based on task execution history.
    """

    try:
        rows = fetch_etl_task_history(
            hours=hours,
            limit=limit,
            database_name=database_name,
            schema_name=schema_name
        )

        states = {}

        for row in rows:
            state = str(row.get("STATE") or "UNKNOWN").upper()
            states[state] = states.get(state, 0) + 1

        failed = states.get("FAILED", 0)
        cancelled = states.get("CANCELLED", 0)
        succeeded = states.get("SUCCEEDED", 0)
        running = states.get("EXECUTING", 0)

        completed = failed + cancelled + succeeded

        success_rate = (
            round(succeeded / completed * 100, 2)
            if completed else 0
        )

        if failed or cancelled:
            health = "NEEDS_ATTENTION"
        elif running:
            health = "RUNNING"
        elif succeeded:
            health = "HEALTHY"
        else:
            health = "NO_EXECUTIONS"

        return {
            "health_status": health,
            "monitoring_period_hours": hours,
            "total_executions": len(rows),
            "successful_executions": succeeded,
            "failed_executions": failed,
            "cancelled_executions": cancelled,
            "running_executions": running,
            "success_rate_percent": success_rate,
            "execution_states": states
        }

    except Exception as exc:
        return {"error": str(exc)}


# =========================================================
# TOOL 17: ANALYZE ETL TASK FAILURE
# =========================================================

@mcp.tool()
def analyze_etl_task_failure(
    task_name: str,
    hours: int = 24,
    database_name: str = None,
    schema_name: str = None
):
    """
    Analyze the latest failed or cancelled execution
    of a particular ETL task.
    """

    try:
        if not task_name or not task_name.strip():
            raise ValueError("task_name cannot be empty.")

        rows = fetch_etl_task_history(
            hours=hours,
            limit=100,
            task_name=task_name,
            database_name=database_name,
            schema_name=schema_name,
            error_only=True
        )

        if not rows:
            return {
                "task_name": task_name,
                "status": "NO_FAILURE_FOUND",
                "message": "No failed or cancelled executions found."
            }

        latest = rows[0]

        error_message = (
            latest.get("ERROR_MESSAGE") or "No error message available."
        )

        error_text = error_message.lower()

        if "permission" in error_text or "privilege" in error_text:
            category = "ACCESS_OR_PERMISSION_ERROR"
            recommendation = (
                "Check task owner role and required object privileges."
            )

        elif "does not exist" in error_text:
            category = "MISSING_OBJECT_ERROR"
            recommendation = (
                "Verify that referenced tables, views or schemas exist."
            )

        elif "timeout" in error_text or "timed out" in error_text:
            category = "TIMEOUT_ERROR"
            recommendation = (
                "Investigate query execution time and warehouse availability."
            )

        elif "compilation" in error_text or "syntax" in error_text:
            category = "SQL_COMPILATION_ERROR"
            recommendation = (
                "Review the task SQL definition and correct the SQL error."
            )

        else:
            category = "UNKNOWN_ERROR"
            recommendation = (
                "Inspect the Snowflake error message and task SQL definition."
            )

        return {
            "task_name": task_name,
            "execution_state": latest.get("STATE"),
            "scheduled_time": latest.get("SCHEDULED_TIME"),
            "completed_time": latest.get("COMPLETED_TIME"),
            "error_code": latest.get("ERROR_CODE"),
            "error_message": error_message,
            "error_category": category,
            "recommendation": recommendation,
            "query_id": latest.get("QUERY_ID")
        }

    except Exception as exc:
        return {"error": str(exc)}


# =========================================================
# TOOL 18: GENERATE ETL MONITORING REPORT
# =========================================================

@mcp.tool()
def generate_etl_monitoring_report(
    hours: int = 24,
    limit: int = 1000,
    database_name: str = None,
    schema_name: str = None
):
    """
    Generate a consolidated Snowflake ETL monitoring report.
    """

    try:
        rows = fetch_etl_task_history(
            hours=hours,
            limit=limit,
            database_name=database_name,
            schema_name=schema_name
        )

        if not rows:
            return {
                "report_status": "NO_DATA",
                "message": "No ETL executions found for this period."
            }

        states = {}

        for row in rows:
            state = str(row.get("STATE") or "UNKNOWN").upper()
            states[state] = states.get(state, 0) + 1

        succeeded = states.get("SUCCEEDED", 0)
        failed = states.get("FAILED", 0)
        cancelled = states.get("CANCELLED", 0)

        completed = succeeded + failed + cancelled

        success_rate = (
            round(succeeded / completed * 100, 2)
            if completed else 0
        )

        failures = [
            {
                "task_name": row.get("NAME"),
                "state": row.get("STATE"),
                "error_code": row.get("ERROR_CODE"),
                "error_message": row.get("ERROR_MESSAGE"),
                "scheduled_time": row.get("SCHEDULED_TIME")
            }
            for row in rows
            if str(row.get("STATE", "")).upper()
            in {"FAILED", "CANCELLED"}
        ]

        report_status = (
            "NEEDS_ATTENTION" if failed or cancelled else "HEALTHY"
        )

        return {
            "report_title": "Snowflake ETL Monitoring Report",
            "report_status": report_status,
            "monitoring_period_hours": hours,
            "total_executions": len(rows),
            "successful_executions": succeeded,
            "failed_executions": failed,
            "cancelled_executions": cancelled,
            "success_rate_percent": success_rate,
            "execution_states": states,
            "failed_task_details": failures,
            "generated_at": datetime.now().astimezone().isoformat()
        }

    except Exception as exc:
        return {"error": str(exc)}
# ---------------------------------------------------------
# Start Streamable HTTP MCP server
# ---------------------------------------------------------

if __name__ == "__main__":
    print("Starting Snowflake AI & SQL Performance Advisor...")
    print(f"MCP endpoint: http://{MCP_HOST}:{MCP_PORT}/mcp")

    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT
    )
