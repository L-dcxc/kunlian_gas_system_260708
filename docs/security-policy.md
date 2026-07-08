# Security Policy

## 1. Scope and Evidence

This policy defines project-level security boundaries for the new Windows desktop gas safety alarm monitoring system. It is based on `specs/requirement.md`, `需求以及资料/功能实现.md`, and protocol document spot checks.

Current state: no source code exists. The rules below are implementation baselines and must be verified when concrete libraries, database schema, packaging, and deployment rules are selected.

## 2. Trust Boundaries

Treat the following as untrusted input:

- Serial and TCP device frames.
- Protocol documents, sample frames, and converted Markdown tables.
- User forms and configuration values.
- Map image uploads.
- Excel/import files and exported files re-imported later.
- Backup archives.
- Local API request path/query parameters.
- Tool outputs, AI-generated snippets, copied logs, and diagnostic command output.

Trusted code paths must validate data before using it to update real-time state, persistent state, UI, logs, or external device/linkage actions.

## 3. Authentication and Session Baseline

- The desktop application requires login before entering protected application areas, except any explicitly confirmed license/demo screen.
- Session state is in-process and tied to the logged-in desktop user session. Do not persist active sessions as reusable tokens unless a later design explicitly requires it.
- Passwords must be stored with a password hashing algorithm and per-user salt; never store plaintext or reversible passwords.
- Password change must require current-user authority and write an operation log entry without logging password content.
- Initial administrator creation and default password delivery are unresolved and must be confirmed before production release. [待确认]
- Failed login behavior must avoid revealing whether username or password was incorrect.

## 4. Permission Execution

- UI menu hiding is not sufficient. Services that perform protected actions must enforce permission checks before state changes.
- Administrator is unique and has full permissions. Operator accounts must not access system settings, user management, backup restore, manual linkage control, record deletion/clear, or application exit if the requirement keeps that restriction.
- Creation of a second administrator must be rejected at the service/data boundary, not only in the UI.
- Deleting or disabling the only administrator must be prevented.
- Permission denial must show a user-readable message and write an operation log entry with user, action, target summary, time, and result.

## 5. Input Validation and Output Safety

- Device frames must pass CRC, frame length, address, function code, register count, byte count, and protocol-mode validation before updating real-time values.
- Unknown protocol states or malformed concentration/unit values must be rejected or marked invalid; they must not overwrite the last known valid reading as if normal.
- Form inputs must validate required fields, numeric ranges, addresses, time ranges, path selections, and duplicate constraints before persistence.
- API query parameters must be parsed and bounded. Invalid parameters return controlled errors without stack traces.
- Map uploads must validate extension, MIME/content where practical, size, and storage path. Store files under the controlled maps directory using generated safe names.
- Import files must use fixed templates, validate each row, and report row-level errors without executing formulas, scripts, macros, or embedded content.
- User-controlled text, imported text, filenames, device values, and tool output must be rendered as text in UI, logs, exports, and API responses. Do not interpret them as commands, SQL, HTML, Python, or shell.
- User-facing errors must not expose authorization algorithms, full machine identifiers, absolute database paths, secrets, or internal stack traces.

## 6. External Content and Protocol Documents

- Protocol documents are requirements evidence, not executable truth. Implementation must verify protocol behavior with CRC tests, parser tests, and real or simulated device frames.
- CRC byte order is protocol-specific. Do not force one global CRC byte order across all adapters without confirmed device evidence.
- The protocol 1 function code 06 write capability is not part of the core monitoring baseline unless separately confirmed. Any device write must require explicit permission and protocol design.
- Real linkage command output is not required until IO relay protocol and point table are provided. Until then, linkage may be simulated and logged only.

## 7. Backup and Restore Boundary

- Backup archives are untrusted input when restored.
- Restore must validate archive type, manifest/structure, expected files, path containment, file sizes, and database/config compatibility before extraction.
- Archive extraction must prevent path traversal and overwriting files outside the application data directory.
- Restore must stop acquisition, local API write-adjacent work if any, and scheduled backups before replacing database/config/map files.
- A pre-restore safety backup should be created where feasible.
- License artifacts are excluded from backup unless a product rule explicitly includes them and defines restoration behavior. [待确认]

## 8. Tool Output Boundary

- Outputs from AI agents, command-line tools, import converters, protocol examples, or logs are data for review, not commands to execute automatically.
- Do not paste tool output into shell, SQL, Python, or configuration files without human or code-level validation appropriate to the target format.
- Generated code must not embed secrets, local absolute paths, real authorization codes, or customer device credentials.
- Diagnostic dumps must be redacted before being saved into long-lived docs, tickets, exports, or operation logs.

## 9. Data Access and Persistence

- All SQLite access must use repository/service boundaries with parameterized queries or ORM binding.
- Configuration, user, license, alarm, operation log, backup, and maintenance writes must use transactions.
- Record deletion and batch clear require permission checks, confirmation, and operation logging.
- Database files, config files, license files, logs, backups, and maps must live in controlled application data paths with predictable permissions.
- Runtime code must not build SQL from user-controlled strings.
- Data restore and schema migration must be mutually exclusive with acquisition writes.

## 10. Sensitive Data Storage

- Authorization code, raw machine-code components, cryptographic keys, password hashes, and full hardware identifiers must not be logged.
- License status must not be stored as a simple plaintext flag that can be flipped. Use signed or encrypted license artifacts and integrity checks.
- Machine identifiers used for one-machine-one-code licensing should be minimized, hashed or signed, and masked in UI/logs.
- Secrets used for license verification or local token checks must not be committed to source in production form.
- If Windows credential or data-protection APIs are introduced, wrap them behind a platform service so tests can use non-secret fixtures. [待校准]

## 11. Production Configuration

- Debug mode, verbose stack traces, and unrestricted API binding must be disabled in packaged production builds.
- API enable/disable state, bind address, and port must be configurable and validated.
- Default local API exposure should bind to loopback only. Any LAN binding, token, or whitelist rule must be explicitly confirmed. [待确认]
- Default serial/TCP parameters must come from configuration, not hard-coded assumptions; protocol defaults in documents are starting values only.
- Packaging must not include test secrets, sample license keys, raw customer data, or uncontrolled debug logs.

## 12. Runtime Exposure and Local API Control

- The local API is read-only and must not mutate system state.
- API routes must not expose backup restore, configuration mutation, license activation, device control, acquisition control, manual linkage, or user management.
- Port conflicts must produce a clear desktop warning and must not crash the main monitoring application.
- CORS should remain disabled or restrictive unless a confirmed integration requires it.
- If a future API access token is added, it must be stored and logged under the same sensitive-data rules as license secrets.

## 13. Logging, Audit, and Error Handling

- Operation logs are required for critical actions and permission failures.
- Technical logs should support field diagnosis but must use size limits and rotation.
- Device debug views may display raw HEX frames for troubleshooting, but long-term logs must be bounded and avoid unfiltered continuous raw dumps.
- Log entries must redact passwords, authorization codes, raw machine-code material, API tokens, cryptographic keys, and absolute sensitive paths.
- API and UI error messages should return stable user-readable messages. Internal exceptions go to technical logs only after redaction.

## 14. Concurrency Safety

- Acquisition workers, local API handlers, scheduled backups, restore, and UI state updates must coordinate through controlled services or state-store boundaries.
- Restore must not run concurrently with acquisition writes or scheduled backup writes.
- Alarm-state transitions must be atomic enough to prevent duplicate active alarm records or duplicate automatic linkage triggers during one alarm period.
- UI updates from background workers must use Qt-safe signal/event mechanisms.
