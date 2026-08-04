import re
import sys
from pathlib import Path
from difflib import unified_diff


def normalize_sql(sql: str) -> str:
    """
    Normalize SQL before comparison.
    """

    # Remove single-line comments
    sql = re.sub(r'--.*', '', sql)

    # Remove blank lines
    lines = [line.strip() for line in sql.splitlines() if line.strip()]

    # Join into one string
    sql = " ".join(lines)

    # Collapse multiple spaces
    sql = re.sub(r'\s+', ' ', sql)

    # Ignore case
    sql = sql.upper()

    return sql.strip()


git_file = Path("sql/create_view.sql")
prod_file = Path("temp/prod_view.sql")

git_sql = normalize_sql(git_file.read_text())
prod_sql = normalize_sql(prod_file.read_text())

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