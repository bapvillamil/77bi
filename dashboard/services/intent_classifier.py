import json
import anthropic
from django.conf import settings

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def classify_intent(user_query: str, session_context: dict) -> dict:
    """
    Classify user query into one of three intents:
    - intent_1: visualization only
    - intent_2: insight only
    - intent_3: both
    """
    has_context = bool(session_context.get('chart_data'))

    system_prompt = """You are an intent classifier for a Jira analytics dashboard.

A user types a natural language query. Classify it into exactly one of these intents:

INTENT_1 — Visualization only
  The user wants to see a chart or data visualization.
  Examples:
  - "show me total tickets resolved by IT members this month"
  - "give me a bar chart of tickets per department"
  - "display Q3 performance"
  - "how many tickets were resolved last sprint"

INTENT_2 — Insight only (follow-up)
  The user is asking a follow-up question about something already shown.
  Only valid if session context exists.
  Examples:
  - "why did this happen?"
  - "what caused the drop?"
  - "what should we do to improve?"
  - "how does this relate to company performance?"
  - "give me suggestions"

INTENT_3 — Both visualization + insight
  The user wants to see data AND get an explanation or analysis.
  Examples:
  - "show me IT performance and explain why it dropped"
  - "display Q3 tickets and analyze the bottlenecks"
  - "give me revenue chart and tell me why it changed"

Rules:
- Queries about showing/displaying/giving data → INTENT_1
- Follow-up questions about what is already shown + session exists → INTENT_2
- Queries asking for both data and analysis → INTENT_3
- If no session context and user asks insight-only → INTENT_3 (need data first)

Respond in JSON only, no preamble:
{
  "intent": "intent_1" | "intent_2" | "intent_3",
  "reasoning": "brief reason"
}"""

    user_message = f"""User query: "{user_query}"
Has existing session context (chart already shown): {has_context}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}]
    )

    text = response.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]

    result = json.loads(text.strip())
    result['has_session_context'] = has_context
    return result
