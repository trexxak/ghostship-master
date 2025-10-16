#!/usr/bin/env python3
"""Prep and launch the autonomous forum simulator with one command.

The script mirrors `manage.py` by loading `.env`, (optionally) blowing away the
local SQLite database, running migrations, seeding lore, nudging the generation
queue, and finally starting the Django development server.

Examples
--------
python scripts/dev_bootstrap_and_run.py
python scripts/dev_bootstrap_and_run.py --keep-db --limit 2
python scripts/dev_bootstrap_and_run.py --no-server --with-specters
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parents[1]
MANAGE_DIR = ROOT / "forum_simulator"
PYTHON = sys.executable
DB_PATH = MANAGE_DIR / "db.sqlite3"

def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


DEFAULT_QUEUE_LIMIT = _int_env("GEN_QUEUE_START_LIMIT", 5)
DEFAULT_RESET = os.getenv("FORUM_RESET", "1").lower() not in {"0", "false", "no"}
DEFAULT_RUNSERVER_ADDR = os.getenv("RUNSERVER_ADDR")

os.environ.setdefault("FORUM_AUTO_TICKS", "1")


def load_env_file() -> None:
    """Load environment variables from `.env` if it exists."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    else:
        load_dotenv(env_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reset, migrate, seed lore, process generation tasks, and launch the dev server."
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Reuse the existing database instead of wiping it (env FORUM_RESET=0).",
    )
    parser.add_argument(
        "--force-reset",
        action="store_true",
        help="Force a reset even if --keep-db was supplied earlier.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_QUEUE_LIMIT,
        help=f"Generation tasks to process before serving (default: {DEFAULT_QUEUE_LIMIT}).",
    )
    parser.add_argument(
        "--no-generation",
        action="store_true",
        help="Skip processing the generation queue before running the server.",
    )
    parser.add_argument(
        "--no-server",
        action="store_true",
        help="Perform setup tasks but do not launch the Django development server.",
    )
    parser.add_argument(
        "--runserver-addr",
        default=DEFAULT_RUNSERVER_ADDR,
        help="Host:port passed to runserver (defaults to Django's 127.0.0.1:8000).",
    )
    parser.add_argument(
        "--runserver-arg",
        dest="runserver_args",
        action="append",
        default=[],
        help="Additional argument to pass to runserver (can be repeated).",
    )
    parser.add_argument(
        "--with-specters",
        action="store_true",
        help="Pass --with-specters to bootstrap_lore for the full lore seed.",
    )
    return parser.parse_args()


def build_commands(args: argparse.Namespace) -> List[List[str]]:
    commands: List[List[str]] = [
        [PYTHON, "manage.py", "makemigrations", "forum"],
        [PYTHON, "manage.py", "migrate"],
    ]

    bootstrap_cmd = [PYTHON, "manage.py", "bootstrap_lore"]
    if args.with_specters:
        bootstrap_cmd.append("--with-specters")
    commands.append(bootstrap_cmd)

    if not args.no_generation:
        limit = max(args.limit or DEFAULT_QUEUE_LIMIT, 1)
        commands.append(
            [PYTHON, "manage.py", "process_generation_queue", "--limit", str(limit)]
        )
    else:
        print(">>> Skipping generation queue processing (--no-generation).", flush=True)

    if not args.no_server:
        runserver: List[str] = [PYTHON, "manage.py", "runserver"]
        if args.runserver_addr:
            runserver.append(args.runserver_addr)
        if args.runserver_args:
            runserver.extend(args.runserver_args)
        commands.append(runserver)
    return commands


def run_command(cmd: Iterable[str]) -> None:
    command_list = list(cmd)
    display = " ".join(command_list)
    print(f"\n=== Running: {display}\n", flush=True)
    subprocess.run(command_list, cwd=MANAGE_DIR, check=True)


def reset_datastore() -> None:
    if DB_PATH.exists():
        print(f"\n=== Removing {DB_PATH} for a clean reset\n", flush=True)
        try:
            DB_PATH.unlink()
        except FileNotFoundError:
            pass
        journal = DB_PATH.with_name(DB_PATH.name + "-journal")
        try:
            journal.unlink()
        except FileNotFoundError:
            pass
    else:
        print(">>> No SQLite file found; using Django flush.", flush=True)
        run_command([PYTHON, "manage.py", "flush", "--no-input"])


def main() -> None:
    load_env_file()

    if not MANAGE_DIR.exists():
        raise SystemExit(f"Expected manage.py directory at {MANAGE_DIR}")

    args = parse_args()
    commands = build_commands(args)

    if args.force_reset:
        reset_db = True
    elif args.keep_db:
        reset_db = False
    else:
        reset_db = DEFAULT_RESET

    if reset_db:
        reset_datastore()
    else:
        print(">>> Keeping existing database (--keep-db).", flush=True)

    for cmd in commands:
        try:
            run_command(cmd)
        except subprocess.CalledProcessError as exc:
            print(
                f"Command failed (exit {exc.returncode}): {' '.join(cmd)}",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.\n", flush=True)
