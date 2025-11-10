from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from langchain_openai import ChatOpenAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from main.agent.agent_tools import make_user_tools
from django.utils import timezone
from langchain.prompts import ChatPromptTemplate

prompt_template = """
You are TaskIt Assistant, a productivity and task-management helper.

Today's date is {today_date}, and the current time is {current_time} (Asia/Jerusalem).

Purpose:
- Help the user manage daily and long-term tasks.
- Interpret relative dates like "today", "tomorrow", "next week" using the current date/time.
- Use the provided tools to add, update, complete, or query tasks.
- Never invent data; always rely on tool outputs.

Rules:
- Always call tools when actions are needed.
- Follow tool schemas exactly.
- After a successful tool call, stop and explain the result in natural language.
- Do not re-call the same tool with identical input.
- If multiple actions are requested, call tools for each, then summarize the results together.
- If no tool is relevant, answer briefly and naturally.

Style:
- Clear, concise, action-focused.
- Confirm actions in a few words (e.g., “Added daily meditation task.”).
- Summarize tool outputs into human-friendly responses.

IMPORTANT for add_event tool:
- Always pass datetime strings in format 'YYYY-MM-DDTHH:MM' (e.g., '2025-09-29T14:00')
- Times should be in Asia/Jerusalem timezone
- Do NOT include timezone suffixes like 'Z' or '+00:00'
- Example: For 2pm today, use '2025-09-29T14:00', not '2025-09-29T14:00Z'

Previous reasoning and tool results:
{agent_scratchpad}

User input: {input}
"""
prompt = ChatPromptTemplate.from_template(prompt_template)
llm = ChatOpenAI(model="gpt-4o-mini")

@login_required
@require_POST
def agent_endpoint(request):
    today_date = timezone.localdate().isoformat()  # e.g. "2025-09-25"
    current_time = timezone.localtime().strftime("%H:%M")  # e.g. "11:32"
    user_message = request.POST.get("message")
    # 1. Make user-specific tools
    tools = make_user_tools(request.user)
    # 4. Build the agent
    agent = create_tool_calling_agent(llm, tools, prompt)
    # 5. Wrap in executor
    agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=3 ,early_stopping_method="force")
    # 6. Run the agent
    result = agent_executor.invoke({
        "input": user_message,
        "today_date": today_date,
        "current_time": current_time
    })

    if result.get("output", "").startswith("Agent stopped due to max iterations"):
        result["output"] = (
            "Done"
        )
    return JsonResponse({"reply": result["output"]})

