from __future__ import annotations

from pulsar_agent.security.redaction import Redactor
from pulsar_agent.sessions.store import SessionStore


def make_store(tmp_path, redactor=None):
    return SessionStore(tmp_path / "state.db", redactor)


def test_create_append_get(tmp_path):
    store = make_store(tmp_path)
    session_id = store.create_session(workspace="/w", model_id="mock:echo")
    store.append_message(session_id, "user", "fix the login bug")
    store.append_message(session_id, "assistant", "looking at auth.py now")
    messages = store.get_messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    sessions = store.list_sessions()
    assert sessions[0]["id"] == session_id
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["title"].startswith("fix the login")
    store.close()


def test_search_returns_snippets(tmp_path):
    store = make_store(tmp_path)
    s1 = store.create_session()
    s2 = store.create_session()
    store.append_message(s1, "user", "refactor the database connection pool")
    store.append_message(s2, "user", "write documentation for the CLI")
    results = store.search("database pool")
    assert len(results) == 1
    assert results[0]["session_id"] == s1
    assert "database" in results[0]["snippet"]
    assert store.search("nonexistentterm") == []
    store.close()


def test_search_handles_special_chars(tmp_path):
    store = make_store(tmp_path)
    session = store.create_session()
    store.append_message(session, "user", "fix the parser")
    assert store.search('fix AND "the" OR (parser)') != []
    assert store.search("!!!") == []
    store.close()


def test_delete_session(tmp_path):
    store = make_store(tmp_path)
    session = store.create_session()
    store.append_message(session, "user", "delete me later")
    assert store.delete_session(session) is True
    assert store.delete_session(session) is False
    assert store.list_sessions() == []
    assert store.search("delete me") == []
    store.close()


def test_messages_redacted_before_persistence(tmp_path):
    secret = "sk-test-persisted-secret-0123456789"
    store = make_store(tmp_path, Redactor([secret]))
    session = store.create_session()
    store.append_message(session, "tool", f"output contains {secret}", "terminal")
    messages = store.get_messages(session)
    assert secret not in messages[0]["content"]
    assert "[REDACTED]" in messages[0]["content"]
    results = store.search("output contains")
    assert results and secret not in results[0]["snippet"]
    store.close()
