import boto3
import csv
import io
import json
import anthropic
from django.conf import settings

s3_client  = boto3.client(
    's3',
    region_name=settings.AWS_REGION,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
)

llm_client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


# ── S3 Reading ────────────────────────────────────────────────────────────────

def list_s3_files() -> list:
    """List all CSV/JSON files under S3_DATA_PREFIX."""
    response = s3_client.list_objects_v2(
        Bucket=settings.S3_BUCKET_NAME,
        Prefix=settings.S3_DATA_PREFIX,
    )
    return [
        obj['Key']
        for obj in response.get('Contents', [])
        if obj['Key'].endswith('.csv') or obj['Key'].endswith('.json')
    ]


def read_s3_csv(key: str) -> list:
    response = s3_client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
    raw = response['Body'].read()

    for encoding in ['utf-8', 'utf-8-sig', 'windows-1252', 'latin-1']:
        try:
            content = raw.decode(encoding)
            reader  = csv.DictReader(io.StringIO(content))
            return list(reader)
        except (UnicodeDecodeError, Exception):
            continue

    content = raw.decode('utf-8', errors='replace')
    return list(csv.DictReader(io.StringIO(content)))


def read_s3_json(key: str) -> list:
    response = s3_client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=key)
    data     = json.loads(response['Body'].read().decode('utf-8'))
    return data if isinstance(data, list) else [data]


def read_s3_file(key: str) -> list:
    if key.endswith('.csv'):
        return read_s3_csv(key)
    elif key.endswith('.json'):
        return read_s3_json(key)
    return []


def get_file_schema(key: str, rows: list) -> dict:
    """Return column names, sample rows, and unique value samples per column."""
    if not rows:
        return {"filename": key, "columns": [], "sample_rows": [], "row_count": 0}

    columns = list(rows[0].keys())
    unique_samples = {}
    for col in columns:
        seen = []
        for row in rows:
            val = row.get(col, '')
            if val and val not in seen:
                seen.append(val)
            if len(seen) >= 3:
                break
        unique_samples[col] = seen

    return {
        "filename":       key,
        "columns":        columns,
        "sample_rows":    rows[:5],
        "row_count":      len(rows),
        "unique_samples": unique_samples,
    }


# ── Dynamic LLM-driven filtering ─────────────────────────────────────────────

def discover_relevant_files(all_schemas: list, scope: dict) -> dict:
    """Ask Claude Haiku to identify which files are relevant to the current scope."""
    prompt = f"""You are a data analyst. A chart is currently showing data with this scope:

CURRENT SCOPE:
{json.dumps(scope, indent=2)}

AVAILABLE DATA FILES:
{json.dumps(all_schemas, indent=2)}

Identify which files are relevant, which columns map to scope fields,
what filter values to apply, and how to join files if needed.

Respond ONLY in this exact JSON format, no preamble:
{{
  "relevant_files": [
    {{
      "filename": "exact s3 key",
      "department_column": "column name or null",
      "period_column": "column name or null",
      "project_column": "column name or null",
      "filter_values": {{
        "department_column": "value or null",
        "period_column": "value or null",
        "project_column": "value or null"
      }},
      "reason": "why this file is relevant"
    }}
  ],
  "join_key": "column to join files on or null",
  "summary": "what data will be used and why"
}}"""

    response = llm_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text.strip())


def apply_filters(rows: list, file_plan: dict) -> list:
    """Filter rows based on LLM-generated plan."""
    filter_values = {
        col: val
        for col, val in file_plan.get('filter_values', {}).items()
        if col and val
    }
    if not filter_values:
        return rows

    filtered = []
    for row in rows:
        if all(
            val.lower() in str(row.get(col, '')).lower()
            for col, val in filter_values.items()
        ):
            filtered.append(row)
    return filtered


def join_files(file_results: list, join_key: str) -> list:
    """Join multiple filtered datasets on a common key."""
    if not file_results:
        return []
    if len(file_results) == 1:
        return file_results[0]['rows']
    if not join_key:
        combined = []
        for fr in file_results:
            combined.extend(fr['rows'])
        return combined

    base = file_results[0]['rows']
    for fr in file_results[1:]:
        lookup = {row.get(join_key): row for row in fr['rows'] if row.get(join_key)}
        base = [{**lookup.get(row.get(join_key), {}), **row} for row in base]
    return base


# ── Main entry points ─────────────────────────────────────────────────────────

def load_all_data() -> tuple:
    """Load all files from S3 and return (schemas, file_data)."""
    files     = list_s3_files()
    schemas   = []
    file_data = {}
    for key in files:
        rows = read_s3_file(key)
        schemas.append(get_file_schema(key, rows))
        file_data[key] = rows
    return schemas, file_data


def fetch_scoped_data(scope: dict) -> dict:
    """Fetch and filter all relevant S3 data for a given scope."""
    schemas, file_data = load_all_data()
    if not schemas:
        return {"note": "No data files found in S3.", "rows": [], "summary": {}}

    plan          = discover_relevant_files(schemas, scope)
    relevant      = plan.get('relevant_files', [])
    join_key      = plan.get('join_key')

    if not relevant:
        return {"note": "No relevant files found for this scope.", "rows": [], "summary": {}}

    file_results = [
        {"filename": fp['filename'], "rows": apply_filters(file_data.get(fp['filename'], []), fp)}
        for fp in relevant
    ]

    rows    = join_files(file_results, join_key)
    summary = compute_summary(rows)
    summary['discovery_note'] = plan.get('summary', '')

    return {"rows": rows, "summary": summary}


def compute_summary(rows: list) -> dict:
    """Dynamically compute summary statistics from any set of rows."""
    if not rows:
        return {"note": "No matching rows found for this scope."}

    columns = list(rows[0].keys())

    numeric_summaries = {}
    for col in columns:
        values = []
        for row in rows:
            try:
                values.append(float(str(row.get(col, '')).replace(',', '')))
            except (ValueError, TypeError):
                pass
        if values:
            numeric_summaries[col] = {
                "sum":   round(sum(values), 2),
                "avg":   round(sum(values) / len(values), 2),
                "min":   round(min(values), 2),
                "max":   round(max(values), 2),
                "count": len(values),
            }

    categorical_summaries = {}
    for col in columns:
        if col in numeric_summaries:
            continue
        counts = {}
        for row in rows:
            val = str(row.get(col, 'Unknown')).strip()
            if val:
                counts[val] = counts.get(val, 0) + 1
        if 1 < len(counts) <= 30:
            categorical_summaries[col] = counts

    return {
        "total_rows":            len(rows),
        "columns":               columns,
        "numeric_summaries":     numeric_summaries,
        "categorical_summaries": categorical_summaries,
        "sample_rows":           rows[:20],
    }
