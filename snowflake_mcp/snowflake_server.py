import os
import re
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from snowflake.connector import connect

# Load credentials from the .env file next to this script (independent of the working directory)
load_dotenv(Path(__file__).resolve().parent / ".env")

# Initialize MCP v2 server
mcp = MCPServer("Snowflake MCP Server")

def get_snowflake_connection():
    """Read credentials from SNOWFLAKE_* environment variables and establish a Snowflake connection."""
    try:
        return connect(
            user=os.environ.get("SNOWFLAKE_USER"),
            password=os.environ.get("SNOWFLAKE_PASSWORD"),
            account=os.environ.get("SNOWFLAKE_ACCOUNT"),
            warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE"),
            database=os.environ.get("SNOWFLAKE_DATABASE"),
            schema=os.environ.get("SNOWFLAKE_SCHEMA"),
            role=os.environ.get("SNOWFLAKE_ROLE"),
            login_timeout=30,
            network_timeout=60,
        )
    except Exception as e:
        raise Exception(f"Configuration or connection error: {str(e)}")

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(\.[A-Za-z_][A-Za-z0-9_$]*){0,2}$")


def _check_identifier(name: str) -> str:
    """Allow only plain (optionally dot-qualified) SQL identifiers to prevent injection."""
    if not isinstance(name, str) or not _IDENTIFIER.match(name):
        raise ValueError("Invalid identifier")
    return name


# --- 1. CONNECTION & SESSION MANAGEMENT TOOLS ---

@mcp.tool()
def test_connection() -> str:
    """Test the connection to Snowflake using config.json and return current session details."""
    try:
        conn = get_snowflake_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()")
        row = cursor.fetchone()
        
        return (
            "Successfully connected to Snowflake!\n"
            f"- User: {row[0]}\n"
            f"- Role: {row[1]}\n"
            f"- Warehouse: {row[2]}\n"
            f"- Database: {row[3]}\n"
            f"- Schema: {row[4]}"
        )
    except Exception as e:
        return f"Failed to connect to Snowflake: {str(e)}"
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'conn' in locals(): conn.close()


# --- 2. SCHEMA INSPECTION TOOLS ---

@mcp.tool()
def list_tables() -> str:
    """List all tables available in the current Snowflake database and schema."""
    conn = get_snowflake_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[1] for row in cursor.fetchall()]
        return f"Tables in schema: {', '.join(tables)}"
    except Exception as e:
        return f"Error listing tables: {str(e)}"
    finally:
        cursor.close()
        conn.close()


# --- 3. READ OPERATION (SELECT) ---

@mcp.tool()
def read_data(query: str) -> str:
    """Execute a SELECT query against Snowflake to read data. 
    Args:
        query: A valid SQL SELECT statement.
    """
    stripped = query.strip().rstrip(";").strip()
    if not stripped.upper().startswith("SELECT") or ";" in stripped:
        return "Error: Only a single SELECT statement is allowed in read_data."
    
    conn = get_snowflake_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        results = cursor.fetchall()
        
        formatted_data = [dict(zip(columns, row)) for row in results]
        return str(formatted_data)
    except Exception as e:
        return f"Database error: {str(e)}"
    finally:
        cursor.close()
        conn.close()


# --- 4. CREATE OPERATION (INSERT) ---

@mcp.tool()
def create_record(table_name: str, columns: list[str], values: list[str]) -> str:
    """Insert a new record into a specified Snowflake table.
    Args:
        table_name: Name of the target table.
        columns: List of column names.
        values: List of corresponding string values to insert.
    """
    try:
        _check_identifier(table_name)
        for c in columns:
            _check_identifier(c)
    except ValueError:
        return "Error: Invalid table or column name."
    if not columns or len(columns) != len(values):
        return "Error: columns and values must be non-empty and the same length."

    conn = get_snowflake_connection()
    try:
        cursor = conn.cursor()
        cols_formatted = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(values))
        
        sql = f"INSERT INTO {table_name} ({cols_formatted}) VALUES ({placeholders})"
        cursor.execute(sql, values)
        conn.commit()
        return f"Successfully inserted record into {table_name}. Rows affected: {cursor.rowcount}"
    except Exception as e:
        conn.rollback()
        return f"Error executing insert: {str(e)}"
    finally:
        cursor.close()
        conn.close()


# --- 5. UPDATE OPERATION ---

@mcp.tool()
def update_record(table_name: str, set_column: str, set_value: str, condition_column: str, condition_value: str) -> str:
    """Update records in a Snowflake table.
    Args:
        table_name: Target table name.
        set_column: Column name to update.
        set_value: New value to set as a string.
        condition_column: Column name for the WHERE clause condition.
        condition_value: Value for the WHERE clause condition as a string.
    """
    try:
        _check_identifier(table_name)
        _check_identifier(set_column)
        _check_identifier(condition_column)
    except ValueError:
        return "Error: Invalid table or column name."

    conn = get_snowflake_connection()
    try:
        cursor = conn.cursor()
        sql = f"UPDATE {table_name} SET {set_column} = %s WHERE {condition_column} = %s"
        cursor.execute(sql, (set_value, condition_value))
        conn.commit()
        return f"Successfully updated {table_name}. Rows affected: {cursor.rowcount}"
    except Exception as e:
        conn.rollback()
        return f"Error executing update: {str(e)}"
    finally:
        cursor.close()
        conn.close()


# --- 6. DELETE OPERATION ---

@mcp.tool()
def delete_record(table_name: str, condition_column: str, condition_value: str) -> str:
    """Delete records from a Snowflake table based on a condition.
    Args:
        table_name: Target table name.
        condition_column: Column name for the WHERE clause condition.
        condition_value: Value for matching deletion as a string.
    """
    try:
        _check_identifier(table_name)
        _check_identifier(condition_column)
    except ValueError:
        return "Error: Invalid table or column name."

    conn = get_snowflake_connection()
    try:
        cursor = conn.cursor()
        sql = f"DELETE FROM {table_name} WHERE {condition_column} = %s"
        cursor.execute(sql, (condition_value,))
        conn.commit()
        return f"Successfully deleted records from {table_name}. Rows affected: {cursor.rowcount}"
    except Exception as e:
        conn.rollback()
        return f"Error executing delete: {str(e)}"
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    mcp.run()#transport="streamable-http",port=8000)