# Local API Conventions

## 1. Scope

This document defines conventions for the desktop application's local read-only HTTP API. It is a contract baseline, not an implementation file. The default implementation target is FastAPI hosted by the desktop application or a controlled child runtime.

## 2. Exposure Defaults

- Bind address defaults to `127.0.0.1`.
- LAN binding, API token, or IP whitelist is `[待确认]` and must not be enabled by default.
- CORS is disabled by default unless a confirmed integration requires it.
- API enable/disable, bind address, and port are configuration-controlled.
- Port conflicts must emit a desktop warning and must not crash the main desktop application.

## 3. Read-only Boundary

Only safe read endpoints are allowed. API handlers must not:

- Change configuration, users, license, maps, backup settings, or linkage rules.
- Start, stop, or restart acquisition.
- Trigger manual linkage or device control.
- Perform backup restore or license activation.
- Open ad hoc SQLite connections outside read services/repositories.

Unsupported state-changing methods return `405 Method Not Allowed` or are not registered.

## 4. URL and Versioning

Use URL versioning and lower-case plural resources:

```text
GET /api/v1/health
GET /api/v1/devices/realtime
GET /api/v1/devices/{detector_id}/realtime
GET /api/v1/alarms/active
GET /api/v1/alarms/history
GET /api/v1/controllers
GET /api/v1/detectors
```

New read-only fields or endpoints may be added to v1. Breaking response shape or semantic changes require a new version.

## 5. Response Envelope

All API responses use the requirement-level envelope:

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": {}
}
```

Error responses keep the same envelope:

```json
{
  "success": false,
  "code": 400,
  "message": "参数校验失败",
  "data": {
    "errors": [
      {"field": "page", "message": "page 必须大于等于 1"}
    ]
  }
}
```

Messages are stable, user-readable, and must not expose stack traces, SQL, absolute paths, authorization algorithms, secrets, or raw machine identifiers.

## 6. Pagination and Filters

Offset pagination is the default for records:

| Query | Type | Default | Constraint |
| --- | --- | --- | --- |
| `page` | int | 1 | `>=1` |
| `per_page` | int | 20 | `1..100` |
| `start_time` / `end_time` | ISO-8601 string | optional | bounded by endpoint policy |

Paginated `data` shape:

```json
{
  "items": [],
  "pagination": {
    "page": 1,
    "per_page": 20,
    "total": 0,
    "total_pages": 0
  }
}
```

Sort and filter fields must be whitelisted by each endpoint. User-controlled strings must never be interpolated into SQL.

## 7. Endpoint Field Baseline

### `GET /api/v1/health`

Returns API and application read status only:

```json
{
  "status": "ok",
  "api_enabled": true,
  "acquisition_status": "running"
}
```

### `GET /api/v1/devices/realtime`

Query: optional `port_id`, `controller_id`, `status`, pagination optional if device count requires it.

Each item is derived from `DeviceReading` and configuration display fields:

```json
{
  "detector_id": 1,
  "position_code": "A-001",
  "detector_name": "一号探头",
  "controller_id": 1,
  "controller_name": "一号控制器",
  "status": "normal",
  "concentration": 12.3,
  "gas_type": "可燃气",
  "unit": "%LEL",
  "alarm_level": null,
  "timestamp": "2026-01-01T10:00:00+08:00"
}
```

### `GET /api/v1/devices/{detector_id}/realtime`

Path `detector_id` must be a positive integer. `404` if detector does not exist.

### `GET /api/v1/alarms/active`

Returns active alarm records and current display data. Must not generate alarms.

### `GET /api/v1/alarms/history`

Supports time range, detector/controller filters, alarm type, status, and pagination.

### `GET /api/v1/controllers` / `GET /api/v1/detectors`

Returns configuration read models needed by integrations. Sensitive internal paths and license data are never exposed.

## 8. HTTP Status Mapping

- `200`: successful read.
- `400`: invalid query/path parameter.
- `404`: requested detector/controller not found.
- `405`: method not allowed for read-only resources.
- `409`: API service state conflict where applicable.
- `500`: unexpected server error; response message remains generic.
- `503`: API enabled but read model temporarily unavailable during restore/maintenance.

## 9. Security Headers and Runtime Controls

Default middleware sets:

- `X-Content-Type-Options: nosniff`
- `Cache-Control: no-store`
- restrictive CORS / no CORS by default

Rate limiting is `[待确认]`; if LAN exposure is enabled later, rate limits and access control become mandatory.

## 10. Trust Boundaries

API request path and query parameters are untrusted. All parameters must be parsed, bounded, and validated before service calls. API output renders database text, imported text, filenames, and device values as JSON strings only. Handlers log validation failures in technical logs where useful, but operation logs are not used for normal read requests unless security policy later requires it.