import os
import re
import sys
from pathlib import Path
from difflib import unified_diff

import snowflake.connector


VALID_TYPES = {
    "tables": "TABLE",
    "views": "VIEW",
    "stored_procedures": "PROCEDURE",
    "functions": "FUNCTION",
    "tasks": "TASK",
}


def normalize_sql(sql: str) -> str:
    sql = re.sub(r'--.*', '', sql)
    sql = re.sub(r'CREATE\s+OR\s+REPLACE', 'CREATE', sql, flags=re.IGNORECASE)
    sql = sql.replace('"', '')
    sql = re.sub(
        r'(CREATE\s+VIEW\s+\S+)\s*\([^)]*\)\s*AS',
        r'\1 AS', sql, flags=re.IGNORECASE | re.DOTALL
    )
    sql = re.sub(r'\s+', ' ', sql)
    return sql.upper().strip()


def pretty(sql):
    keywords = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING",
                "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "JOIN", "ON", "AS"]
    for kw in keywords:
        sql = sql.replace(f" {kw} ", f"\n{kw} ")
    sql = sql.replace(",", ",\n")
    return sql.strip()


def parse_object_from_path(sql_file: Path, base="sql"):
    """
    Expects: sql/<layer>/<schema>/<type_folder>/<object_name>.sql
    Returns (object_type, schema, object_name) or raises ValueError.
    """
    parts = sql_file.parts
    try:
        idx = parts.index(base)
    except ValueError:
        raise ValueError(f"'{sql_file}' is not under a '{base}/' root")

    try:
        layer, schema, type_folder, filename = parts[idx + 1: idx + 5]
    except ValueError:
        raise ValueError(
            f"'{sql_file}' doesn't match sql/<layer>/<schema>/<type>/<file>.sql"
        )

    object_type = VALID_TYPES.get(type_folder.lower())
    if not object_type:
        raise ValueError(f"Unknown object-type folder '{type_folder}' in '{sql_file}'")

    object_name = Path(filename).stem.upper()
    return object_type, schema.upper(), object_name


def render_placeholders(sql_text: str, database: str, schema: str) -> str:
    return (
        sql_text.replace("{{ DATABASE }}", database)
                .replace("{{DATABASE}}", database)
                .replace("{{ SCHEMA }}", schema)
                .replace("{{SCHEMA}}", schema)
    )


def get_target_files(base="sql"):
    """
    If FILES env var is set (newline-separated paths from a PR diff),
    use only those. Otherwise walk the whole sql/ tree.
    """
    files_env = os.environ.get("FILES", "").strip()
    if files_env:
        paths = [Path(f.strip()) for f in files_env.splitlines() if f.strip().endswith(".sql")]
    else:
        paths = list(Path(base).rglob("*.sql"))
    return paths


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

checked = passed = failed = 0
results = []

print("\nStarting Drift Detection...\n")

for sql_file in sql_files:

    if not sql_file.exists():
        # e.g. file was deleted in the PR — nothing to compare
        continue

    try:
        object_type, schema, object_name = parse_object_from_path(sql_file)
    except ValueError as ex:
        print(f"Skipping {sql_file}: {ex}")
        continue

    print(f"Checking {object_type:<10} {database}.{schema}.{object_name}")

    raw_sql = sql_file.read_text()
    rendered_sql = render_placeholders(raw_sql, database, schema)
    git_sql = normalize_sql(rendered_sql)

    fq_name = f"{database}.{schema}.{object_name}"

    try:
        cur.execute(f"SELECT GET_DDL('{object_type}', %s)", (fq_name,))
        prod_sql = normalize_sql(cur.fetchone()[0])
    except Exception as ex:
        checked += 1
        failed += 1
        results.append({"status": "ERROR", "object": fq_name, "message": str(ex)})
        continue

    checked += 1
    if git_sql == prod_sql:
        passed += 1
        results.append({"status": "PASS", "object": fq_name})
    else:
        failed += 1
        results.append({
            "status": "FAIL", "object": fq_name,
            "git": pretty(git_sql), "prod": pretty(prod_sql)
        })

cur.close()
conn.close()

print()
print("=" * 60)
print("DRIFT DETECTION REPORT")
print("=" * 60)
print(f"Objects Checked : {checked}")
print(f"No Drift        : {passed}")
print(f"Drift Detected  : {failed}")
print()

for r in results:
    glyph = {"PASS": "✓", "FAIL": "✗"}.get(r["status"], "!")
    suffix = " (Unable to Compare)" if r["status"] == "ERROR" else ""
    print(f"{glyph} {r['object']}{suffix}")

for r in results:
    if r["status"] != "FAIL":
        continue
    print()
    print("=" * 60)
    print(r["object"])
    print("=" * 60)
    diff = unified_diff(r["git"].splitlines(), r["prod"].splitlines(),
                         fromfile="Git", tofile="Live", lineterm="")
    for line in diff:
        print(line)

if failed > 0:
    print("\n❌ Drift detected.")
    sys.exit(1)

print("\n✅ No drift detected.")
sys.exit(0)