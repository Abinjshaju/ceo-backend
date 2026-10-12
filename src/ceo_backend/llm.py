from collections.abc import Iterator
from typing import Protocol

from langchain_core.language_models import BaseChatModel

from ceo_backend.config import Settings
from ceo_backend.errors import Unavailable
from ceo_backend.models import EMBEDDING_DIMENSIONS


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LLMProvider(Protocol):
    model_name: str

    def chat(self, purpose: str) -> BaseChatModel:
        """purpose: agent | extraction | brief | summary | research | ocr"""
        ...

    def embedder(self) -> Embedder | None: ...

    def speak(self, text: str) -> Iterator[bytes]:
        """Spoken audio for text as raw 24 kHz 16-bit mono PCM, streamed as the model produces it.
        Raises Unavailable without speech."""
        ...

    def transcribe(self, audio: bytes, language: str | None, names: list[str]) -> str:
        """What is said in a WAV clip; empty when nobody speaks. Raises Unavailable without transcription."""
        ...


class GeminiEmbedder:
    def __init__(self, settings: Settings) -> None:
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        self.model_name = settings.embedding_model
        self._client = GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model, google_api_key=settings.gemini_api_key
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_documents(
            texts, task_type="RETRIEVAL_DOCUMENT", output_dimensionality=EMBEDDING_DIMENSIONS
        )

    def embed_query(self, text: str) -> list[float]:
        return self._client.embed_query(
            text, task_type="RETRIEVAL_QUERY", output_dimensionality=EMBEDDING_DIMENSIONS
        )


class GeminiProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model_name = settings.gemini_model
        self._models: dict[str, BaseChatModel] = {}
        self._embedder: GeminiEmbedder | None = None
        self._genai_client = None

    def chat(self, purpose: str) -> BaseChatModel:
        if not self.settings.gemini_api_key:
            raise Unavailable("The model is not configured. Set GOOGLE_API_KEY on the server.")
        if purpose not in self._models:
            from langchain_google_genai import ChatGoogleGenerativeAI

            self._models[purpose] = ChatGoogleGenerativeAI(
                model=self.settings.gemini_model,
                google_api_key=self.settings.gemini_api_key,
                max_retries=2,
                timeout=90,
            )
        return self._models[purpose]

    def embedder(self) -> Embedder | None:
        if not (self.settings.embeddings_enabled and self.settings.gemini_api_key):
            return None
        if self._embedder is None:
            self._embedder = GeminiEmbedder(self.settings)
        return self._embedder

    def speak(self, text: str) -> Iterator[bytes]:
        # Streaming: the first audio arrives in about a second, against several seconds for the
        # whole clip. The text is sent as is: style hints such as "read calmly" get read aloud too.
        if not (self.settings.speech_enabled and self.settings.gemini_api_key):
            raise Unavailable("Speech is not configured on the server.")
        from google.genai import types

        voice = types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.settings.speech_voice))
        stream = self._genai().models.generate_content_stream(
            model=self.settings.speech_model,
            contents=text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"], speech_config=types.SpeechConfig(voice_config=voice)
            ),
        )
        for chunk in stream:
            for candidate in chunk.candidates or []:
                for part in (candidate.content.parts if candidate.content else None) or []:
                    if part.inline_data and part.inline_data.data:
                        yield part.inline_data.data

    def transcribe(self, audio: bytes, language: str | None, names: list[str]) -> str:
        # Minimal thinking keeps a 7-second question to about 2 s. The names fix the usual mistakes
        # ("Priya and Nyer" for "Priya Nair"). They sit in the system instruction: in the prompt, models
        # answered silence with the list of names. Flash-Lite was faster but misheard names in real
        # browser audio, and with stronger hints it made names up for bursts of noise.
        if not (self.settings.transcription_enabled and self.settings.gemini_api_key):
            raise Unavailable("Transcription is not configured on the server.")
        from google.genai import types

        system = (
            "You are a speech-to-text transcriber. Transcribe only words that are actually spoken in the audio, "
            "exactly as spoken. Write numbers, dates and amounts as digits ($2.2M, March 31). Output only the "
            "transcript. If no words are spoken (silence, noise, music, breathing, coughing), output exactly NO_SPEECH."
        )
        if names:
            system += " Names the speaker may use, for spelling only; never output them unless you hear them: " + ", ".join(names) + "."
        response = self._genai().models.generate_content(
            model=self.settings.transcription_model,
            contents=[
                types.Part.from_bytes(data=audio, mime_type="audio/wav"),
                "Transcribe this audio." + (f" The speaker most likely uses {language}." if language else ""),
            ],
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=0,
                thinking_config=types.ThinkingConfig(thinking_level="minimal"),
            ),
        )
        return (response.text or "").strip()

    def _genai(self):
        """One google-genai client for speech and transcription."""
        if self._genai_client is None:
            from google import genai
            from google.genai import types

            self._genai_client = genai.Client(
                api_key=self.settings.gemini_api_key, http_options=types.HttpOptions(timeout=30_000)
            )
        return self._genai_client


def text_of(content) -> str:
    """Plain text from a model message's content, which Gemini returns as blocks."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type", "text") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts)
