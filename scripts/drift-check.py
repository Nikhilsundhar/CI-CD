import os
import re
import sys
from pathlib import Path
from difflib import unified_diff

import snowflake.connector
from snowflake.connector.errors import ProgrammingError


# ==========================================================
# Config
# ==========================================================

VALID_TYPES = {
    "tables": "TABLE",
    "views": "VIEW",
    "stored_procedures": "PROCEDURE",
    "functions": "FUNCTION",
    "tasks": "TASK",
}

# Snowflake error code for "object does not exist or not authorized"
OBJECT_NOT_FOUND_ERRNO = 2003


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
# Resolve object identity from folder structure
#   sql/<layer>/<schema>/<type_folder>/<object_name>.sql
#   e.g. sql/silver/slvr_sales/views/customer_orders_view.sql
# ==========================================================

def parse_object_from_path(sql_file: Path, base: str = "sql"):

    parts = sql_file.parts

    try:
        idx = parts.index(base)
    except ValueError:
        raise ValueError(f"'{sql_file}' is not under a '{base}/' root")

    try:
        layer, schema, type_folder, filename = parts[idx + 1: idx + 5]
    except ValueError:
        raise ValueError(
            f"'{sql_file}' does not match sql/<layer>/<schema>/<type>/<file>.sql"
        )

    object_type = VALID_TYPES.get(type_folder.lower())

    if not object_type:
        raise ValueError(
            f"Unknown object-type folder '{type_folder}' in '{sql_file}' "
            f"(expected one of {sorted(VALID_TYPES)})"
        )

    object_name = Path(filename).stem.upper()

    return object_type, schema.upper(), object_name


# ==========================================================
# Render {{ DATABASE }} / {{ SCHEMA }} placeholders
# ==========================================================

def render_placeholders(sql_text: str, database: str, schema: str) -> str:
    return (
        sql_text
        .replace("{{ DATABASE }}", database)
        .replace("{{DATABASE}}", database)
        .replace("{{ SCHEMA }}", schema)
        .replace("{{SCHEMA}}", schema)
    )


# ==========================================================
# Discover SQL files
#   - If FILES env var is set (newline-separated paths), use only those
#     (used by the PR "changed files" workflows).
#   - Otherwise walk the whole sql/ tree
#     (used by the full-repo workflow_dispatch check).
# ==========================================================

def get_target_files(base: str = "sql"):

    files_env = os.environ.get("FILES", "").strip()

    if files_env:
        paths = [
            Path(f.strip())
            for f in files_env.splitlines()
            if f.strip().endswith(".sql")
        ]
    else:
        paths = list(Path(base).rglob("*.sql"))

    return paths


# ==========================================================
# Main
# ==========================================================

sql_files = get_target_files()

if not sql_files:
    print("No SQL files to check.")
    sys.exit(0)


print("Connecting to Snowflake...")

conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_USER"],
    password=os.environ["SNOWFLAKE_PASSWORD"],
    warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
    role=os.environ["SNOWFLAKE_ROLE"],
)

cur = conn.cursor()
database = os.environ["SNOWFLAKE_DATABASE"]

checked = 0
passed = 0
failed = 0
new_objects = 0

results = []

print("\nStarting Drift Detection...\n")

for sql_file in sql_files:

    if not sql_file.exists():
        # File was deleted in this PR/diff — nothing to compare against.
        print(f"Skipping {sql_file} (deleted / not present)")
        continue

    try:
        object_type, schema, object_name = parse_object_from_path(sql_file)
    except ValueError as ex:
        print(f"Skipping {sql_file}: {ex}")
        continue

    fq_name = f"{database}.{schema}.{object_name}"

    print(f"Checking {object_type:<10} {fq_name}")

    raw_sql = sql_file.read_text()
    rendered_sql = render_placeholders(raw_sql, database, schema)
    git_sql = normalize_sql(rendered_sql)

    try:
        cur.execute(
            "SELECT GET_DDL(%s, %s)",
            (object_type, fq_name)
        )
        prod_sql = normalize_sql(cur.fetchone()[0])

    except ProgrammingError as ex:
        checked += 1

        if ex.errno == OBJECT_NOT_FOUND_ERRNO:
            # Object doesn't exist yet in this environment.
            # Not drift -- just not deployed here yet.
            new_objects += 1
            results.append({
                "status": "NEW",
                "object": fq_name
            })
        else:
            # A real Snowflake error (permissions, syntax, etc.)
            failed += 1
            results.append({
                "status": "ERROR",
                "object": fq_name,
                "message": str(ex)
            })

        continue

    except Exception as ex:
        # Anything unexpected (network, auth, etc.) -- treat as a real failure.
        checked += 1
        failed += 1
        results.append({
            "status": "ERROR",
            "object": fq_name,
            "message": str(ex)
        })
        continue

    checked += 1

    if git_sql == prod_sql:

        passed += 1

        results.append({
            "status": "PASS",
            "object": fq_name
        })

    else:

        failed += 1

        results.append({
            "status": "FAIL",
            "object": fq_name,
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

print(f"Objects Checked        : {checked}")
print(f"No Drift               : {passed}")
print(f"New (not yet deployed) : {new_objects}")
print(f"Drift Detected         : {failed}")

print()

for result in results:

    if result["status"] == "PASS":
        print(f"✓ {result['object']}")

    elif result["status"] == "FAIL":
        print(f"✗ {result['object']}")

    elif result["status"] == "NEW":
        print(f"+ {result['object']} (new — not deployed here yet)")

    else:
        print(f"! {result['object']} (unable to compare — {result.get('message', 'unknown error')})")


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
        tofile="Live",
        lineterm=""
    )

    for line in diff:
        print(line)


# ==========================================================
# Exit
# ==========================================================

if failed > 0:
    print("\n❌ Drift or errors detected.")
    sys.exit(1)

print("\n✅ No drift detected.")
sys.exit(0)