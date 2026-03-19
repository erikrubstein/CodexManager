from __future__ import annotations

import argparse
import json
import os
import sqlite3
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
THREAD_COLUMNS = [
    "id",
    "rollout_path",
    "created_at",
    "updated_at",
    "source",
    "model_provider",
    "cwd",
    "title",
    "sandbox_policy",
    "approval_mode",
    "tokens_used",
    "has_user_event",
    "archived",
    "archived_at",
    "git_sha",
    "git_branch",
    "git_origin_url",
    "cli_version",
    "first_user_message",
    "agent_nickname",
    "agent_role",
    "memory_mode",
    "model",
    "reasoning_effort",
]


@dataclass
class SessionRecord:
    session_id: str
    rollout_path: Path
    relative_rollout_path: str
    updated_at: int
    created_at: int
    title: str
    first_user_message: str
    cli_version: str
    thread_row: dict[str, Any] | None
    history_entries: list[dict[str, Any]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex_manager.py",
        description="Export and import Codex sessions.",
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
        help="Path to the export bundle. Defaults to ./codex-sessions-YYYYMMDD-HHMMSS.zip.",
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

    import_parser = subparsers.add_parser("import", help="Import a Codex session bundle.")
    import_parser.add_argument("bundle", nargs="?", help="Path to a .zip bundle created by this tool.")
    import_parser.add_argument(
        "-i",
        "--input",
        "--import-file",
        dest="bundle_flag",
        help="Path to a .zip bundle created by this tool.",
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
            Choice("Import bundle", value="import"),
        ],
        style=APP_STYLE,
        use_shortcuts=True,
    ).ask()

    if choice == "export":
        args = argparse.Namespace(output=None, limit=25, session_id=[])
        return handle_export(manager, args)
    if choice == "import":
        bundle = questionary.path(
            "Bundle path",
            style=APP_STYLE,
            only_files=True,
        ).ask()
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

    output_path = resolve_output_path(args.output)
    manager.export_sessions(selected, output_path)
    print_success(f"Exported {len(selected)} session(s) to {output_path}")
    return 0


def handle_import(manager: "CodexSessionManager", args: argparse.Namespace) -> int:
    bundle = args.bundle_flag or args.bundle
    if not bundle:
        print_banner()
        bundle = questionary.path(
            "Bundle path",
            style=APP_STYLE,
            only_files=True,
        ).ask()
    if not bundle:
        print("A bundle path is required.", file=sys.stderr)
        return 1

    imported = manager.import_bundle(Path(bundle).expanduser())
    print_success(f"Imported {imported} session(s).")
    return 0


def choose_sessions_interactively(sessions: list[SessionRecord]) -> list[SessionRecord]:
    print_banner()
    print_info("Use arrow keys to move, space to toggle sessions, and enter to confirm.")
    choices = []
    for session in sessions:
        updated = format_epoch(session.updated_at)
        title = shorten(session.title or session.first_user_message or session.session_id, 88)
        label = f"{updated}  {title}"
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


def default_bundle_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path.cwd() / f"codex-sessions-{timestamp}.zip"


def resolve_output_path(cli_output: str | None) -> Path:
    if cli_output:
        return Path(cli_output).expanduser()

    default_path = str(default_bundle_path())
    chosen = questionary.path(
        "Output bundle path",
        default=default_path,
        style=APP_STYLE,
    ).ask()
    if not chosen:
        return Path(default_path)
    return Path(chosen).expanduser()


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
    print(" Codex Session Porter")
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
        self.history_path = self.codex_home / "history.jsonl"
        self.state_db_path = self.codex_home / "state_5.sqlite"

    def list_sessions(self) -> list[SessionRecord]:
        thread_rows = self._load_thread_rows()
        history_map = self._load_history_entries()
        sessions: list[SessionRecord] = []

        if not self.sessions_dir.exists():
            return sessions

        for rollout_path in self.sessions_dir.rglob("*.jsonl"):
            record = self._build_session_record(rollout_path, thread_rows, history_map)
            if record:
                sessions.append(record)

        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def export_sessions(self, sessions: list[SessionRecord], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "bundle_version": BUNDLE_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "codex_home": str(self.codex_home),
            "session_count": len(sessions),
            "sessions": [],
        }

        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for session in sessions:
                base = f"sessions/{session.session_id}"
                archive.write(session.rollout_path, arcname=f"{base}/rollout.jsonl")
                archive.writestr(
                    f"{base}/thread.json",
                    json.dumps(session.thread_row or self._thread_row_from_session(session), indent=2),
                )
                archive.writestr(f"{base}/history.json", json.dumps(session.history_entries, indent=2))
                manifest["sessions"].append(
                    {
                        "session_id": session.session_id,
                        "relative_rollout_path": session.relative_rollout_path,
                        "created_at": session.created_at,
                        "updated_at": session.updated_at,
                        "title": session.title,
                        "cli_version": session.cli_version,
                    }
                )

            archive.writestr("manifest.json", json.dumps(manifest, indent=2))

    def import_bundle(self, bundle_path: Path) -> int:
        if not bundle_path.exists():
            raise SystemExit(f"Bundle not found: {bundle_path}")

        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        imported_count = 0

        with zipfile.ZipFile(bundle_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self._validate_manifest(manifest)

            for session_info in manifest["sessions"]:
                session_id = session_info["session_id"]
                base = f"sessions/{session_id}"
                thread_row = json.loads(archive.read(f"{base}/thread.json").decode("utf-8"))
                history_entries = json.loads(archive.read(f"{base}/history.json").decode("utf-8"))
                rollout_bytes = archive.read(f"{base}/rollout.jsonl")
                rollout_path = self._resolve_import_rollout_path(session_info, thread_row)

                rollout_path.parent.mkdir(parents=True, exist_ok=True)
                rollout_path.write_bytes(rollout_bytes)

                normalized_thread = self._normalize_thread_row(thread_row, rollout_path)
                self._upsert_thread_row(normalized_thread)
                self._append_history_entries(history_entries, session_id)
                imported_count += 1

        return imported_count

    def _build_session_record(
        self,
        rollout_path: Path,
        thread_rows: dict[str, dict[str, Any]],
        history_map: dict[str, list[dict[str, Any]]],
    ) -> SessionRecord | None:
        session_meta = self._read_session_meta(rollout_path)
        if not session_meta:
            return None

        session_id = session_meta["payload"]["id"]
        relative_rollout_path = rollout_path.relative_to(self.codex_home).as_posix()
        thread_row = thread_rows.get(session_id)
        history_entries = history_map.get(session_id, [])

        created_at = self._coalesce_int(
            thread_row.get("created_at") if thread_row else None,
            parse_iso_to_epoch(session_meta["payload"].get("timestamp")),
            int(rollout_path.stat().st_ctime),
        )
        updated_at = self._coalesce_int(
            thread_row.get("updated_at") if thread_row else None,
            history_entries[-1]["ts"] if history_entries else None,
            int(rollout_path.stat().st_mtime),
        )
        title = (
            (thread_row or {}).get("title")
            or (thread_row or {}).get("first_user_message")
            or (history_entries[0]["text"] if history_entries else "")
            or session_id
        )
        first_user_message = (
            (thread_row or {}).get("first_user_message")
            or (history_entries[0]["text"] if history_entries else "")
            or title
        )

        return SessionRecord(
            session_id=session_id,
            rollout_path=rollout_path,
            relative_rollout_path=relative_rollout_path,
            updated_at=updated_at,
            created_at=created_at,
            title=title,
            first_user_message=first_user_message,
            cli_version=(thread_row or {}).get("cli_version") or session_meta["payload"].get("cli_version", ""),
            thread_row=thread_row,
            history_entries=history_entries,
        )

    def _read_session_meta(self, rollout_path: Path) -> dict[str, Any] | None:
        with rollout_path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
        if not first_line:
            return None
        payload = json.loads(first_line)
        if payload.get("type") != "session_meta":
            return None
        return payload

    def _load_thread_rows(self) -> dict[str, dict[str, Any]]:
        if not self.state_db_path.exists():
            return {}

        query = f"SELECT {', '.join(THREAD_COLUMNS)} FROM threads"
        with sqlite3.connect(self.state_db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(query).fetchall()
        return {row["id"]: dict(row) for row in rows}

    def _load_history_entries(self) -> dict[str, list[dict[str, Any]]]:
        history_map: dict[str, list[dict[str, Any]]] = {}
        if not self.history_path.exists():
            return history_map

        with self.history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                session_id = entry.get("session_id")
                if not session_id:
                    continue
                history_map.setdefault(session_id, []).append(entry)

        for entries in history_map.values():
            entries.sort(key=lambda item: item.get("ts", 0))
        return history_map

    def _validate_manifest(self, manifest: dict[str, Any]) -> None:
        version = manifest.get("bundle_version")
        if version != BUNDLE_VERSION:
            raise SystemExit(f"Unsupported bundle version: {version}")

    def _resolve_import_rollout_path(self, session_info: dict[str, Any], thread_row: dict[str, Any]) -> Path:
        relative = session_info.get("relative_rollout_path")
        if relative:
            return self.codex_home / Path(relative)

        original_rollout = thread_row.get("rollout_path")
        if original_rollout:
            original_path = Path(original_rollout)
            if "sessions" in original_path.parts:
                index = original_path.parts.index("sessions")
                return self.codex_home.joinpath(*original_path.parts[index:])

        created_at = self._coalesce_int(
            thread_row.get("created_at"),
            session_info.get("created_at"),
            int(datetime.now().timestamp()),
        )
        dt = datetime.fromtimestamp(created_at)
        filename = f"rollout-{dt.strftime('%Y-%m-%dT%H-%M-%S')}-{session_info['session_id']}.jsonl"
        return self.sessions_dir / dt.strftime("%Y/%m/%d") / filename

    def _normalize_thread_row(self, thread_row: dict[str, Any], rollout_path: Path) -> dict[str, Any]:
        normalized = {column: thread_row.get(column) for column in THREAD_COLUMNS}
        normalized["rollout_path"] = str(rollout_path)
        normalized["cwd"] = normalized.get("cwd") or str(Path.home())
        normalized["source"] = normalized.get("source") or "cli"
        normalized["model_provider"] = normalized.get("model_provider") or "openai"
        normalized["title"] = normalized.get("title") or normalized.get("first_user_message") or normalized["id"]
        normalized["first_user_message"] = normalized.get("first_user_message") or normalized["title"]
        normalized["sandbox_policy"] = normalized.get("sandbox_policy") or "{}"
        normalized["approval_mode"] = normalized.get("approval_mode") or "on-request"
        normalized["tokens_used"] = int(normalized.get("tokens_used") or 0)
        normalized["has_user_event"] = int(normalized.get("has_user_event") or 0)
        normalized["archived"] = int(normalized.get("archived") or 0)
        normalized["memory_mode"] = normalized.get("memory_mode") or "enabled"
        normalized["created_at"] = self._coalesce_int(normalized.get("created_at"), int(rollout_path.stat().st_ctime))
        normalized["updated_at"] = self._coalesce_int(normalized.get("updated_at"), normalized["created_at"])
        normalized["cli_version"] = normalized.get("cli_version") or ""
        return normalized

    def _upsert_thread_row(self, thread_row: dict[str, Any]) -> None:
        if not self.state_db_path.exists():
            raise SystemExit(f"Codex state database not found: {self.state_db_path}")

        columns = ", ".join(THREAD_COLUMNS)
        placeholders = ", ".join("?" for _ in THREAD_COLUMNS)
        update_columns = ", ".join(f"{column}=excluded.{column}" for column in THREAD_COLUMNS if column != "id")
        values = [thread_row.get(column) for column in THREAD_COLUMNS]

        with sqlite3.connect(self.state_db_path) as connection:
            connection.execute(
                f"""
                INSERT INTO threads ({columns})
                VALUES ({placeholders})
                ON CONFLICT(id) DO UPDATE SET {update_columns}
                """,
                values,
            )
            connection.commit()

    def _append_history_entries(self, entries: list[dict[str, Any]], session_id: str) -> None:
        existing = set()
        if self.history_path.exists():
            with self.history_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if entry.get("session_id") != session_id:
                        continue
                    existing.add(self._history_key(entry))

        new_entries = [entry for entry in entries if self._history_key(entry) not in existing]
        if not new_entries:
            return

        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8", newline="\n") as handle:
            for entry in sorted(new_entries, key=lambda item: item.get("ts", 0)):
                handle.write(json.dumps(entry, ensure_ascii=True) + "\n")

    def _history_key(self, entry: dict[str, Any]) -> tuple[Any, Any, Any]:
        return (entry.get("session_id"), entry.get("ts"), entry.get("text"))

    def _thread_row_from_session(self, session: SessionRecord) -> dict[str, Any]:
        row = {column: None for column in THREAD_COLUMNS}
        row["id"] = session.session_id
        row["rollout_path"] = str(session.rollout_path)
        row["created_at"] = session.created_at
        row["updated_at"] = session.updated_at
        row["source"] = "cli"
        row["model_provider"] = "openai"
        row["cwd"] = str(Path.home())
        row["title"] = session.title
        row["sandbox_policy"] = "{}"
        row["approval_mode"] = "on-request"
        row["tokens_used"] = 0
        row["has_user_event"] = 0
        row["archived"] = 0
        row["cli_version"] = session.cli_version
        row["first_user_message"] = session.first_user_message
        row["memory_mode"] = "enabled"
        return row

    def _coalesce_int(self, *values: Any) -> int:
        for value in values:
            if value is None or value == "":
                continue
            return int(value)
        raise ValueError("Expected at least one numeric value")


def run_self_tests() -> None:
    assert parse_iso_to_epoch("2026-03-19T20:36:01.697Z") == 1773952561
    assert default_bundle_path().suffix == ".zip"


if __name__ == "__main__":
    raise SystemExit(main())
