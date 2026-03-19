from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import questionary
from questionary import Choice
from questionary import Style


BUNDLE_VERSION = 1
APP_STYLE = Style(
    [
        ("qmark", "fg:#5dade2 bold"),
        ("question", "bold"),
        ("answer", "fg:#5dade2"),
        ("pointer", "fg:#5dade2 bold"),
        ("highlighted", "fg:#5dade2 bold"),
        ("selected", "fg:#5dade2"),
        ("separator", "fg:#7f8c8d"),
        ("instruction", "fg:#7f8c8d"),
        ("text", ""),
    ]
)


@dataclass
class SessionRecord:
    session_id: str
    rollout_path: Path
    relative_sessions_path: str
    updated_at: int
    created_at: int
    last_message: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex_manager.py",
        description="Export and import Codex session files.",
    )
    parser.add_argument(
        "--codex-home",
        help="Override the Codex home directory. Defaults to CODEX_HOME or ~/.codex.",
    )

    subparsers = parser.add_subparsers(dest="command")

    export_parser = subparsers.add_parser("export", help="Export selected Codex sessions.")
    export_parser.add_argument(
        "-o",
        "--output",
        "--export-file",
        dest="output",
        help="Export path. Defaults to ./codex-sessions-YYYYMMDD-HHMMSS.zip.",
    )
    export_parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="How many recent sessions to show in the selection menu.",
    )
    export_parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Export a specific session id. Can be provided multiple times.",
    )

    import_parser = subparsers.add_parser("import", help="Import session file(s).")
    import_parser.add_argument(
        "bundle",
        nargs="?",
        help="Path to an exported .zip or a single .jsonl session file.",
    )
    import_parser.add_argument(
        "-i",
        "--input",
        "--import-file",
        dest="bundle_flag",
        help="Path to an exported .zip or a single .jsonl session file.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    manager = CodexSessionManager(args.codex_home)

    if args.command == "export":
        return handle_export(manager, args)
    if args.command == "import":
        return handle_import(manager, args)
    return handle_interactive_root(manager)


def handle_interactive_root(manager: "CodexSessionManager") -> int:
    print_banner()
    choice = questionary.select(
        "Choose an action",
        choices=[
            Choice("Export sessions", value="export"),
            Choice("Import sessions", value="import"),
        ],
        style=APP_STYLE,
        use_shortcuts=True,
    ).ask()

    if choice == "export":
        args = argparse.Namespace(output=None, limit=25, session_id=[])
        return handle_export(manager, args)
    if choice == "import":
        bundle = prompt_existing_file_path("Import file")
        args = argparse.Namespace(bundle=bundle, bundle_flag=None)
        return handle_import(manager, args)

    print("Invalid selection.", file=sys.stderr)
    return 1


def handle_export(manager: "CodexSessionManager", args: argparse.Namespace) -> int:
    sessions = manager.list_sessions()
    if not sessions:
        print("No Codex sessions were found.", file=sys.stderr)
        return 1

    requested_ids = list(dict.fromkeys(args.session_id))
    if requested_ids:
        by_id = {session.session_id: session for session in sessions}
        missing_ids = [session_id for session_id in requested_ids if session_id not in by_id]
        if missing_ids:
            print(f"Unknown session id(s): {', '.join(missing_ids)}", file=sys.stderr)
            return 1
        selected = [by_id[session_id] for session_id in requested_ids]
    else:
        selected = choose_sessions_interactively(sessions[: max(1, args.limit)])
        if not selected:
            print_info("No sessions selected.")
            return 0

    output_path = resolve_output_path(args.output, len(selected))
    manager.export_sessions(selected, output_path)
    print_success(f"Exported {len(selected)} session(s) to {output_path}")
    return 0


def handle_import(manager: "CodexSessionManager", args: argparse.Namespace) -> int:
    bundle = args.bundle_flag or args.bundle
    if not bundle:
        print_banner()
        bundle = prompt_existing_file_path("Import file")
    if not bundle:
        print("An import file is required.", file=sys.stderr)
        return 1

    imported = manager.import_file(Path(bundle).expanduser())
    print_success(f"Imported {imported} session(s).")
    return 0


def choose_sessions_interactively(sessions: list[SessionRecord]) -> list[SessionRecord]:
    print_banner()
    print_info("Use arrow keys to move, space to toggle sessions, and enter to confirm.")
    choices = []
    for session in sessions:
        updated = format_epoch(session.updated_at)
        message = shorten(session.last_message or session.session_id, 88)
        label = f"{updated}  {message}"
        choices.append(Choice(title=label, value=session))

    selected = questionary.checkbox(
        "Select session(s) to export",
        choices=choices,
        style=APP_STYLE,
        instruction="(Space to select, Enter to confirm)",
        validate=lambda result: True if result else "Select at least one session.",
    ).ask()
    if not selected:
        return []

    return list(selected)


def default_export_path(single_session: bool) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = ".jsonl" if single_session else ".zip"
    return Path.cwd() / f"codex-sessions-{timestamp}{suffix}"


def resolve_output_path(cli_output: str | None, selected_count: int) -> Path:
    if cli_output:
        return Path(cli_output).expanduser()

    default_path = str(default_export_path(selected_count == 1))
    chosen = questionary.path(
        "Export file path",
        default=default_path,
        style=APP_STYLE,
    ).ask()
    if not chosen:
        return Path(default_path)
    return Path(chosen).expanduser()


def prompt_existing_file_path(prompt_text: str) -> str | None:
    return questionary.path(
        prompt_text,
        style=APP_STYLE,
        validate=validate_existing_file_path,
    ).ask()


def validate_existing_file_path(value: str) -> bool | str:
    if not value or not value.strip():
        return "A file path is required."

    path = Path(value).expanduser()
    if not path.exists():
        return "That file does not exist."
    if not path.is_file():
        return "That path is not a file."
    return True


def format_epoch(value: int) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")


def shorten(value: str, width: int) -> str:
    value = " ".join(value.split())
    if len(value) <= width:
        return value
    return value[: width - 3] + "..."


def parse_iso_to_epoch(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def print_banner() -> None:
    print("\033[38;5;75m" + "=" * 58)
    print(" Codex Manager")
    print("=" * 58 + "\033[0m")


def print_success(message: str) -> None:
    print(f"\033[38;5;75m{message}\033[0m")


def print_info(message: str) -> None:
    print(message)


class CodexSessionManager:
    def __init__(self, codex_home: str | None = None) -> None:
        home = codex_home or os.environ.get("CODEX_HOME")
        self.codex_home = (Path(home).expanduser() if home else Path.home() / ".codex").resolve()
        self.sessions_dir = self.codex_home / "sessions"

    def list_sessions(self) -> list[SessionRecord]:
        sessions: list[SessionRecord] = []

        if not self.sessions_dir.exists():
            return sessions

        for rollout_path in self.sessions_dir.rglob("*.jsonl"):
            record = self._build_session_record(rollout_path)
            if record:
                sessions.append(record)

        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def export_sessions(self, sessions: list[SessionRecord], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.suffix.lower() == ".jsonl":
            if len(sessions) != 1:
                raise SystemExit("A .jsonl export path can only be used when exporting exactly one session.")
            shutil.copy2(sessions[0].rollout_path, output_path)
            return

        manifest = {
            "bundle_version": BUNDLE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_count": len(sessions),
            "sessions": [
                {
                    "session_id": session.session_id,
                    "relative_sessions_path": session.relative_sessions_path,
                }
                for session in sessions
            ],
        }

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            for session in sessions:
                archive.write(
                    session.rollout_path,
                    arcname=f"sessions/{session.relative_sessions_path}",
                )

    def import_file(self, import_path: Path) -> int:
        if not import_path.exists():
            raise SystemExit(f"Import file not found: {import_path}")

        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        if import_path.suffix.lower() == ".jsonl":
            relative_path = self._infer_relative_sessions_path(import_path)
            destination = self.sessions_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(import_path, destination)
            return 1

        imported_count = 0
        with zipfile.ZipFile(import_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            version = manifest.get("bundle_version")
            if version != BUNDLE_VERSION:
                raise SystemExit(f"Unsupported bundle version: {version}")

            for session in manifest.get("sessions", []):
                relative_path = session["relative_sessions_path"]
                destination = self.sessions_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = archive.read(f"sessions/{relative_path}")
                destination.write_bytes(payload)
                imported_count += 1

        return imported_count

    def _build_session_record(self, rollout_path: Path) -> SessionRecord | None:
        session_id: str | None = None
        created_at: int | None = None
        last_message = ""

        with rollout_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") == "session_meta":
                    payload = entry.get("payload", {})
                    session_id = payload.get("id")
                    created_at = parse_iso_to_epoch(payload.get("timestamp"))
                    continue

                if entry.get("type") == "event_msg":
                    payload = entry.get("payload", {})
                    if payload.get("type") == "user_message":
                        last_message = payload.get("message", "") or last_message

        if not session_id:
            return None

        relative_sessions_path = rollout_path.relative_to(self.sessions_dir).as_posix()
        stat = rollout_path.stat()
        created = created_at or int(stat.st_ctime)
        updated = int(stat.st_mtime)

        return SessionRecord(
            session_id=session_id,
            rollout_path=rollout_path,
            relative_sessions_path=relative_sessions_path,
            updated_at=updated,
            created_at=created,
            last_message=last_message or session_id,
        )

    def _infer_relative_sessions_path(self, import_path: Path) -> Path:
        try:
            path_from_sessions = import_path.resolve().relative_to(self.sessions_dir.resolve())
            return path_from_sessions
        except ValueError:
            pass

        created_at = None
        with import_path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
        if first_line:
            try:
                payload = json.loads(first_line)
                if payload.get("type") == "session_meta":
                    created_at = parse_iso_to_epoch(payload.get("payload", {}).get("timestamp"))
            except json.JSONDecodeError:
                created_at = None

        dt = datetime.fromtimestamp(created_at or int(import_path.stat().st_mtime))
        return Path(dt.strftime("%Y/%m/%d")) / import_path.name


def run_self_tests() -> None:
    assert parse_iso_to_epoch("2026-03-19T20:36:01.697Z") == 1773952561
    assert default_export_path(False).suffix == ".zip"
    assert default_export_path(True).suffix == ".jsonl"
    assert validate_existing_file_path(__file__) is True


if __name__ == "__main__":
    raise SystemExit(main())
