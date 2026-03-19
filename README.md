# Codex Session Porter

Single-file CLI utility for exporting and importing local Codex sessions.

## What it does

- Lists recent sessions from `~/.codex/sessions` with the newest first.
- Exports one or more selected sessions into a zip bundle.
- Uses an arrow-key driven interactive UI with restrained colors for the action menu and session picker.
- Bundles the rollout file plus the matching `threads` row from `state_5.sqlite` and matching entries from `history.jsonl`.
- Imports that bundle back into a local Codex profile by restoring the rollout file, upserting the `threads` row, and appending any missing history entries.

## Requirements

```powershell
pip install -r requirements.txt
```

## Usage

Interactive mode:

```powershell
python .\codex_session_porter.py
```

Interactive export asks for an output bundle path and defaults it to the current directory.

Direct export:

```powershell
python .\codex_session_porter.py export
```

Direct export with an explicit output file:

```powershell
python .\codex_session_porter.py export --export-file .\session-export.zip
```

Export specific session ids:

```powershell
python .\codex_session_porter.py export --session-id 019d07cf-f21b-7ae0-9c51-3704a5630c7c -o .\session-export.zip
```

Import a bundle:

```powershell
python .\codex_session_porter.py import .\session-export.zip
```

Import a bundle with an explicit flag:

```powershell
python .\codex_session_porter.py import --import-file .\session-export.zip
```

Override the Codex home directory:

```powershell
python .\codex_session_porter.py --codex-home C:\path\to\.codex export
```
