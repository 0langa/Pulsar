from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from tests.conftest import requires_git

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_cli(args, home, workspace=None, stdin_text=None, extra_env=None):
    env = dict(os.environ)
    env["PULSAR_HOME"] = str(home)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    env.update(extra_env or {})
    cmd = [sys.executable, "-m", "pulsar_agent", *args]
    return subprocess.run(
        cmd,
        cwd=str(workspace or REPO_ROOT),
        env=env,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_module_help(tmp_path):
    result = run_cli(["--help"], home=tmp_path / "h")
    assert result.returncode == 0
    assert "pulsar" in result.stdout
    assert "setup" in result.stdout


def test_version(tmp_path):
    result = run_cli(["--version"], home=tmp_path / "h")
    assert result.returncode == 0
    assert "pulsar" in result.stdout


def test_setup_with_fake_key(tmp_path):
    home = tmp_path / "h"
    home.mkdir(parents=True)
    fake_key = "sk-ant-test-fake-key-1234567890abcdef"
    # Pre-seed the key so setup skips the interactive getpass prompt
    # (getpass needs a real console on Windows).
    (home / ".env").write_text(f"ANTHROPIC_API_KEY={fake_key}\n", encoding="utf-8")
    answers = "anthropic\nclaude-sonnet-5\nreview\n"
    result = run_cli(["setup"], home=home, stdin_text=answers)
    assert result.returncode == 0, result.stderr
    assert "already present" in result.stdout
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"] == "anthropic:claude-sonnet-5"
    assert config["approval_preset"] == "review"
    text = (home / "config.yaml").read_text(encoding="utf-8")
    assert fake_key not in text
    assert "api_key" not in text
    env_text = (home / ".env").read_text(encoding="utf-8")
    assert fake_key in env_text


def test_model_command_updates_config(tmp_path):
    home = tmp_path / "h"
    result = run_cli(["model", "mock:echo"], home=home)
    assert result.returncode == 0
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"] == "mock:echo"


def test_model_command_rejects_bad_id(tmp_path):
    result = run_cli(["model", "not-a-model-id"], home=tmp_path / "h")
    assert result.returncode == 2


def test_model_command_rejects_unresolvable_id(tmp_path):
    # Format-valid id whose provider key is absent must not be persisted
    # (run_cli strips OPENAI_API_KEY from the environment).
    home = tmp_path / "h"
    result = run_cli(["model", "openai:gpt-4o"], home=home)
    assert result.returncode == 2
    assert "cannot set model" in result.stderr
    config_path = home / "config.yaml"
    if config_path.exists():
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert config.get("model") != "openai:gpt-4o"


def test_once_with_mock_provider(tmp_path):
    home = tmp_path / "h"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_cli(["model", "mock:echo"], home=home)
    result = run_cli(["--once", "hello pulsar"], home=home, workspace=workspace)
    assert result.returncode == 0, result.stderr
    assert "echo: hello pulsar" in result.stdout


def test_once_with_scripted_tool_call(tmp_path):
    home = tmp_path / "h"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "data.txt").write_text("magic number 1234", encoding="utf-8")
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "data.txt"}}]},
        {"text": "file says: magic number 1234"},
    ]), encoding="utf-8")
    run_cli(["model", "mock:echo"], home=home)
    result = run_cli(
        ["--once", "read data.txt"], home=home, workspace=workspace,
        extra_env={"PULSAR_MOCK_SCRIPT": str(script)},
    )
    assert result.returncode == 0, result.stderr
    assert "file says: magic number 1234" in result.stdout


def test_sessions_list_and_delete(tmp_path):
    home = tmp_path / "h"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run_cli(["model", "mock:echo"], home=home)
    run_cli(["--once", "remember the zebra password topic"], home=home, workspace=workspace)

    listed = run_cli(["sessions", "list"], home=home)
    assert listed.returncode == 0
    session_id = listed.stdout.split()[0]

    searched = run_cli(["sessions", "search", "zebra"], home=home)
    assert searched.returncode == 0
    assert "zebra" in searched.stdout

    deleted = run_cli(["sessions", "delete", session_id], home=home)
    assert deleted.returncode == 0
    assert "deleted" in deleted.stdout

    relisted = run_cli(["sessions", "list"], home=home)
    assert session_id not in relisted.stdout


def test_terminal_output_redacted_in_session_db(tmp_path):
    """Secrets echoed by terminal must not reach the session database."""
    home = tmp_path / "h"
    workspace = tmp_path / "ws"
    workspace.mkdir()
    secret = "sk-test-e2e-secret-abcdefgh12345678"
    (home).mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text(f"DEMO_API_KEY={secret}\n", encoding="utf-8")
    script = tmp_path / "script.json"
    command = f'"{sys.executable}" -c "print(\'{secret}\')"'
    script.write_text(json.dumps([
        {"tool_calls": [{"name": "terminal", "arguments": {"command": command}}]},
        {"text": "command finished"},
    ]), encoding="utf-8")
    run_cli(["model", "mock:echo"], home=home)
    config_path = home / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["approval_preset"] = "trusted-local"
    # The python invocation is APPROVAL-tier; allowlist it so the
    # non-interactive run can execute it without a prompt.
    config["security"]["command_allowlist"] = [command]
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = run_cli(
        ["--once", "run the command"], home=home, workspace=workspace,
        extra_env={"PULSAR_MOCK_SCRIPT": str(script)},
    )
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout

    import sqlite3

    conn = sqlite3.connect(str(home / "state.db"))
    rows = conn.execute("SELECT content FROM messages").fetchall()
    conn.close()
    all_content = "\n".join(row[0] for row in rows)
    assert len(rows) >= 3
    assert secret not in all_content
    assert "[REDACTED]" in all_content


@requires_git
def test_patch_and_rollback_in_temp_repo(tmp_path):
    home = tmp_path / "h"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    target = workspace / "main.py"
    target.write_text("greeting = 'hello'\n", encoding="utf-8")

    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        {"tool_calls": [{"name": "read_file", "arguments": {"path": "main.py"}}]},
        {"tool_calls": [{"name": "patch", "arguments": {
            "path": "main.py", "old_text": "'hello'", "new_text": "'goodbye'"}}]},
        {"text": "patched"},
    ]), encoding="utf-8")
    run_cli(["model", "mock:echo"], home=home)
    config_path = home / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["approval_preset"] = "trusted-local"
    # Explicit, opt-in autonomy grant so the non-interactive patch is auto-approved.
    config.setdefault("security", {}).setdefault("autonomy", {})["allow_writes"] = True
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    result = run_cli(
        ["--once", "patch main.py"], home=home, workspace=workspace,
        extra_env={"PULSAR_MOCK_SCRIPT": str(script)},
    )
    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "greeting = 'goodbye'\n"

    from pulsar_agent.checkpoints.store import CheckpointStore

    store = CheckpointStore(home, workspace)
    entries = store.list()
    assert entries, "patch should have created a checkpoint"
    store.restore(entries[-1]["hash"])
    assert target.read_text(encoding="utf-8") == "greeting = 'hello'\n"
    assert not (workspace / ".git" / "pulsar").exists()
