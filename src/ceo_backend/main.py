import os
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel

from ceo_backend.agent import build_llm, chat
from ceo_backend.onboarding import intake_summary, run_turn
from ceo_backend.research import make_gemini_researcher
from ceo_backend.store import IntakeStore

load_dotenv()

app = FastAPI(title="ceo-backend")


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    reply: str


class OnboardingReply(BaseModel):
    session_id: str
    reply: str
    intake: dict


@lru_cache
def get_store() -> IntakeStore:
    return IntakeStore(os.getenv("DATABASE_PATH", "ceo_backend.db"))


@lru_cache
def get_onboarding_llm():
    return build_llm()


@lru_cache
def get_researcher():
    return make_gemini_researcher(get_onboarding_llm())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def post_chat(request: ChatRequest) -> ChatResponse:
    reply = chat(request.session_id, request.message)
    return ChatResponse(session_id=request.session_id, reply=reply)


@app.post("/onboarding/chat", response_model=OnboardingReply)
def onboarding_chat(request: ChatRequest) -> OnboardingReply:
    store = get_store()
    reply = run_turn(
        request.session_id,
        request.message,
        get_onboarding_llm(),
        store,
        get_researcher(),
    )
    return OnboardingReply(
        session_id=request.session_id,
        reply=reply,
        intake=intake_summary(store.get_intake(request.session_id)),
    )


@app.get("/onboarding/{session_id}")
def onboarding_state(session_id: str) -> dict:
    return intake_summary(get_store().get_intake(session_id))


def run() -> None:
    import uvicorn

    uvicorn.run("ceo_backend.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
