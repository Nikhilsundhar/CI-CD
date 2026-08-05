import os
import re
import sys
from pathlib import Path
from difflib import unified_diff

import snowflake.connector


# --------------------------------------------------------------------
# Normalize SQL
# --------------------------------------------------------------------
def normalize_sql(sql: str) -> str:
    """
    Normalize SQL before comparison.
    """

    # Remove comments
    sql = re.sub(r'--.*', '', sql)

    # Remove CREATE OR REPLACE
    sql = re.sub(
        r'CREATE\s+OR\s+REPLACE',
        'CREATE',
        sql,
        flags=re.IGNORECASE
    )

    # Remove quoted identifiers
    sql = sql.replace('"', '')

    # Remove generated view column list from GET_DDL
    sql = re.sub(
        r'(CREATE\s+VIEW\s+\S+)\s*\([^)]*\)\s*AS',
        r'\1 AS',
        sql,
        flags=re.IGNORECASE | re.DOTALL
    )

    # Collapse whitespace
    sql = re.sub(r'\s+', ' ', sql)

    return sql.upper().strip()


# --------------------------------------------------------------------
# Pretty formatting
# --------------------------------------------------------------------
def pretty(sql: str) -> str:

    keywords = [
        "SELECT",
        "FROM",
        "WHERE",
        "GROUP BY",
        "ORDER BY",
        "HAVING",
        "LEFT JOIN",
        "RIGHT JOIN",
        "INNER JOIN",
        "JOIN",
        "ON",
        "AS"
    ]

    for kw in keywords:
        sql = sql.replace(f" {kw} ", f"\n{kw} ")

    sql = sql.replace(",", ",\n")

    return sql.strip()


# --------------------------------------------------------------------
# Read Git SQL
# --------------------------------------------------------------------
git_file = Path("sql/views/create_view.sql")

if not git_file.exists():
    print("Git SQL file not found.")
    sys.exit(1)

git_sql = git_file.read_text()


# --------------------------------------------------------------------
# Connect to Snowflake
# --------------------------------------------------------------------
print("Connecting to Snowflake...")

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    password=os.environ["SNOWFLAKE_PASSWORD"],
    warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    role=os.environ["SNOWFLAKE_ROLE"],
    database=os.environ["SNOWFLAKE_DATABASE"],
    schema=os.environ["SNOWFLAKE_SCHEMA"]
)

cur = conn.cursor()

print("Fetching Production View DDL...")

cur.execute("""
SELECT GET_DDL(
    'VIEW',
    'VW_GOLD_CUSTOMER_ACTIVITY'
)
""")

prod_sql = cur.fetchone()[0]

cur.close()
conn.close()


# --------------------------------------------------------------------
# Normalize
# --------------------------------------------------------------------
git_sql = normalize_sql(git_sql)
prod_sql = normalize_sql(prod_sql)

print("\n===== NORMALIZED GIT =====")
print(git_sql)

print("\n===== NORMALIZED PROD =====")
print(prod_sql)


# --------------------------------------------------------------------
# Compare
# --------------------------------------------------------------------
if git_sql == prod_sql:

    print("\n====================================")
    print(" Drift Detection Report")
    print("====================================")
    print("✅ No drift detected.")

    sys.exit(0)


# --------------------------------------------------------------------
# Pretty report
# --------------------------------------------------------------------
git_pretty = pretty(git_sql)
prod_pretty = pretty(prod_sql)

print("\n====================================")
print(" Drift Detection Report")
print("====================================")

print("\n❌ Drift detected!\n")

print("--------------- Git ----------------\n")
print(git_pretty)

print("\n----------- Production -------------\n")
print(prod_pretty)

print("\n------------- Difference -----------\n")

diff = unified_diff(
    git_pretty.splitlines(),
    prod_pretty.splitlines(),
    fromfile="Git",
    tofile="Production",
    lineterm=""
)

for line in diff:
    print(line)

sys.exit(1)