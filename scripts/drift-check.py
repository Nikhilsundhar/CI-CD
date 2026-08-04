import os
import re
import sys
from pathlib import Path
from difflib import unified_diff

import snowflake.connector


def normalize_sql(sql: str) -> str:
    """Normalize SQL for comparison."""

    # Remove comments
    sql = re.sub(r'--.*', '', sql)

    # Remove CREATE OR REPLACE prefix because GET_DDL formats it differently
    sql = re.sub(
        r'CREATE\s+OR\s+REPLACE\s+VIEW\s+',
        'CREATE VIEW ',
        sql,
        flags=re.IGNORECASE
    )

    # Collapse whitespace
    sql = re.sub(r'\s+', ' ', sql)

    return sql.strip().upper()


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

git_sql = Path("sql/create_view.sql").read_text()

prod_sql = normalize_sql(prod_sql)
git_sql = normalize_sql(git_sql)

print()

if git_sql == prod_sql:
    print("✅ No drift detected.")
    sys.exit(0)

print("❌ Drift detected!\n")

diff = unified_diff(
    git_sql.split(),
    prod_sql.split(),
    fromfile="Git",
    tofile="Production",
    lineterm=""
)

for line in diff:
    print(line)

sys.exit(1)