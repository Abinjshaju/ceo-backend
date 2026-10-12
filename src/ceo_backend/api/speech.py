import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ceo_backend.api.deps import current_user, get_services, get_session
from ceo_backend.auth import require_membership
from ceo_backend.errors import Invalid, NotFound, Unavailable
from ceo_backend.models import Conversation, Message, User
from ceo_backend.services.container import Services
from ceo_backend.services.speech import SAMPLE_RATE, raw_pcm, spoken_parts
from ceo_backend.services.transcription import (
    MAX_AUDIO_BYTES,
    TooLarge,
    check_wav,
    clean_transcript,
    glossary,
    has_sound,
)

log = logging.getLogger(__name__)
router = APIRouter()


# Only stored assistant replies the user can read are spoken, so this never voices arbitrary text.
def speakable(session: Session, user: User, message_id: int) -> Message:
    message = session.get(Message, message_id)
    conversation = session.get(Conversation, message.conversation_id) if message else None
    if message is None or message.role != "assistant" or conversation is None:
        raise NotFound("Message not found.")
    try:
        require_membership(session, user, conversation.workspace_id)
    except NotFound:
        raise NotFound("Message not found.") from None
    return message


@router.get("/messages/{message_id}/speech")
def speech_plan(message_id: int, user: User = Depends(current_user), session: Session = Depends(get_session),
                services: Services = Depends(get_services)) -> dict:
    spoken = spoken_parts(speakable(session, user, message_id).content)
    settings = services.settings
    return {
        "engine": "gemini" if settings.speech_enabled and settings.gemini_api_key else "browser",
        "parts": spoken.parts,
        "truncated": spoken.truncated,
        "sample_rate": SAMPLE_RATE,
    }


@router.get("/messages/{message_id}/speech/audio")
def speech_audio(message_id: int, user: User = Depends(current_user), session: Session = Depends(get_session),
                 services: Services = Depends(get_services)) -> StreamingResponse:
    """The spoken parts as one stream of raw PCM, sent while Gemini produces it."""
    parts = spoken_parts(speakable(session, user, message_id).content).parts
    if not parts:
        raise NotFound("Nothing to read aloud.")
    chunks = services.llm.speak(" ".join(parts))
    # Wait for the first audio before answering, so a failure is still a clean 503.
    try:
        first = next(chunks)
    except StopIteration:
        raise Unavailable("The speech model returned no audio.") from None
    except Unavailable:
        raise
    except Exception:
        log.exception("speech failed for message %s", message_id)
        raise Unavailable("Speech is unavailable right now.") from None

    def stream():
        yield raw_pcm(first)
        try:
            for chunk in chunks:
                yield raw_pcm(chunk)
        except Exception:
            log.exception("speech stream for message %s ended early", message_id)

    return StreamingResponse(stream(), media_type=f"audio/L16; rate={SAMPLE_RATE}; channels=1",
                             headers={"Cache-Control": "private, no-store"})


@router.post("/workspaces/{workspace_id}/transcriptions")
async def transcribe(workspace_id: uuid.UUID, request: Request,
                     language: str | None = Query(default=None, max_length=20),
                     user: User = Depends(current_user), session: Session = Depends(get_session),
                     services: Services = Depends(get_services)) -> dict:
    """What the user said in a WAV clip, for voice conversation. The audio is neither stored nor logged."""
    def names_for_member() -> list[str]:
        require_membership(session, user, workspace_id)
        return glossary(session, workspace_id)

    names = await run_in_threadpool(names_for_member)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise TooLarge("Recordings are limited to about a minute.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_AUDIO_BYTES:
            raise TooLarge("Recordings are limited to about a minute.")
    if not data:
        raise Invalid("No audio was sent.")
    audio = bytes(data)
    check_wav(audio)
    if not await run_in_threadpool(has_sound, audio):
        return {"text": ""}
    try:
        text = await run_in_threadpool(services.llm.transcribe, audio, language, names)
    except Unavailable:
        raise
    except Exception:
        log.exception("transcription failed in workspace %s", workspace_id)
        raise Unavailable("Transcription is unavailable right now.") from None
    return {"text": clean_transcript(text)}
