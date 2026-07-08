# Release Notes

## Windows packaging baseline

This release establishes the first Windows packaging and delivery baseline for the desktop shell/API-router container layer.

Included delivery assets:

- `packaging/app.spec` for PyInstaller one-directory builds.
- `packaging/README.md` with build and runtime data directory guidance.
- `packaging/smoke_test.ps1` for source-tree or packaged executable smoke validation.

Runtime data behavior:

- Packaged runs create mutable data in a per-user application data directory instead of next to the executable.
- `--data-dir` and `GAS_ALARM_DATA_DIR` remain available for smoke tests and support isolation.
- Required runtime subdirectories are created on startup: `maps`, `backups`, `logs`, `config`, and `db`.
- Path setup failures use controlled user-facing messages and avoid exposing sensitive absolute paths.

Security defaults verified by the smoke baseline:

- Runtime `DEBUG` remains `false` by default.
- The local API is disabled by default and its configured bind address remains loopback (`127.0.0.1`).
- Smoke output reports only high-level status and does not include authorization codes, raw machine identifiers, customer data, or sensitive absolute database paths.

Known limits:

- The PyInstaller build requires PyInstaller to be installed in the build environment; it is not part of runtime dependencies.
- Code signing, installer packaging, update channels, and customer-specific deployment paths remain outside this baseline.
- License artifact backup/restore behavior is still `[待确认]` and is not included in packaging assets.
