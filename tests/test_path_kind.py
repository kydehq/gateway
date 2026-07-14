"""Unit tests for the _path_kind classifier in src/kyde/server.py.

Pure function over (upstream, api_path). Covers each branch and the
quirks of how api_path arrives from different upstreams:
  - OpenAI / Anthropic: v1/ stripped before this layer sees it
  - Gemini / Copilot / Ollama: v1/ or v1beta/ preserved + Gemini uses
    ':method' suffixes on /models/{name} URLs.
"""

from __future__ import annotations

import pytest

from kyde import server


@pytest.mark.parametrize(
    "upstream,api_path,expected",
    [
        # OpenAI — v1/ stripped at _resolve_upstream
        ("openai", "chat/completions", server.PATH_KIND_CHAT),
        ("openai", "embeddings", server.PATH_KIND_EMBEDDING),
        ("openai", "moderations", server.PATH_KIND_MODERATION),
        ("openai", "models", server.PATH_KIND_MODELS_LIST),
        ("openai", "models/gpt-4o", server.PATH_KIND_MODELS_LIST),
        ("openai", "audio/transcriptions", server.PATH_KIND_AUDIO_TRANSCRIPTION),
        ("openai", "audio/translations", server.PATH_KIND_AUDIO_TRANSLATION),
        ("openai", "audio/speech", server.PATH_KIND_AUDIO_SPEECH),
        ("openai", "images/generations", server.PATH_KIND_IMAGE_GENERATION),
        ("openai", "images/edits", server.PATH_KIND_IMAGE_EDIT),
        ("openai", "images/variations", server.PATH_KIND_IMAGE_VARIATION),
        ("openai", "completions", server.PATH_KIND_LEGACY_COMPLETION),
        ("openai", "files", server.PATH_KIND_FILE_OP),
        ("openai", "files/file-abc/content", server.PATH_KIND_FILE_OP),
        ("openai", "fine_tuning/jobs", server.PATH_KIND_FINE_TUNING),
        # Some older clients use hyphens
        ("openai", "fine-tuning/jobs", server.PATH_KIND_FINE_TUNING),
        # Anthropic — same stripping
        ("anthropic", "messages", server.PATH_KIND_CHAT),
        # Ollama — v1/ retained for the OpenAI-compat path, native paths
        # never carry it.
        ("ollama", "v1/chat/completions", server.PATH_KIND_CHAT),
        ("ollama", "api/chat", server.PATH_KIND_CHAT),
        ("ollama", "api/generate", server.PATH_KIND_CHAT),
        # Gemini — v1beta/ stripped + ':method' suffix is what defines the kind.
        ("gemini", "v1beta/models/gemini-pro:generateContent", server.PATH_KIND_CHAT),
        (
            "gemini",
            "v1beta/models/gemini-pro:streamGenerateContent",
            server.PATH_KIND_CHAT,
        ),
        (
            "gemini",
            "v1beta/models/text-embedding-004:embedContent",
            server.PATH_KIND_EMBEDDING,
        ),
        (
            "gemini",
            "v1beta/models/text-embedding-004:batchEmbedContents",
            server.PATH_KIND_EMBEDDING,
        ),
        (
            "gemini",
            "v1beta/models/gemini-pro:countTokens",
            server.PATH_KIND_TOKENS_COUNT,
        ),
        ("gemini", "v1beta/models", server.PATH_KIND_MODELS_LIST),
        # Unclassified path → unknown (this is the operator signal to add a
        # bucket; we explicitly cover it to lock the fallback in).
        ("openai", "some/new/endpoint", server.PATH_KIND_UNKNOWN),
        ("openai", "", server.PATH_KIND_UNKNOWN),
        # Leading slash robustness
        ("openai", "/chat/completions", server.PATH_KIND_CHAT),
        ("openai", "/embeddings", server.PATH_KIND_EMBEDDING),
    ],
)
def test_path_kind_classifies(upstream, api_path, expected):
    assert server._path_kind(upstream, api_path) == expected
