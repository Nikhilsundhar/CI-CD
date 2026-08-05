import os
import re
import sys
from pathlib import Path
from difflib import unified_diff

import snowflake.connector
import sqlglot
from sqlglot import expressions as exp


# =====================================================
# Normalize SQL
# =====================================================

def normalize_sql(sql: str):

    sql = re.sub(r'--.*', '', sql)

    sql = sql.replace('"', '')

    sql = re.sub(
        r'CREATE\s+OR\s+REPLACE',
        'CREATE',
        sql,
        flags=re.IGNORECASE
    )

    sql = re.sub(
        r'(CREATE\s+VIEW\s+\S+)\s*\([^)]*\)\s*AS',
        r'\1 AS',
        sql,
        flags=re.IGNORECASE | re.DOTALL
    )

    sql = re.sub(r'\s+', ' ', sql)

    return sql.upper().strip()


# =====================================================
# Pretty Printer
# =====================================================

def pretty(sql):

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

    return sql


# =====================================================
# Extract Object Details using sqlglot
# =====================================================

def extract_object_details(sql):

    expressions = sqlglot.parse(sql, read="snowflake")

    for expression in expressions:

        if isinstance(expression, exp.Create):

            object_type = expression.args["kind"].upper()

            object_name = expression.this.sql(
                dialect="snowflake"
            ).replace('"', '').upper()

            return object_type, object_name

    raise Exception("No CREATE statement found.")


# =====================================================
# Find SQL Files
# =====================================================

sql_files = list(Path("sql").rglob("*.sql"))

if not sql_files:

    print("No SQL files found.")

    sys.exit(1)


# =====================================================
# Connect
# =====================================================

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

checked = 0
passed = 0
failed = 0

results = []

print("\nStarting Drift Detection...\n")


# =====================================================
# Compare every object
# =====================================================

for sql_file in sql_files:

    raw_sql = sql_file.read_text()

    try:

        object_type, object_name = extract_object_details(raw_sql)

    except Exception as ex:

        print(f"Skipping {sql_file}")

        print(ex)

        continue

    git_sql = normalize_sql(raw_sql)

    print(f"Checking {object_type:<12} {object_name}")

    try:

        cur.execute(f"""
            SELECT GET_DDL(
                '{object_type}',
                '{object_name}'
            )
        """)

        prod_sql = normalize_sql(cur.fetchone()[0])

    except Exception as ex:

        checked += 1
        failed += 1

        results.append({

            "status": "ERROR",

            "object": object_name,

            "message": str(ex)

        })

        continue

    checked += 1

    if git_sql == prod_sql:

        passed += 1

        results.append({

            "status": "PASS",

            "object": object_name

        })

    else:

        failed += 1

        results.append({

            "status": "FAIL",

            "object": object_name,

            "git": pretty(git_sql),

            "prod": pretty(prod_sql)

        })


cur.close()

conn.close()


# =====================================================
# Summary
# =====================================================

print()

print("=" * 60)

print("DRIFT DETECTION REPORT")

print("=" * 60)

print(f"Objects Checked : {checked}")

print(f"No Drift        : {passed}")

print(f"Drift Detected  : {failed}")

print("=" * 60)

print()

for result in results:

    if result["status"] == "PASS":

        print(f"✓ {result['object']}")

    elif result["status"] == "FAIL":

        print(f"✗ {result['object']}")

    else:

        print(f"! {result['object']} (Unable to Compare)")


# =====================================================
# Detailed Diff
# =====================================================

for result in results:

    if result["status"] != "FAIL":

        continue

    print()

    print("=" * 60)

    print(result["object"])

    print("=" * 60)

    diff = unified_diff(

        result["git"].splitlines(),

        result["prod"].splitlines(),

        fromfile="Git",

        tofile="Production",

        lineterm=""

    )

    for line in diff:

        print(line)


# =====================================================
# Exit
# =====================================================

if failed > 0:

    print("\n❌ Drift detected.")

    sys.exit(1)

print("\n✅ No drift detected.")

sys.exit(0)