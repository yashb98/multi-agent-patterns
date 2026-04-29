from types import SimpleNamespace

from jobpulse.voice_handler import transcribe_voice_url


def test_transcribe_voice_url_uses_safe_fetch(monkeypatch):
    monkeypatch.setattr("jobpulse.voice_handler.OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("jobpulse.voice_handler.safe_fetch_bytes", lambda *args, **kwargs: b"fake-audio")

    transcript = SimpleNamespace(text="hello world")
    client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=lambda **kwargs: transcript,
            )
        )
    )
    monkeypatch.setattr(
        "shared.agents.get_openai_client",
        lambda: client,
    )

    result = transcribe_voice_url("https://cdn.example.com/voice.ogg")

    assert result == "hello world"
