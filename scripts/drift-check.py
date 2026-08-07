import os
import re
import sys
from pathlib import Path
from difflib import unified_diff

import snowflake.connector


# ==========================================================
# Normalize SQL
# ==========================================================

def normalize_sql(sql: str) -> str:

    # Remove comments
    sql = re.sub(r'--.*', '', sql)

    # Remove CREATE OR REPLACE
    sql = re.sub(
        r'CREATE\s+OR\s+REPLACE',
        'CREATE',
        sql,
        flags=re.IGNORECASE
    )

    # Remove quotes
    sql = sql.replace('"', '')

    # Remove generated column list from GET_DDL
    sql = re.sub(
        r'(CREATE\s+VIEW\s+\S+)\s*\([^)]*\)\s*AS',
        r'\1 AS',
        sql,
        flags=re.IGNORECASE | re.DOTALL
    )

    # Collapse whitespace
    sql = re.sub(r'\s+', ' ', sql)

    return sql.upper().strip()


# ==========================================================
# Pretty SQL
# ==========================================================

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

    return sql.strip()


# ==========================================================
# Extract Object
# ==========================================================

def extract_object(sql):

    pattern = re.compile(
        r"""
        CREATE
        \s+
        (?:OR\s+REPLACE\s+)?
        (VIEW|TABLE|FUNCTION|PROCEDURE|TASK)
        \s+
        ([A-Za-z0-9_."$]+)
        """,
        re.IGNORECASE | re.VERBOSE
    )

    match = pattern.search(sql)

    if not match:
        raise Exception("Could not determine object.")

    object_type = match.group(1).upper()

    object_name = match.group(2)

    object_name = object_name.replace('"', '')

    # Remove database/schema if present
    object_name = object_name.split(".")[-1]

    return object_type, object_name.upper()


# ==========================================================
# Discover SQL Files
# ==========================================================

sql_files = list(Path("sql").rglob("*.sql"))

if not sql_files:
    print("No SQL files found.")
    sys.exit(1)


# ==========================================================
# Connect
# ==========================================================

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


# ==========================================================
# Compare
# ==========================================================

checked = 0
passed = 0
failed = 0

results = []

print("\nStarting Drift Detection...\n")

for sql_file in sql_files:

    raw_sql = sql_file.read_text()

    try:

        object_type, object_name = extract_object(raw_sql)

    except Exception as ex:

        print(f"Skipping {sql_file}")

        print(ex)

        continue

    print(f"Checking {object_type:<12} {object_name}")

    git_sql = normalize_sql(raw_sql)

    try:

        cur.execute(f"""
            SELECT GET_DDL(
                '{object_type}',
                '{object_name}'
            )
        """)

        prod_sql = cur.fetchone()[0]

        prod_sql = normalize_sql(prod_sql)

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


# ==========================================================
# Summary
# ==========================================================

print()

print("=" * 60)
print("DRIFT DETECTION REPORT")
print("=" * 60)

print(f"Objects Checked : {checked}")
print(f"No Drift        : {passed}")
print(f"Drift Detected  : {failed}")

print()

for result in results:

    if result["status"] == "PASS":

        print(f"✓ {result['object']}")

    elif result["status"] == "FAIL":

        print(f"✗ {result['object']}")

    else:

        print(f"! {result['object']} (Unable to Compare)")


# ==========================================================
# Differences
# ==========================================================

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


# ==========================================================
# Exit
# ==========================================================

if failed > 0:

    print("\n❌ Drift detected.")

    sys.exit(1)

print("\n✅ No drift detected.")

sys.exit(0)