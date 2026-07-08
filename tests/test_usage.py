from __future__ import annotations

from pulsar_agent.usage import UsageTracker


def test_record_anthropic_field_names():
    tracker = UsageTracker()
    tracker.record({"input_tokens": 100, "output_tokens": 20})
    assert tracker.requests == 1
    assert tracker.input_tokens == 100
    assert tracker.output_tokens == 20


def test_record_openai_field_names():
    tracker = UsageTracker()
    tracker.record({"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60})
    assert tracker.input_tokens == 50
    assert tracker.output_tokens == 10


def test_record_cache_tokens_and_garbage_tolerated():
    tracker = UsageTracker()
    tracker.record(
        {
            "input_tokens": 5,
            "output_tokens": "not-a-number",
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 7,
        }
    )
    tracker.record({})  # empty usage block is a no-op
    assert tracker.requests == 1
    assert tracker.input_tokens == 5
    assert tracker.output_tokens == 0
    assert tracker.cache_read_tokens == 30
    assert tracker.cache_creation_tokens == 7


def test_turn_counters_reset_but_totals_accumulate():
    tracker = UsageTracker()
    tracker.record({"input_tokens": 10, "output_tokens": 5})
    tracker.start_turn()
    tracker.record({"input_tokens": 3, "output_tokens": 2})
    assert tracker.input_tokens == 13
    assert tracker.output_tokens == 7
    assert tracker.turn_input_tokens == 3
    assert tracker.turn_output_tokens == 2


def test_cost_only_with_configured_pricing():
    tracker = UsageTracker()
    tracker.record({"input_tokens": 2_000_000, "output_tokens": 1_000_000})
    assert tracker.cost(None) is None
    assert tracker.cost({}) is None
    assert tracker.cost({"input_per_mtok": 0, "output_per_mtok": 0}) is None
    cost = tracker.cost({"input_per_mtok": 3.0, "output_per_mtok": 15.0})
    assert cost == 2 * 3.0 + 1 * 15.0


def test_summary_shows_tokens_and_pricing_hint():
    tracker = UsageTracker()
    tracker.record({"input_tokens": 10, "output_tokens": 5})
    text = tracker.summary()
    assert "10 in / 5 out" in text
    assert "not configured" in text
    priced = tracker.summary({"input_per_mtok": 1.0, "output_per_mtok": 1.0})
    assert "$" in priced


def test_agent_loop_records_usage(workspace, home, config):
    # End-to-end through the mock provider: every request lands in the tracker.
    from pulsar_agent.cli.repl import Repl

    config["model"] = "mock:echo"
    repl = Repl(home=home, config=config, workspace=workspace, interactive=False)
    try:
        repl.agent.run_turn("hello")
        assert repl.usage.requests == 1
        assert repl.usage.input_tokens == 10
        assert repl.usage.output_tokens == 5
        # Tracker survives a model rebuild (e.g. /model or /new).
        repl._build_agent(new_session=True)
        repl.agent.run_turn("again")
        assert repl.usage.requests == 2
        assert repl.usage.input_tokens == 20
        assert repl.usage.turn_input_tokens == 10  # per-turn counter reset
    finally:
        repl.close()


def test_subagent_usage_folds_into_parent_tracker(workspace, home, config):
    from pulsar_agent.cli.repl import Repl
    from pulsar_agent.run_agent import run_subagent

    config["model"] = "mock:echo"
    repl = Repl(home=home, config=config, workspace=workspace, interactive=False)
    try:
        report = run_subagent(repl.agent.context, "explorer", "look around", budget=2)
        assert "explorer subagent report" in report
        assert repl.usage.requests >= 1
        assert repl.usage.input_tokens >= 10
    finally:
        repl.close()


def test_usage_slash_command(workspace, home, config, capsys):
    from pulsar_agent.cli.repl import Repl

    config["model"] = "mock:echo"
    repl = Repl(home=home, config=config, workspace=workspace, interactive=False)
    try:
        repl.agent.run_turn("ping")
        assert repl.handle_slash("/usage") is True
        out = capsys.readouterr().out
        assert "10 in / 5 out" in out
    finally:
        repl.close()
