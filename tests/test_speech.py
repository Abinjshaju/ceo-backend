import array
import dataclasses
import io
import math
import wave

from ceo_backend.services.speech import BUDGET_CHARS, MORE, TABLE_NOTE, spoken_parts
from tests.conftest import ai


def reply(client, auth, project_id, provider, text_value: str) -> int:
    provider.reply(ai(text_value))
    response = client.post(f"/projects/{project_id}/messages",
                           json={"client_message_id": "s1", "content": "Where does it stand?"}, headers=auth())
    assert response.status_code == 200, response.text
    return response.json()["message_id"]


def test_spoken_text_skips_markup_and_stops_after_about_40_seconds():
    text = ("Revenue grew **12%** [doc:3] per [the report](https://example.com/r) [latest_brief].\n\n"
            "| Year | Revenue |\n|---|---|\n| 2026 | 4 |\n\n```\nprint('x')\n```\n\n## Next steps\n"
            + "\n".join(f"- Step {i} is a fairly long item about what the team does next" for i in range(30)))
    spoken = spoken_parts(text)
    joined = " ".join(spoken.parts)
    assert spoken.parts[0] == "Revenue grew 12% per the report."
    assert joined.count(TABLE_NOTE) == 1
    assert "Next steps. Step 0 is a fairly long item about what the team does next." in joined
    for leftover in ("[", "**", "https", "print", "|", "#"):
        assert leftover not in joined
    assert spoken.truncated and spoken.parts[-1].endswith(MORE)
    assert len(joined) <= BUDGET_CHARS + len(MORE) + 1
    assert spoken_parts("| a |\n|---|\n| 1 |").parts == [TABLE_NOTE]
    assert spoken_parts("").parts == []


def test_reply_is_spoken_in_parts(client, auth, project_id, provider):
    message_id = reply(client, auth, project_id, provider,
                       "It is on track. **Two risks** remain:\n\n- Hiring is late\n- Vendor costs rose")
    plan = client.get(f"/messages/{message_id}/speech", headers=auth())
    assert plan.status_code == 200, plan.text
    assert plan.json() == {"engine": "gemini", "truncated": False, "sample_rate": 24_000,
                           "parts": ["It is on track.", "Two risks remain: Hiring is late. Vendor costs rose."]}

    audio = client.get(f"/messages/{message_id}/speech/audio", headers=auth())
    assert audio.status_code == 200, audio.text
    assert audio.headers["content-type"].startswith("audio/L16; rate=24000")
    # Both chunks arrive as raw PCM; the WAV header on the second is removed.
    assert audio.content == b"\x01\x00" * 1200 + b"\x02\x00" * 1200
    assert provider.spoken == ["It is on track. Two risks remain: Hiring is late. Vendor costs rose."]


def test_only_members_can_hear_assistant_replies(client, auth, project_id, provider):
    message_id = reply(client, auth, project_id, provider, "It is on track.")
    outsider = auth("someone@else.com")
    assert client.get(f"/messages/{message_id}/speech", headers=outsider).status_code == 404
    assert client.get(f"/messages/{message_id}/speech/audio", headers=outsider).status_code == 404
    assert client.get(f"/messages/{message_id}/speech").status_code == 401

    messages = client.get(f"/projects/{project_id}/messages", headers=auth()).json()["messages"]
    own_message = next(m["id"] for m in messages if m["role"] == "user")
    assert client.get(f"/messages/{own_message}/speech", headers=auth()).status_code == 404
    assert client.get("/messages/999999/speech", headers=auth()).status_code == 404
    assert provider.spoken == []


def test_speech_failure_and_browser_voice(client, auth, project_id, provider, services):
    message_id = reply(client, auth, project_id, provider, "It is on track.")
    provider.speech_error = RuntimeError("quota exceeded")
    failed = client.get(f"/messages/{message_id}/speech/audio", headers=auth())
    assert failed.status_code == 503
    assert "quota" not in failed.text

    services.settings = dataclasses.replace(services.settings, speech_enabled=False)
    assert client.get(f"/messages/{message_id}/speech", headers=auth()).json()["engine"] == "browser"


def wav(seconds: float = 1.0, rate: int = 16_000, channels: int = 1, silent: bool = False) -> bytes:
    """A clip with a 220 Hz tone (sound worth transcribing), or silence."""
    count = int(seconds * rate) * channels
    samples = array.array("h", (0 if silent else int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(count)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(samples.tobytes())
    return buffer.getvalue()


def transcribe(client, workspace_id, body: bytes, headers: dict):
    return client.post(f"/workspaces/{workspace_id}/transcriptions?language=en-GB", content=body,
                       headers={"Content-Type": "audio/wav", **headers})


def test_speech_is_transcribed_with_the_workspace_names(client, auth, workspace_id, project_id, provider):
    provider.transcripts.append("Can Priya still deliver the retention drive by December?")
    response = transcribe(client, workspace_id, wav(2), auth())
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "Can Priya still deliver the retention drive by December?"}
    heard = provider.heard[0]
    assert heard["language"] == "en-GB" and heard["bytes"] > 64_000
    assert {"Acme Foods", "Retention drive", "Priya"} <= set(heard["names"])

    # Silence never reaches the model (it tends to invent words for it); "no speech" answers come back empty.
    assert transcribe(client, workspace_id, wav(2, silent=True), auth()).json() == {"text": ""}
    assert len(provider.heard) == 1
    provider.transcripts += ["NO_SPEECH", "<noise>", "[inaudible]", '"Yes, go ahead."']
    assert [transcribe(client, workspace_id, wav(1), auth()).json()["text"] for _ in range(4)] == ["", "", "", "Yes, go ahead."]


def test_transcription_checks_the_recording(client, auth, workspace_id, provider):
    for body in (b"", b"not a wav file", wav(0.1), wav(1, channels=2), wav(1, rate=96_000), wav(61)):
        assert transcribe(client, workspace_id, body, auth()).status_code == 422
    assert transcribe(client, workspace_id, wav(70), auth()).status_code == 413
    assert transcribe(client, workspace_id, wav(1), auth("someone@else.com")).status_code == 404
    assert client.post(f"/workspaces/{workspace_id}/transcriptions", content=wav(1)).status_code == 401
    assert provider.heard == []


def test_transcription_failure_is_a_clean_503(client, auth, workspace_id, provider):
    provider.speech_error = RuntimeError("quota exceeded for this key")
    failed = transcribe(client, workspace_id, wav(1), auth())
    assert failed.status_code == 503
    assert "quota" not in failed.text
