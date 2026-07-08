# Backend Standards

## 1. Scope and Evidence

This document is the project-level engineering baseline for the Windows desktop gas safety alarm monitoring system. It is based on:

- `specs/requirement.md`
- `需求以及资料/功能实现.md`
- Spot checks of Modbus protocol documents under `需求以及资料/`

Current repository state: new project with no source code, dependency manifest, entry point, migrations, or build script. All conventions below are initial project baselines and must be recalibrated after the first runnable skeleton exists.

## 2. Runtime and Technology Baseline

- Target platform: Windows 10/11 desktop.
- Language baseline: Python 3, with exact supported Python version to be fixed by the first dependency lock file. [待校准]
- Desktop UI: use one Qt binding consistently across the project. Default baseline is `PySide6`; `PyQt6` is allowed only if chosen before UI implementation and then used consistently.
- Local database: SQLite.
- Serial access: `pyserial` or an equivalent wrapper behind the communication channel abstraction.
- TCP access: RTU over TCP by default for 485-to-LAN devices. Standard Modbus TCP is out of scope until confirmed.
- Local read-only API: `FastAPI` or `Flask`, hosted inside the desktop application process or a controlled child runtime. Default baseline is FastAPI if no implementation constraint appears later. [待校准]
- Scheduling: a single project scheduling abstraction over Qt timers, worker threads, or APScheduler; do not scatter independent timers across business modules.
- Packaging: PyInstaller or Nuitka. The chosen tool must produce a Windows runnable exe or portable directory and include SQLite initialization, config defaults, maps/backups/log directories, and required Qt plugins.

## 3. Project Directory Baseline

Until implementation proves otherwise, use the following top-level boundaries:

```text
app/                 Python application package
app/main.py          desktop entry point
app/config/          configuration loading and defaults
app/core/            application bootstrap, state store, event bus, scheduler, logging
app/db/              SQLite connection, migrations, repositories
app/device/          communication channels, polling, protocol adapters, device debug support
app/services/        business application services
app/ui/              PySide6/PyQt6 windows, widgets, dialogs, view models
app/api/             local read-only HTTP API integration
app/assets/          bundled UI assets only
tests/               unit and integration tests
data/                local runtime data in development only
docs/                project standards and delivery documentation
需求以及资料/          source requirement and protocol documents; do not edit during implementation
specs/               workspace requirement documents; do not treat as runtime assets
```

Runtime data created by packaged builds should live under a controlled application data directory, not under `Program Files`. Development may use `data/` for local SQLite files, maps, backups, and logs.

## 4. Architecture-Neutral Layer Map

Layer classification is based on dependency direction, reuse scope, and whether the capability can be independently accepted. Directory, package, application, or service names are not automatically business modules.

### 基础/平台层 (Foundation / Platform Layer)

Responsibilities used by most other code and not independently accepted as end-user business features:

- Application bootstrap and lifecycle.
- Configuration loading, validation, and default values.
- Logging, audit-log sinks, error boundaries, and redaction helpers.
- SQLite connection management, transactions, migrations, and repository base utilities.
- Scheduler, event bus, in-memory state store, worker execution, cancellation primitives.
- File path management for maps, backups, logs, config, and license artifacts.
- Shared cryptographic and signing helpers used by authorization, license, and integrity checks.

Rules:

- Foundation code must not import UI pages or business services.
- Foundation code may expose small interfaces and utilities, but must not contain gas-alarm business decisions.

### 集合/容器层 (Collection / Container Layer)

Containers assemble multiple capabilities and are not business modules by themselves:

- Desktop shell and main window navigation.
- Local API host process/thread and HTTP router collection.
- Data directory layout and packaged runtime container.
- Test harnesses, simulator runners, and delivery packaging scripts.
- `app/services/`, `app/ui/`, `app/device/`, and `app/api/` if used as broad directories.

Rules:

- A container layer groups entry points, pages, routes, or services; it must not be named as a single business capability in planning or acceptance.
- Business ownership must be assigned to the independently accepted capability inside the container, such as authorization, acquisition, alarm state, map monitoring, backup restore, or records query.

### 局部通用层 (Local Shared Layer)

Reusable within a limited part of the system:

- Protocol adapter base types and common Modbus frame helpers.
- Serial/TCP channel abstractions used by acquisition and device debug.
- Repository implementations used by services.
- UI common widgets, table models, validators, dialogs, and view models.
- Import/export, print/PDF, backup packaging, and file validation helpers.
- Permission guard helpers used by UI and services.

Rules:

- Local shared code must stay protocol- or domain-neutral where practical.
- Protocol-specific code belongs behind `protocol_1` or `protocol_2` adapters and must not leak register details into UI or general services.

### 业务模块层 (Business Module Layer)

Independently accepted capabilities from the requirement scope:

- Authorization and login/session handling.
- User and permission management.
- License validation and activation status.
- Port, controller, detector, gas type, and map configuration.
- Device acquisition lifecycle and real-time state update.
- Protocol 1 and Protocol 2 adaptation into the unified device reading model.
- Alarm state machine, active alarms, recovery handling, and linkage trigger coordination.
- Real-time monitoring, device card monitoring, map point monitoring, charts, records query, big screen display.
- Backup, scheduled backup, restore, and restore safety checks.
- Local read-only API read model.
- Device debug and simulator support.

Rules:

- Business modules may depend on foundation and local shared interfaces.
- Business modules must communicate through service methods, events, or explicit state-store updates; do not couple UI widgets directly to serial/TCP, Modbus parsing, or SQLite internals.

## 5. Python and Qt Coding Conventions

- Python files and packages: `snake_case.py` and lower-case package names.
- Python functions, methods, variables, and attributes: `snake_case`.
- Classes, dataclasses, Qt widgets, and service classes: `PascalCase`.
- Constants: `UPPER_SNAKE_CASE`.
- Qt signals: descriptive `snake_case` names ending with `_changed`, `_received`, `_failed`, or `_requested` where applicable.
- UI classes must not block the Qt main thread. Long-running acquisition, backup, restore, import/export, and API startup work must run through worker abstractions and report results back through Qt-safe signals/events.
- Prefer small typed value objects for protocol requests, parsed readings, service results, and state snapshots. Avoid passing unstructured dicts across layers except at API serialization boundaries.
- Keep Chinese user-facing text in a UI text/resource location once the skeleton exists; do not scatter duplicate literal messages through services. [待校准]

## 6. SQLite and Persistence Conventions

- SQLite access must go through repository or unit-of-work style boundaries. UI code must not open database connections or issue SQL.
- All writes that affect configuration, users, alarms, linkage, backup settings, or records deletion must run in explicit transactions and produce an operation log entry where required.
- Use parameterized SQL or ORM parameter binding only.
- Runtime state may be cached in memory, but alarm records, operation logs, configuration, maintenance plans, backup settings, and license state must have persistent ownership.
- Running records and alarm records must support time-range queries and pagination. Exact schema and indexes belong in database design documents or migrations, not in this standard.
- Soft-delete versus hard-delete is a design decision to be fixed in database design; record deletion and batch clear must still be auditable. [待确认]

## 7. Device Acquisition and Protocol Conventions

- UI must never access serial ports, TCP sockets, or raw Modbus frames directly.
- Acquisition code owns channel open/close, polling loop, timeout, retry, reconnect, and offline marking.
- Protocol adapters own request construction, response validation, CRC checks, length checks, address/function checks, register decoding, and conversion to the unified device reading model.
- Business and UI layers consume only unified device readings and service/state-store outputs, not protocol-specific register offsets.
- Protocol mode is a project setting: `protocol_1` or `protocol_2`. Mixed protocol operation in one deployment is out of scope.
- CRC byte order must remain protocol-specific. Protocol 1 documents describe high byte before low byte in checked sections; Protocol 2 probe documentation describes standard Modbus low byte before high byte. Do not normalize this without real-device confirmation.
- Device debug must show raw send/receive HEX, CRC result, parse result, and clear error reason without using the debug page as a general write-control surface.

## 8. Local API Conventions

- The local HTTP API is read-only. It must not change configuration, start/stop acquisition, perform restore, activate license, trigger linkage, or control devices.
- The response envelope follows the requirement-level shape `success/code/message/data`; endpoint field details belong in a later API document.
- API implementation reads from services or read models; it must not bypass permission/data boundaries by opening ad hoc database access.
- API port, enable/disable state, and bind address are configuration-controlled. Default exposure should be local-only until the customer confirms broader access rules.

## 9. Backup, Restore, and Runtime Files

- Backup must cover SQLite database, map files, and configuration files by default.
- License artifacts are included in backup only if an explicit product rule is confirmed. [待确认]
- Restore must validate backup structure and path containment before extracting or overwriting files.
- Restore must stop acquisition and any competing scheduled backup before replacing database or runtime files.
- Failed backup/restore must leave a user-readable error, an operation log entry, and no partially trusted runtime state.

## 10. Logging and Audit Baseline

- Application logs record technical diagnostics for support.
- Operation logs record user-visible critical actions: login attempts as required, permission denial, configuration changes, user changes, record deletion/clear, backup/restore, acquisition start/stop, manual linkage, and license status changes.
- Raw device frames may be logged only in bounded diagnostic logs or debug views with size limits. Do not store unbounded frame logs in the primary operation log.
- Logs must avoid authorization codes, full machine identifiers, passwords, cryptographic secrets, database absolute paths, and internal stack traces in user-facing messages.

## 11. Packaging and Run Baseline

- Development entry point: `python3 app/main.py` after the skeleton exists. [待校准]
- Test command: `python3 -m pytest` after tests and requirements exist. [待校准]
- Packaged application must start without requiring source checkout paths.
- Packaged runtime must create missing data directories safely and must not assume write access next to the executable.
- Configuration defaults must be deterministic and documented; environment-specific production values must not be hard-coded into code.

## 12. Initial Test Baseline

Minimum test coverage expected once implementation begins:

- CRC and Modbus frame validation for each protocol mode.
- Protocol parsing for normal, alarm, fault, offline, invalid length, CRC error, address mismatch, and unknown status.
- Simulated serial/TCP acquisition paths when real devices are unavailable.
- Alarm state transitions, duplicate suppression during one alarm period, and recovery.
- Permission denial and operation logging.
- Map point ratio coordinate persistence.
- Record pagination/time filters.
- Backup/restore structure validation and stopped-acquisition restore behavior.
- Local API read-only behavior and port conflict handling.
- Packaged build smoke test on Windows.
