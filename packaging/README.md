# Windows Packaging Baseline

This directory contains the Windows delivery baseline for the desktop shell/API-router container layer.

## PyInstaller build

Prerequisites:

```powershell
py -3 -m pip install -r requirements-dev.txt
```

Build:

```powershell
py -3 -m PyInstaller packaging\app.spec
```

The output directory is `dist\GasSafetyAlarm`. The spec bundles read-only application code and resources only:

- `app/config`
- `app/db/migrations`
- `app/assets`
- PySide6 Qt runtime data collected by PyInstaller hooks

The spec must not include `data/`, backups, logs, generated SQLite databases, tests, test secrets, license samples, customer data, or files under `需求以及资料/`.

## Runtime data directory

The packaged application creates mutable runtime data under a controlled per-user application data directory. It does not write next to the executable and does not require source checkout paths.

Resolution order:

1. `--data-dir <path>` command-line override for local smoke tests and support isolation.
2. `GAS_ALARM_DATA_DIR` environment override for automated tests.
3. `%LOCALAPPDATA%\GasSafetyAlarm`, then `%APPDATA%\GasSafetyAlarm`, then `%USERPROFILE%\GasSafetyAlarm` fallback.

The runtime creates these subdirectories when missing: `maps`, `backups`, `logs`, `config`, and `db`.

## Smoke validation

After building, run:

```powershell
.\packaging\smoke_test.ps1 -AppPath .\dist\GasSafetyAlarm\GasSafetyAlarm.exe
```

For source-tree validation without a built exe:

```powershell
.\packaging\smoke_test.ps1
```

The smoke script uses an isolated temporary data directory and reports only high-level checks. It must not print authorization codes, raw machine identifiers, customer data, or sensitive absolute database paths.
