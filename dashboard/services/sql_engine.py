import json
import anthropic
from django.conf import settings
from .s3_service import load_all_data, apply_filters, join_files, compute_summary

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def query_to_plan(user_query: str, all_schemas: list) -> dict:
    """
    Ask Claude Sonnet to convert a natural language query into
    a structured execution plan against the available CSV files.
    """
    prompt = f"""You are a data query planner. The user wants to query CSV data stored in S3.

USER QUERY:
"{user_query}"

AVAILABLE DATA FILES AND THEIR SCHEMAS:
{json.dumps(all_schemas, indent=2)}

Convert the user query into a structured execution plan.

Respond ONLY in this exact JSON format, no preamble:
{{
  "relevant_files": [
    {{
      "filename": "exact s3 key",
      "filter_values": {{
        "column_name": "value to filter by"
      }}
    }}
  ],
  "join_key": "column to join files on or null",
  "group_by": "column name to group results by or null",
  "aggregate_column": "column name to aggregate or null",
  "aggregate_func": "count" | "sum" | "avg" | "min" | "max" | null,
  "sort_by": "column name to sort results by or null",
  "sort_order": "desc" | "asc",
  "limit": 50,
  "scope": {{
    "department": "extracted department value or null",
    "period": "extracted time period or null",
    "project": "extracted project or null"
  }},
  "result_label": "human readable description of what this query returns",
  "x_axis_label": "label for x axis",
  "y_axis_label": "label for y axis"
}}

Rules:
- filter_values: only include filters explicitly mentioned in the query
- group_by: the column whose unique values become the chart x-axis
- aggregate_column: the column to measure (if count, use any column)
- aggregate_func: almost always "count" for ticket data, "sum" for revenue/numeric data
- sort_order: "desc" by default to show highest first
- limit: default 50, use smaller for "top N" queries
- scope: extract any department/period/project mentioned for use by insight engine"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text.strip())


def execute_plan(plan: dict, file_data: dict) -> list:
    """Execute the LLM-generated plan against the in-memory CSV rows."""
    relevant = plan.get('relevant_files', [])
    join_key = plan.get('join_key')

    file_results = []
    for fp in relevant:
        filename = fp.get('filename')
        rows     = file_data.get(filename, [])

        filter_values = {
            col: val
            for col, val in fp.get('filter_values', {}).items()
            if col and val
        }
        if filter_values:
            rows = [
                row for row in rows
                if all(
                    val.lower() in str(row.get(col, '')).lower()
                    for col, val in filter_values.items()
                )
            ]
        file_results.append({"filename": filename, "rows": rows})

    joined = join_files(file_results, join_key)

    if not joined:
        return []

    group_by   = plan.get('group_by')
    agg_col    = plan.get('aggregate_column')
    agg_func   = plan.get('aggregate_func', 'count')
    sort_by    = plan.get('sort_by')
    sort_order = plan.get('sort_order', 'desc')
    limit      = plan.get('limit', 50)

    if group_by:
        groups = {}
        for row in joined:
            key = str(row.get(group_by, 'Unknown')).strip()
            if key not in groups:
                groups[key] = []
            groups[key].append(row)

        results = []
        for group_val, group_rows in groups.items():
            if agg_func == 'count':
                agg_value = len(group_rows)
            elif agg_func == 'sum' and agg_col:
                try:
                    agg_value = sum(
                        float(str(r.get(agg_col, 0)).replace(',', ''))
                        for r in group_rows
                    )
                    agg_value = round(agg_value, 2)
                except (ValueError, TypeError):
                    agg_value = 0
            elif agg_func == 'avg' and agg_col:
                try:
                    vals = [
                        float(str(r.get(agg_col, 0)).replace(',', ''))
                        for r in group_rows
                    ]
                    agg_value = round(sum(vals) / len(vals), 2) if vals else 0
                except (ValueError, TypeError):
                    agg_value = 0
            elif agg_func in ('min', 'max') and agg_col:
                try:
                    vals = [
                        float(str(r.get(agg_col, 0)).replace(',', ''))
                        for r in group_rows
                    ]
                    agg_value = round(min(vals) if agg_func == 'min' else max(vals), 2)
                except (ValueError, TypeError):
                    agg_value = 0
            else:
                agg_value = len(group_rows)

            results.append({
                "label":     group_val,
                "value":     agg_value,
                "row_count": len(group_rows),
            })

        sort_col = sort_by or 'value'
        results.sort(
            key=lambda x: x.get(sort_col, x.get('value', 0)),
            reverse=(sort_order == 'desc')
        )

        return results[:limit]

    return joined[:limit]


def determine_chart_type(plan: dict, results: list) -> str:
    group_by   = plan.get('group_by', '')
    agg_func   = plan.get('aggregate_func', 'count')
    num_groups = len(results)

    time_keywords  = ['month', 'quarter', 'sprint', 'week', 'year', 'date', 'period']
    is_time_series = any(kw in group_by.lower() for kw in time_keywords)

    if is_time_series:
        return 'line'
    elif num_groups > 10:
        return 'horizontalBar'
    elif num_groups <= 3 and agg_func in ('sum', 'count'):
        return 'doughnut'
    else:
        return 'bar'


def run_query(user_query: str) -> dict:
    """Main entry point for the SQL engine."""
    schemas, file_data = load_all_data()

    if not schemas:
        return {"error": "No data files found in S3."}

    plan    = query_to_plan(user_query, schemas)
    results = execute_plan(plan, file_data)

    if not results:
        return {"error": "No data found matching your query. Try a different filter or time period."}

    chart_type = determine_chart_type(plan, results)

    labels = [str(r.get('label', '')) for r in results]
    values = [r.get('value', 0) for r in results]

    raw_rows = []
    for fp in plan.get('relevant_files', []):
        raw_rows.extend(file_data.get(fp.get('filename', ''), []))
    summary = compute_summary(raw_rows[:100])

    return {
        "chart_type":   chart_type,
        "labels":       labels,
        "values":       values,
        "x_axis_label": plan.get('x_axis_label', plan.get('group_by', '')),
        "y_axis_label": plan.get('y_axis_label', plan.get('aggregate_func', 'count')),
        "result_label": plan.get('result_label', user_query),
        "scope":        plan.get('scope', {}),
        "raw_rows":     results,
        "summary":      summary,
    }


def execute_builder_query(
    x_axis: str,
    y_axis: str,
    group_by: str,
    chart_type: str,
    filters: dict,
    aggregation: str = 'count',
    show_values: bool = True,
    kpi_format: str = 'number',
) -> dict:
    """Execute a chart build from the drag-and-drop builder."""
    schemas, file_data = load_all_data()

    if not schemas:
        return {"error": "No data files found in S3."}

    # KPI mode: compute a single aggregate value across ALL rows of a field
    if chart_type == 'kpi':
        # Find which file has the value field (y_axis) or x_axis
        value_col = y_axis or x_axis
        kpi_file  = None
        for schema in schemas:
            if value_col in schema.get('columns', []):
                kpi_file = schema['filename']
                break
        if not kpi_file:
            return {"error": f"Column '{value_col}' not found in any data file."}

        rows = file_data.get(kpi_file, [])
        if filters:
            rows = [r for r in rows if all(
                str(v).lower() in str(r.get(c, '')).lower()
                for c, v in filters.items() if c and v
            )]

        agg = aggregation or 'count'
        if agg == 'count':
            kpi_val = len(rows)
        elif agg == 'count_distinct':
            kpi_val = len(set(str(r.get(value_col, '')) for r in rows if r.get(value_col)))
        elif agg in ('sum', 'avg', 'min', 'max'):
            nums = []
            for r in rows:
                try: nums.append(float(str(r.get(value_col, 0)).replace(',', '')))
                except: pass
            if nums:
                if agg == 'sum': kpi_val = round(sum(nums), 2)
                elif agg == 'avg': kpi_val = round(sum(nums)/len(nums), 2)
                elif agg == 'min': kpi_val = round(min(nums), 2)
                elif agg == 'max': kpi_val = round(max(nums), 2)
            else:
                kpi_val = 0
        else:
            kpi_val = len(rows)

        label = y_axis or x_axis or 'Value'
        return {
            "chart_type":   'kpi',
            "labels":       [label],
            "values":       [kpi_val],
            "datasets":     None,
            "x_axis_label": '',
            "y_axis_label": label,
            "result_label": f"{label} ({agg})",
            "scope":        {},
            "raw_rows":     [],
            "summary":      {},
            "show_values":  True,
            "kpi_format":   kpi_format,
        }

    primary_file = None
    for schema in schemas:
        if x_axis in schema.get('columns', []):
            primary_file = schema['filename']
            break

    if not primary_file:
        return {"error": f"Column '{x_axis}' not found in any data file."}

    rows = file_data.get(primary_file, [])

    if filters:
        rows = [
            row for row in rows
            if all(
                str(val).lower() in str(row.get(col, '')).lower()
                for col, val in filters.items()
                if col and val
            )
        ]

    if not rows:
        return {"error": "No data found after applying filters."}

    # Use explicit aggregation from UI
    if aggregation and aggregation != 'count':
        agg_func = aggregation
    elif y_axis:
        numeric_count = 0
        for row in rows[:20]:
            try:
                float(str(row.get(y_axis, '')).replace(',', ''))
                numeric_count += 1
            except (ValueError, TypeError):
                pass
        agg_func = 'sum' if numeric_count > 10 else 'count'
    else:
        agg_func = 'count'

    if group_by and group_by != x_axis:
        from collections import defaultdict
        series_data = defaultdict(lambda: defaultdict(float))
        all_groups  = set()

        for row in rows:
            x_val = str(row.get(x_axis, 'Unknown')).strip()
            g_val = str(row.get(group_by, 'Unknown')).strip()
            all_groups.add(g_val)

            if agg_func in ('count', 'count_distinct'):
                series_data[x_val][g_val] += 1
            elif agg_func == 'sum' and y_axis:
                try:
                    series_data[x_val][g_val] += float(str(row.get(y_axis, 0)).replace(',', ''))
                except (ValueError, TypeError):
                    pass

        x_labels   = sorted(series_data.keys())
        all_groups = sorted(all_groups)

        SERIES_COLORS = [
            'rgba(59,130,246,0.8)',  'rgba(16,185,129,0.8)',
            'rgba(245,158,11,0.8)',  'rgba(239,68,68,0.8)',
            'rgba(139,92,246,0.8)',  'rgba(236,72,153,0.8)',
        ]

        datasets = []
        for i, grp in enumerate(all_groups):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            datasets.append({
                'label':           grp,
                'data':            [round(series_data[x][grp], 2) for x in x_labels],
                'backgroundColor': color,
                'borderColor':     color.replace('0.8)', '1)'),
                'borderWidth':     1,
            })

        result_label = f"{x_axis} by {group_by}"
        if y_axis:
            result_label += f" ({agg_func} of {y_axis})"

        return {
            "chart_type":   'bar' if chart_type not in ['line', 'doughnut'] else chart_type,
            "labels":       x_labels,
            "values":       [],
            "datasets":     datasets,
            "x_axis_label": x_axis,
            "y_axis_label": y_axis or 'Count',
            "result_label": result_label,
            "scope":        {},
            "raw_rows":     [],
            "summary":      {},
            "show_values":  show_values,
            "kpi_format":   kpi_format,
        }

    else:
        groups = {}
        for row in rows:
            key = str(row.get(x_axis, 'Unknown')).strip()
            if key not in groups:
                groups[key] = []
            groups[key].append(row)

        results = []
        for key, group_rows in groups.items():
            if agg_func == 'count':
                val = len(group_rows)
            elif agg_func == 'count_distinct' and y_axis:
                val = len(set(str(r.get(y_axis, '')) for r in group_rows if r.get(y_axis)))
            elif agg_func == 'sum' and y_axis:
                try:
                    val = round(sum(
                        float(str(r.get(y_axis, 0)).replace(',', ''))
                        for r in group_rows
                    ), 2)
                except (ValueError, TypeError):
                    val = 0
            elif agg_func == 'avg' and y_axis:
                try:
                    vals = [float(str(r.get(y_axis, 0)).replace(',', '')) for r in group_rows]
                    val = round(sum(vals) / len(vals), 2) if vals else 0
                except (ValueError, TypeError):
                    val = 0
            elif agg_func == 'min' and y_axis:
                try:
                    vals = [float(str(r.get(y_axis, 0)).replace(',', '')) for r in group_rows]
                    val = round(min(vals), 2) if vals else 0
                except (ValueError, TypeError):
                    val = 0
            elif agg_func == 'max' and y_axis:
                try:
                    vals = [float(str(r.get(y_axis, 0)).replace(',', '')) for r in group_rows]
                    val = round(max(vals), 2) if vals else 0
                except (ValueError, TypeError):
                    val = 0
            else:
                val = len(group_rows)
            results.append({'label': key, 'value': val})

        results.sort(key=lambda x: x['value'], reverse=True)
        results = results[:50]

        labels = [r['label'] for r in results]
        values = [r['value'] for r in results]

        result_label = f"{x_axis}"
        if y_axis:
            result_label += f" vs {y_axis} ({agg_func})"
        else:
            result_label += " (count)"

        if chart_type == 'bar' and len(labels) > 10:
            chart_type = 'horizontalBar'

        return {
            "chart_type":   chart_type,
            "labels":       labels,
            "values":       values,
            "datasets":     None,
            "x_axis_label": x_axis,
            "y_axis_label": y_axis or 'Count',
            "result_label": result_label,
            "scope":        {},
            "raw_rows":     results,
            "summary":      compute_summary(rows[:100]),
            "show_values":  show_values,
            "kpi_format":   kpi_format,
        }
