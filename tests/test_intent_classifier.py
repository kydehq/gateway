"""
Tests for the LLM-backed session intent classifier (Item 12).

The real LLM call is patched — we don't want tests to need provider
credentials. Tests verify:
  - classify_session returns None when no URL is configured
  - classify_session caches results in session_intents
  - /api/sessions includes the cached intent
  - parse logic accepts the documented `label:confidence` format
"""

from unittest.mock import patch


from kyde import auth, intent_classifier, ledger

_PASSWORD = "CorrectHorse!Battery9"


def _append(session_id: str, content: str = "hello"):
    return ledger.append(
        agent_id="agent:intent",
        action_type="chat",
        model="gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": content}]},
        response_body={"choices": [{"message": {"content": "ok"}}]},
        why_messages=[{"role": "user", "content": content}],
        tool_calls=[],
        session_id=session_id,
    )


def _seed_admin(client):
    ledger.create_user(
        username="admin",
        email="admin@example.test",
        password_hash=auth.hash_password(_PASSWORD),
        roles=["admin"],
        must_change_password=False,
    )
    r = client.post(
        "/login",
        data={"username": "admin", "password": _PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_parse_response_handles_label_colon_confidence():
    assert intent_classifier._parse_response("code_review:0.83") == (
        "code_review",
        0.83,
    )
    assert intent_classifier._parse_response("data_query: 0.5") == ("data_query", 0.5)
    # Unknown labels collapse to 'other'.
    assert intent_classifier._parse_response("mystery:0.9")[0] == "other"
    # Garbage gives 'other' with 0 confidence.
    assert intent_classifier._parse_response("???")[0] == "other"


def test_classify_session_returns_none_without_config(monkeypatch):
    monkeypatch.delenv("INTENT_CLASSIFIER_URL", raising=False)
    _append("00000000-0000-4000-8000-aaaaaaaaaaaa", content="a longer message body")
    assert (
        intent_classifier.classify_session("00000000-0000-4000-8000-aaaaaaaaaaaa")
        is None
    )


def test_classify_session_caches_to_session_intents(monkeypatch):
    monkeypatch.setenv("INTENT_CLASSIFIER_URL", "http://stub")
    monkeypatch.setenv("INTENT_CLASSIFIER_MODEL", "test-model")
    sid = "00000000-0000-4000-8000-bbbbbbbbbbbb"
    _append(sid, content="Please debug this null pointer in our checkout flow")

    with patch.object(intent_classifier, "_call_model", return_value="debugging:0.92"):
        result = intent_classifier.classify_session(sid)

    assert result == {"intent": "debugging", "confidence": 0.92, "model": "test-model"}

    cached = intent_classifier.get_intent(sid)
    assert cached["intent"] == "debugging"
    assert cached["confidence"] == 0.92


def test_classify_session_returns_none_on_model_error(monkeypatch):
    monkeypatch.setenv("INTENT_CLASSIFIER_URL", "http://stub")
    sid = "00000000-0000-4000-8000-cccccccccccc"
    _append(sid, content="a message that needs classification but the call fails")

    with patch.object(
        intent_classifier, "_call_model", side_effect=RuntimeError("boom")
    ):
        assert intent_classifier.classify_session(sid) is None
    # Nothing cached on failure.
    assert intent_classifier.get_intent(sid) is None


def test_api_sessions_surfaces_cached_intent(client, monkeypatch):
    _seed_admin(client)
    sid = "00000000-0000-4000-8000-dddddddddddd"
    _append(sid, content="Summarize the last quarter's incident report")

    monkeypatch.setenv("INTENT_CLASSIFIER_URL", "http://stub")
    with patch.object(
        intent_classifier, "_call_model", return_value="summarization:0.77"
    ):
        intent_classifier.classify_session(sid)

    body = client.get("/api/sessions").json()
    target = next(s for s in body["items"] if s["session_id"] == sid)
    assert target["intent"] == "summarization"
    assert target["intent_confidence"] == 0.77


def test_api_classify_endpoint_503_without_config(client, monkeypatch):
    _seed_admin(client)
    monkeypatch.delenv("INTENT_CLASSIFIER_URL", raising=False)
    sid = "00000000-0000-4000-8000-eeeeeeeeeeee"
    _append(sid, content="this message needs classifying")
    r = client.post(f"/api/sessions/{sid}/classify")
    assert r.status_code == 503


def test_api_classify_endpoint_persists_result(client, monkeypatch):
    _seed_admin(client)
    monkeypatch.setenv("INTENT_CLASSIFIER_URL", "http://stub")
    sid = "00000000-0000-4000-8000-ffffffffffff"
    _append(sid, content="Please review my pull request before lunch")

    with patch.object(
        intent_classifier, "_call_model", return_value="code_review:0.65"
    ):
        r = client.post(f"/api/sessions/{sid}/classify")

    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "code_review"
    assert body["confidence"] == 0.65


def test_condense_trims_long_turns():
    long_content = "x" * (intent_classifier._MAX_CHARS_PER_TURN + 50)
    text = intent_classifier._condense([{"role": "user", "content": long_content}])
    assert text.startswith("user: ")
    assert text.endswith("…")
    assert len(text) < len(long_content)


def test_parse_response_handles_bad_confidence_and_unknown_label():
    # Non-numeric confidence → 0.0.
    assert intent_classifier._parse_response("code_review: high") == (
        "code_review",
        0.0,
    )
    # Unknown label falls back to "other"; confidence clamps to [0, 1].
    assert intent_classifier._parse_response("juggling: 7.5") == ("other", 1.0)


def test_classify_session_returns_none_without_turns(monkeypatch):
    monkeypatch.setenv("INTENT_CLASSIFIER_URL", "http://stub")
    # Session id that has no ledger entries at all.
    assert intent_classifier.classify_session("no-such-session") is None


def test_call_model_posts_chat_completion(monkeypatch):
    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "coding: 0.9"}}]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            captured.update(url=url, json=json, headers=headers)
            return _Resp()

    monkeypatch.setattr(intent_classifier.httpx, "Client", _Client)
    raw = intent_classifier._call_model(
        "user: hi", {"url": "http://llm.local/v1/chat", "model": "m1", "key": "sk-1"}
    )
    assert raw == "coding: 0.9"
    assert captured["url"] == "http://llm.local/v1/chat"
    assert captured["headers"]["Authorization"] == "Bearer sk-1"
    assert captured["json"]["model"] == "m1"
    assert captured["json"]["temperature"] == 0.0
