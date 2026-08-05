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

    import sqlglot
from sqlglot import expressions as exp


def normalize_sql(sql: str):
    """
    Convert SQL into a canonical form using sqlglot.
    """

    # Remove comments
    sql = re.sub(r'--.*', '', sql)

    # Parse SQL
    parsed = sqlglot.parse_one(
        sql,
        read="snowflake"
    )

    # For CREATE statements compare only the object definition,
    # not CREATE/REPLACE syntax.
    if isinstance(parsed, exp.Create):

        body = parsed.expression

        if body is None:
            return parsed.sql(
                dialect="snowflake",
                pretty=False,
                normalize=True
            )

        return body.sql(
            dialect="snowflake",
            pretty=False,
            normalize=True
        )

    return parsed.sql(
        dialect="snowflake",
        pretty=False,
        normalize=True
    )


# =====================================================
# Pretty Printer
# =====================================================

def pretty(sql):

    parsed = sqlglot.parse_one(
        sql,
        read="snowflake"
    )

    return parsed.sql(
        dialect="snowflake",
        pretty=True,
        normalize=True
    )


# =====================================================
# Extract Object Details using sqlglot
# =====================================================

def extract_object_details(sql):

    parsed = sqlglot.parse_one(
        sql,
        read="snowflake"
    )

    if not isinstance(parsed, exp.Create):
        raise Exception("No CREATE statement found.")

    object_type = parsed.args["kind"].upper()

    object_name = parsed.this.sql(
        dialect="snowflake",
        normalize=True
    )

    return object_type, object_name.upper()


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

    git_pretty = pretty(git_sql)

    print(f"Checking {object_type:<12} {object_name}")

    try:

        cur.execute(f"""
            SELECT GET_DDL(
                '{object_type}',
                '{object_name}'
            )
        """)

        prod_sql = normalize_sql(cur.fetchone()[0])

        prod_pretty = pretty(prod_sql)

    except Exception as ex:

        checked += 1
        failed += 1

        results.append({

            "status": "FAIL",
        
            "object": object_name,
        
            "git": git_pretty,
        
            "prod": prod_pretty
        
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