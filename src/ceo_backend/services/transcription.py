"""Voice conversation input: checking a spoken clip and the names that help transcribe it."""
import array
import io
import re
import sys
import uuid
import wave

from sqlalchemy import select
from sqlalchemy.orm import Session

from ceo_backend.errors import DomainError, Invalid
from ceo_backend.models import CompanyProfile, Project, ProjectFact

MAX_AUDIO_BYTES = 2_000_000  # a minute of 16 kHz mono is about 1.9 MB
MIN_SECONDS, MAX_SECONDS = 0.3, 60
MAX_NAMES = 30
# A clip needs about 0.3 s louder than this (of 32767) to be worth transcribing. Near-silent clips
# are where the model makes things up, such as answering with the hint names.
SOUND_LEVEL = 300
SOUND_SECONDS = 0.3
NO_SPEECH = re.compile(r"^\W*no_speech\W*$|^\s*[<\[(][^<>\[\]()]*[>\])]\s*$", re.IGNORECASE)


class TooLarge(DomainError):
    status_code = 413


def has_sound(data: bytes) -> bool:
    with wave.open(io.BytesIO(data)) as clip:
        rate = clip.getframerate()
        samples = array.array("h", clip.readframes(clip.getnframes()))
    if sys.byteorder == "big":
        samples.byteswap()
    frame = max(1, rate // 50)  # 20 ms
    loud = 0
    for start in range(0, len(samples), frame):
        chunk = samples[start:start + frame]
        if (sum(s * s for s in chunk) / len(chunk)) ** 0.5 >= SOUND_LEVEL:
            loud += 1
    return loud * frame / rate >= SOUND_SECONDS


def clean_transcript(text: str) -> str:
    """The transcript, or "" when the model reports no speech ("NO_SPEECH", "<noise>", "[inaudible]")."""
    text = (text or "").strip().strip('"“”').strip()
    return "" if NO_SPEECH.match(text) else text


def check_wav(data: bytes) -> float:
    """The clip's length in seconds, if it is a WAV the transcriber should take."""
    try:
        with wave.open(io.BytesIO(data)) as clip:
            channels, width, rate, frames = clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getnframes()
    except (wave.Error, EOFError):
        raise Invalid("Send the recording as a WAV file.") from None
    if channels != 1 or width != 2 or not 8_000 <= rate <= 48_000:
        raise Invalid("Send mono 16-bit audio at 8–48 kHz.")
    seconds = frames / rate
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise Invalid(f"Recordings must be {MIN_SECONDS}–{MAX_SECONDS} seconds long.")
    return seconds


def glossary(session: Session, workspace_id: uuid.UUID) -> list[str]:
    """Names a transcriber tends to get wrong: the company, its projects and their owners."""
    names: list[str] = []
    profile = session.scalar(select(CompanyProfile).where(CompanyProfile.workspace_id == workspace_id))
    company = ((profile.fields if profile else None) or {}).get("name") or {}
    if isinstance(company.get("value"), str):
        names.append(company["value"])
    projects = session.scalars(
        select(Project).where(Project.workspace_id == workspace_id, Project.status != "archived")
        .order_by(Project.created_at.desc()).limit(MAX_NAMES)
    ).all()
    names += [project.name for project in projects]
    owners = session.scalars(
        select(ProjectFact.value).where(ProjectFact.project_id.in_([p.id for p in projects]), ProjectFact.field == "owner")
    ).all() if projects else []
    names += owners
    unique = list(dict.fromkeys(name.strip() for name in names if name and name.strip() and len(name) <= 80))
    return unique[:MAX_NAMES]
