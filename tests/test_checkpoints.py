from __future__ import annotations

from pulsar_agent.checkpoints.store import CheckpointStore
from tests.conftest import requires_git


@requires_git
def test_snapshot_and_restore(home, workspace):
    (workspace / "keep.txt").write_text("v1")
    store = CheckpointStore(home, workspace)
    first = store.snapshot("initial")
    assert first

    (workspace / "keep.txt").write_text("v2")
    (workspace / "new.txt").write_text("added later")
    second = store.snapshot("after edit")
    assert second and second != first

    store.restore(first)
    assert (workspace / "keep.txt").read_text() == "v1"
    assert not (workspace / "new.txt").exists()


@requires_git
def test_snapshot_no_changes_returns_none(home, workspace):
    (workspace / "a.txt").write_text("x")
    store = CheckpointStore(home, workspace)
    assert store.snapshot("first") is not None
    assert store.snapshot("no changes") is None


@requires_git
def test_shadow_repo_outside_project_git(home, workspace):
    store = CheckpointStore(home, workspace)
    (workspace / "f.txt").write_text("data")
    store.snapshot("snap")
    assert not (workspace / ".git").exists()
    assert store.git_dir.is_relative_to(home / "checkpoints")


@requires_git
def test_env_files_excluded_from_checkpoints(home, workspace):
    (workspace / ".env").write_text("SECRET_KEY=topsecret123")
    (workspace / "code.py").write_text("print(1)")
    store = CheckpointStore(home, workspace)
    store.snapshot("snap")
    tracked = store._git("ls-tree", "-r", "--name-only", "HEAD").stdout
    assert "code.py" in tracked
    assert ".env" not in tracked


@requires_git
def test_list_checkpoints(home, workspace):
    store = CheckpointStore(home, workspace)
    (workspace / "a.txt").write_text("1")
    store.snapshot("first snap")
    (workspace / "a.txt").write_text("2")
    store.snapshot("second snap")
    entries = store.list()
    assert len(entries) == 2
    assert entries[0]["label"] == "second snap"


@requires_git
def test_rollback_is_reversible(home, workspace):
    store = CheckpointStore(home, workspace)
    (workspace / "a.txt").write_text("original")
    first = store.snapshot("first")
    (workspace / "a.txt").write_text("modified")
    store.restore(first)
    assert (workspace / "a.txt").read_text() == "original"
    labels = [e["label"] for e in store.list()]
    assert "pre-rollback snapshot" in labels
