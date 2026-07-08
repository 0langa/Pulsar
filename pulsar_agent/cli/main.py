"""Pulsar CLI entry point: `pulsar` and `python -m pulsar_agent`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pulsar_agent import __version__
from pulsar_agent.config import ConfigError, config_warnings, load_config, save_config
from pulsar_agent.home import ensure_home_layout, get_pulsar_home
from pulsar_agent.providers.router import parse_model_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pulsar",
        description=(
            "Pulsar: a local-first coding agent. Run with no arguments for the "
            "interactive CLI."
        ),
    )
    parser.add_argument("--version", action="version", version=f"pulsar {__version__}")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Project directory to work in (default: current directory)",
    )
    parser.add_argument(
        "--once",
        metavar="MESSAGE",
        help="Run a single non-interactive turn and print the reply",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Start the full-screen TUI (requires the 'tui' extra: "
        'pip install "pulsar-agent[tui]")',
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("setup", help="Interactive first-run configuration")

    model_parser = subparsers.add_parser("model", help="Set the default model")
    model_parser.add_argument("model_id", help="provider:model, e.g. anthropic:claude-sonnet-5")

    sessions_parser = subparsers.add_parser("sessions", help="Manage stored sessions")
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_sub.add_parser("list", help="List recent sessions")
    delete_parser = sessions_sub.add_parser("delete", help="Delete a session by id")
    delete_parser.add_argument("session_id")
    search_parser = sessions_sub.add_parser("search", help="Full-text search past sessions")
    search_parser.add_argument("query")

    return parser


def _sessions_command(args: argparse.Namespace, home: Path) -> int:
    from pulsar_agent.secrets import SecretStore
    from pulsar_agent.security.redaction import Redactor
    from pulsar_agent.sessions.store import DB_FILENAME, SessionStore

    secrets = SecretStore(home)
    store = SessionStore(home / DB_FILENAME, Redactor(secrets.all_values()))
    try:
        if args.sessions_command == "list":
            sessions = store.list_sessions()
            if not sessions:
                print("no sessions")
                return 0
            for session in sessions:
                print(
                    f"{session['id']}  {session['updated_at']}  "
                    f"[{session['message_count']} msgs]  {session['title'][:50]}"
                )
            return 0
        if args.sessions_command == "delete":
            if store.delete_session(args.session_id):
                print(f"deleted session {args.session_id}")
                return 0
            print(f"session {args.session_id} not found")
            return 1
        if args.sessions_command == "search":
            results = store.search(args.query)
            if not results:
                print("no matches")
                return 0
            for result in results:
                print(f"{result['session_id']}  {result['title'][:40]}\n  {result['snippet']}")
            return 0
    finally:
        store.close()
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    home = ensure_home_layout(get_pulsar_home())

    if args.command == "setup":
        from pulsar_agent.cli.setup_wizard import run_setup

        return run_setup(home)

    try:
        config = load_config(home)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    for warning in config_warnings(config):
        print(f"warning: {warning}", file=sys.stderr)

    if args.command == "model":
        # Resolve fully (provider profile + key presence) before persisting so
        # a format-valid but unusable id never bricks the next startup — same
        # guarantee the interactive /model command gives.
        from pulsar_agent.providers.router import resolve_runtime_provider
        from pulsar_agent.secrets import SecretStore

        try:
            parse_model_id(args.model_id)
            resolve_runtime_provider(args.model_id, config, SecretStore(home))
        except Exception as exc:
            print(f"cannot set model: {exc}", file=sys.stderr)
            print("Hint: run `pulsar setup` to configure the provider and key.", file=sys.stderr)
            return 2
        config["model"] = args.model_id
        save_config(home, config)
        print(f"default model set to {args.model_id}")
        return 0

    if args.command == "sessions":
        return _sessions_command(args, home)

    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"workspace {workspace} is not a directory", file=sys.stderr)
        return 2

    if args.tui:
        if args.once is not None:
            print("--tui and --once cannot be combined", file=sys.stderr)
            return 2
        from pulsar_agent.cli.tui import run_tui

        return run_tui(home, config, workspace)

    from pulsar_agent.cli.repl import Repl

    try:
        repl = Repl(
            home=home,
            config=config,
            workspace=workspace,
            interactive=args.once is None,
        )
    except Exception as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        print("Hint: run `pulsar setup` to configure a provider and key.", file=sys.stderr)
        return 2

    if args.once is not None:
        from pulsar_agent.cli.repl import recovery_hint

        try:
            print(repl.run_once(args.once))
            return 0
        except Exception as exc:
            message = repl.redactor.redact(str(exc))
            print(f"error: {message}", file=sys.stderr)
            hint = recovery_hint(message)
            if hint:
                print(f"hint: {hint}", file=sys.stderr)
            return 1

    return repl.run()


if __name__ == "__main__":
    raise SystemExit(main())
