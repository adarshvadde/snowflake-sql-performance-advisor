import sqlglot
from sqlglot import exp

def validate_sql(sql: str):
    try:
        statements = sqlglot.parse(sql, read="snowflake")

        if len(statements) != 1 or statements[0] is None:
            return {
                "valid": False,
                "reason": "Only one SQL statement is allowed."
            }

        statement = statements[0]

        if not isinstance(statement, exp.Query):
            return {
                "valid": False,
                "reason": "Only SELECT queries are allowed."
            }

        return {
            "valid": True,
            "query_type": "SELECT",
            "message": "SQL passed read-only structural validation."
        }

    except Exception as exc:
        return {
            "valid": False,
            "reason": str(exc)
        }