from __future__ import annotations

import pytest

from pulsar_agent.secrets import SecretStore, parse_env_text
from pulsar_agent.security.redaction import MASK, Redactor

FAKE_KEY = "sk-test-abcdefghijklmnopqrstuv123456"


def test_parse_env_text():
    values = parse_env_text(
        "# comment\nANTHROPIC_API_KEY=abc123def\nexport OTHER='quoted'\n\nBROKEN\n"
    )
    assert values == {"ANTHROPIC_API_KEY": "abc123def", "OTHER": "quoted"}


def test_secret_store_reads_env_file(home):
    (home / ".env").write_text(f"MY_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    store = SecretStore(home)
    assert store.get("MY_API_KEY") == FAKE_KEY
    assert store.all_values() == [FAKE_KEY]


def test_secret_store_falls_back_to_process_env(home, monkeypatch):
    monkeypatch.setenv("FROM_PROCESS_TOKEN", "process-value-123")
    store = SecretStore(home)
    assert store.get("FROM_PROCESS_TOKEN") == "process-value-123"


def test_secret_store_set_writes_file(home):
    store = SecretStore(home)
    store.set("NEW_KEY", "value-abc-123")
    text = (home / ".env").read_text(encoding="utf-8")
    assert "NEW_KEY=value-abc-123" in text
    store.set("NEW_KEY", "value-def-456")
    text = (home / ".env").read_text(encoding="utf-8")
    assert text.count("NEW_KEY=") == 1
    assert "value-def-456" in text


def test_secret_store_rejects_newline_value(home):
    # A newline would corrupt the line-based .env parser and silently drop
    # later keys; reject rather than write garbage.
    store = SecretStore(home)
    store.set("GOOD", "fine-value-123")
    with pytest.raises(ValueError, match="newline"):
        store.set("BAD", "line1\nSNEAKY=injected")
    reloaded = SecretStore(home)
    assert reloaded.get("GOOD") == "fine-value-123"
    assert reloaded.get("SNEAKY") is None


def test_redactor_masks_known_values():
    redactor = Redactor([FAKE_KEY])
    assert FAKE_KEY not in redactor.redact(f"the key is {FAKE_KEY} ok")


def test_redactor_masks_patterns():
    redactor = Redactor()
    samples = [
        "sk-ant-api03-abcdefghijklmnop1234",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWX",
        "Authorization: Bearer abc123def456ghi789",
        "AWS AKIAIOSFODNN7EXAMPLE end",
        "MY_SECRET=supersecretvalue",
        "password: hunter2hunter2",
    ]
    for sample in samples:
        out = redactor.redact(sample)
        assert MASK in out, f"not redacted: {sample}"


def test_redactor_disabled_passthrough():
    redactor = Redactor([FAKE_KEY], enabled=False)
    assert redactor.redact(FAKE_KEY) == FAKE_KEY


def test_redactor_leaves_normal_text():
    redactor = Redactor()
    text = "def main():\n    return 42  # the answer"
    assert redactor.redact(text) == text
