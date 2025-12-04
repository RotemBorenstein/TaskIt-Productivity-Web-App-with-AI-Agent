
from typing import Iterable
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from main.models import AgentChatMessage


DEFAULT_SESSION_ID = "default"


def get_session_id_from_request(request) -> str:
    session_id = (
        request.POST.get("session_id")
        or request.GET.get("session_id")
        or DEFAULT_SESSION_ID
    )
    return session_id


def _rows_to_chat_history(rows: Iterable[AgentChatMessage]) -> ChatMessageHistory:
    """
    Convert DB rows into a LangChain ChatMessageHistory.
    Only stores human/ai/system messages (no tool traces).
    """
    history = ChatMessageHistory()

    for row in rows:
        if row.role == AgentChatMessage.ROLE_HUMAN:
            history.add_user_message(row.content)
        elif row.role == AgentChatMessage.ROLE_AI:
            history.add_ai_message(row.content)
        elif row.role == AgentChatMessage.ROLE_SYSTEM:
            history.add_message(SystemMessage(content=row.content))
        else:
            # Unknown role: ignore or raise, depending on how strict you want to be
            continue

    return history


def load_history_for_user(user, session_id: str, max_messages: int = 40) -> ChatMessageHistory:
    """
    Load the most recent messages for a given user + session_id and
    return them as a ChatMessageHistory.

    Limiting to max_messages avoids unbounded growth.
    """
    qs = (
        AgentChatMessage.objects
        .filter(user=user, session_id=session_id)
        .order_by("-created_at")[:max_messages]
    )
    # We sliced in reverse order, so flip back to chronological
    rows_in_order = list(qs)[::-1]
    return _rows_to_chat_history(rows_in_order)


def build_memory_for_user(user, session_id: str, window_size: int = 6) -> ConversationBufferWindowMemory:
    chat_history = load_history_for_user(user, session_id)
    memory = ConversationBufferWindowMemory(
        k=window_size,
        chat_memory=chat_history,
        memory_key="chat_history",  # goes into {chat_history} in the prompt
        input_key="input",          # from agent_executor.invoke({...})
        output_key="output",        # AgentExecutor returns {"output": "..."}
        return_messages=True,
    )
    return memory


def persist_turn(user, session_id: str, user_text: str, ai_text: str) -> None:
    """
    Persist a single user -> AI turn to the database.

    The in-memory LangChain memory is only for the current request;
    DB is the source of truth across requests.
    """
    AgentChatMessage.objects.bulk_create(
        [
            AgentChatMessage(
                user=user,
                session_id=session_id,
                role=AgentChatMessage.ROLE_HUMAN,
                content=user_text,
            ),
            AgentChatMessage(
                user=user,
                session_id=session_id,
                role=AgentChatMessage.ROLE_AI,
                content=ai_text,
            ),
        ]
    )
