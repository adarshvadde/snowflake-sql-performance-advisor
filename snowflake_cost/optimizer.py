import sqlglot
from sqlglot import exp

def analyze_query(sql: str):
    parsed = sqlglot.parse_one(sql, read="snowflake")

    recommendations = []
    sql_upper = sql.upper()

    if "SELECT *" in sql_upper:
        recommendations.append({
            "issue": "SELECT *",
            "severity": "medium",
            "recommendation":
                "Select only the columns required by downstream logic."
        })

    if "CROSS JOIN" in sql_upper:
        recommendations.append({
            "issue": "CROSS JOIN",
            "severity": "high",
            "recommendation":
                "Check whether the Cartesian product is intentional."
        })

    if "DISTINCT" in sql_upper:
        recommendations.append({
            "issue": "DISTINCT operation",
            "severity": "medium",
            "recommendation":
                "Check whether duplicates can be prevented earlier."
        })

    if "LIKE '%" in sql_upper:
        recommendations.append({
            "issue": "Leading wildcard",
            "severity": "medium",
            "recommendation":
                "Investigate whether an alternative filtering strategy "
                "can reduce scanning."
        })

    joins = list(parsed.find_all(exp.Join))

    return {
        "join_count": len(joins),
        "recommendation_count": len(recommendations),
        "recommendations": recommendations,
        "optimization_status":
            "REVIEW_REQUIRED" if recommendations else "NO_BASIC_ISSUES_FOUND"
    }