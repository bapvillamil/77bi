import json
import anthropic
from django.conf import settings
from .s3_service import fetch_scoped_data

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def generate_insight(
    user_query: str,
    chart_data: dict,
    chat_history: list,
) -> str:
    """Generate grounded insight using Claude Sonnet."""
    scope        = chart_data.get('scope', {})
    raw_rows     = chart_data.get('raw_rows', [])
    summary      = chart_data.get('summary', {})
    result_label = chart_data.get('result_label', '')

    enrichment = {}
    if scope:
        try:
            enrichment = fetch_scoped_data(scope)
        except Exception as e:
            enrichment = {"note": f"Could not fetch enrichment data: {str(e)}"}

    system_prompt = """You are a senior data analyst specializing in Jira project performance and business metrics.

You generate deep insights, root cause analysis, projections, and actionable recommendations.

STRICT GROUNDING RULES:
1. Use ONLY the data provided in this prompt.
2. Label assumptions clearly as assumptions.
3. Label projections as projections based on visible patterns.
4. Do not hallucinate metrics or trends not in the data.
5. Reference the scope (department, period, project) in your statements.
6. If data is insufficient, say so clearly.

Structure your response clearly with these sections where relevant:
- SUMMARY: what the data shows
- ROOT CAUSE: why this pattern exists (hypothesis-based)
- PATTERNS: notable trends or anomalies
- PROJECTIONS: what might happen next based on current trends
- RECOMMENDATIONS: concrete actionable suggestions"""

    context = f"""=== WHAT IS CURRENTLY SHOWN IN THE CHART ===
Chart title: {result_label}
Scope: {json.dumps(scope, indent=2)}

Chart data points:
{json.dumps(raw_rows[:30], indent=2)}

Summary statistics:
{json.dumps(summary, indent=2)}

=== ENRICHED DATA (same scope, additional Jira context) ===
{json.dumps(enrichment.get('summary', {}), indent=2)}

Sample enriched rows:
{json.dumps(enrichment.get('rows', [])[:20], indent=2)}

=== USER QUESTION ===
{user_query}

Answer based ONLY on the data above. Do not reference data outside this scope."""

    messages = []
    for turn in chat_history[-6:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": context})

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=messages,
    )

    return response.content[0].text


def generate_insight_from_session(user_query: str, session_context: dict) -> str:
    """Intent 2: generate insight from existing session context only."""
    chart_data   = session_context.get('chart_data', {})
    chat_history = session_context.get('chat_history', [])

    if not chart_data:
        return (
            "No chart context found in this session. "
            "Please first ask for a visualization so I have data to analyze."
        )

    return generate_insight(
        user_query=user_query,
        chart_data=chart_data,
        chat_history=chat_history,
    )
