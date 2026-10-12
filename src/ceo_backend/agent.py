import os
from functools import lru_cache

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_google_genai import ChatGoogleGenerativeAI

SYSTEM_PROMPT = "You are a helpful assistant for the CEO backend. Keep answers concise."

_histories: dict[str, InMemoryChatMessageHistory] = {}


def build_llm() -> BaseChatModel:
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
        google_api_key=os.getenv("GOOGLE_API_KEY"),
        temperature=0.7,
    )


def build_chain(llm: BaseChatModel) -> Runnable[dict, BaseMessage]:
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            MessagesPlaceholder("history"),
            ("human", "{input}"),
        ]
    )
    return prompt | llm


def _get_history(session_id: str) -> InMemoryChatMessageHistory:
    if session_id not in _histories:
        _histories[session_id] = InMemoryChatMessageHistory()
    return _histories[session_id]


def build_agent(llm: BaseChatModel) -> RunnableWithMessageHistory:
    return RunnableWithMessageHistory(
        build_chain(llm),
        _get_history,
        input_messages_key="input",
        history_messages_key="history",
    )


@lru_cache
def get_agent() -> RunnableWithMessageHistory:
    return build_agent(build_llm())


def chat(session_id: str, message: str) -> str:
    response = get_agent().invoke(
        {"input": message},
        config={"configurable": {"session_id": session_id}},
    )
    return str(response.content)
