# Database Conventions

## 1. Scope

This document defines SQLite persistence conventions for the gas safety alarm desktop application. It is a schema and access baseline, not a migration implementation.

## 2. Access Rules

- UI code must not open SQLite connections or issue SQL.
- API handlers must read through services/read models and repositories only.
- All SQL uses parameter binding; user-controlled strings are never concatenated into SQL.
- Writes that affect configuration, users, license, alarms, linkage, backup settings, maintenance, or record deletion use explicit transactions.
- Each critical write records an operation log in the same transaction where practical.

## 3. Connection and Transaction Baseline

- SQLite connections are created by `app/db/connection.py`.
- Unit of Work controls commit and rollback.
- Recommended PRAGMA baseline: `foreign_keys=ON`, WAL mode where compatible, reasonable busy timeout.
- Schema migrations and restore operations are mutually exclusive with acquisition writes.

## 4. Core Tables

Initial schema groups:

| Group | Tables |
| --- | --- |
| Auth/license | `users`, optional `roles`, `license_info` |
| Device config | `ports`, `controllers`, `detectors`, `gas_types`, `system_settings` |
| Map | `maps`, `map_points` |
| Runtime/records | `realtime_snapshots`, `running_records`, `alarm_records`, `operation_logs` |
| Linkage | `linkage_objects`, `linkage_rules`, `linkage_records` |
| Maintenance | `maintenance_plans` |
| Backup | `backup_settings`, optional `backup_records` |
| Migration | `schema_migrations` |

Most tables include `created_at`, `updated_at`, and optional `deleted_at` or `is_deleted`. Soft-delete versus hard-delete remains `[待确认]`; record deletion and clear must remain auditable either way.

## 5. Key Field Conventions

- Primary keys: integer `id` unless a table has a justified natural key.
- Timestamps: ISO-8601 text with timezone or UTC text consistently across the project `[待校准]`.
- Enums: store stable lower-case strings where business readability helps (`normal`, `active`), or integer protocol raw values only in raw/debug fields.
- Money is not involved.
- Paths: store relative paths under controlled app data directories, not absolute user paths.
- User-facing text fields require length limits.

## 6. Important Constraints and Indexes

- `users.username` unique.
- Administrator uniqueness enforced by service transaction plus database constraint where SQLite version supports partial unique indexes.
- `controllers(port_id, address)` unique among active controllers.
- Detector position code uniqueness is `[待确认]`; default design treats it as unique among active detectors.
- `map_points.detector_id` unique among active points; `x_ratio` and `y_ratio` constrained to `0..1` in service and ideally CHECK constraints.
- `alarm_records` prevents duplicate active rows for the same detector/alarm type by transaction check and, where possible, partial unique index.
- Time-range indexes:
  - `running_records(detector_id, recorded_at)`
  - `alarm_records(start_time, detector_id, status)`
  - `operation_logs(created_at, user_id, action_type)`
  - `linkage_records(created_at, object_id)`

## 7. DeviceReading Persistence Mapping

`DeviceReading` is the only device state model exposed above protocol adapters. Persistence maps it as follows:

- `realtime_snapshots`: latest reading per detector, including status, concentration, unit, gas type, timestamp, protocol, source type, raw status/value summary, and quality marker.
- `running_records`: sampled valid monitoring data according to detector storage interval.
- `alarm_records`: state-machine alarm periods derived from reading transitions, not raw polling frames.

Invalid frames or failed CRC validations are not persisted as valid runtime readings. They may be counted in technical diagnostics or communication status records if introduced later.

## 8. Operation Logs

Required for:

- Permission denial.
- User and password changes.
- License activation/status changes.
- Configuration changes and imports.
- Record deletion/clear.
- Backup/restore and scheduled backup failures.
- Acquisition start/stop.
- Manual linkage and automatic linkage results.

Operation logs must not include passwords, authorization codes, raw machine identifiers, cryptographic secrets, absolute sensitive paths, or unredacted stack traces.

## 9. Backup and Restore Impact

- Backup covers SQLite database, config files, and map files by default.
- Restore validates backup manifest and schema compatibility before replacing files.
- Restore must stop acquisition and scheduled backups before database replacement.
- A pre-restore safety backup should be created where feasible.
- License artifacts are excluded unless product rules explicitly include them `[待确认]`.

## 10. Query and Pagination Conventions

- List endpoints and pages use `page` and `per_page` with upper bound 100.
- Time-range queries should require start/end or impose a safe maximum span for large tables.
- Sort fields are whitelisted in repository methods.
- Export operations should use the same filters as query pages and enforce maximum row limits `[待确认]`.

## 11. Migration Conventions

- Migrations are versioned and idempotent where practical.
- Migration failure aborts startup into a controlled error state and writes technical logs.
- Schema migrations are not run while acquisition or restore is active.
- Seed data for initial administrator is `[待确认]`; do not commit production default secrets.