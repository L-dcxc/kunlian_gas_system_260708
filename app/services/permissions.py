from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"


class Permission(StrEnum):
    MONITOR_VIEW = "monitor.view"
    RECORD_VIEW = "records.view"
    CHART_VIEW = "charts.view"
    MAP_VIEW = "maps.view"
    DEVICE_DEBUG_VIEW = "device_debug.view"
    MAINTENANCE_VIEW = "maintenance.view"
    MAINTENANCE_MANAGE = "maintenance.manage"
    PASSWORD_CHANGE = "password.change"
    LICENSE_ACTIVATE = "license.activate"
    SYSTEM_SETTINGS = "system.settings"
    USER_MANAGE = "users.manage"
    BACKUP_RESTORE = "backup.restore"
    LINKAGE_MANUAL_CONTROL = "linkage.manual_control"
    RECORD_DELETE = "records.delete"
    RECORD_CLEAR = "records.clear"
    APP_EXIT = "app.exit"


class SensitiveAction(StrEnum):
    LICENSE_ACTIVATE = "license.activate"
    PASSWORD_CHANGE = "password.change"
    USER_CREATE = "users.create"
    USER_UPDATE = "users.update"
    USER_DISABLE = "users.disable"
    USER_DELETE = "users.delete"
    USER_MANAGE = Permission.USER_MANAGE.value
    SYSTEM_SETTINGS = "system.settings"
    BACKUP_RESTORE = "backup.restore"
    LINKAGE_MANUAL_CONTROL = "linkage.manual_control"
    RECORD_DELETE = "records.delete"
    RECORD_CLEAR = "records.clear"
    APP_EXIT = "app.exit"


ADMIN_PERMISSIONS = frozenset({"*"})
OPERATOR_PERMISSIONS = frozenset(
    {
        Permission.MONITOR_VIEW.value,
        Permission.RECORD_VIEW.value,
        Permission.CHART_VIEW.value,
        Permission.MAP_VIEW.value,
        Permission.DEVICE_DEBUG_VIEW.value,
        Permission.MAINTENANCE_VIEW.value,
        Permission.PASSWORD_CHANGE.value,
    }
)
RESTRICTED_OPERATOR_ACTIONS = frozenset(
    {
        Permission.SYSTEM_SETTINGS.value,
        Permission.USER_MANAGE.value,
        Permission.BACKUP_RESTORE.value,
        Permission.LINKAGE_MANUAL_CONTROL.value,
        Permission.RECORD_DELETE.value,
        Permission.RECORD_CLEAR.value,
        Permission.APP_EXIT.value,
        Permission.MAINTENANCE_MANAGE.value,
        SensitiveAction.USER_CREATE.value,
        SensitiveAction.USER_UPDATE.value,
        SensitiveAction.USER_DISABLE.value,
        SensitiveAction.USER_DELETE.value,
    }
)


def normalize_role(role: str | Role) -> str:
    try:
        return Role(role).value
    except ValueError as exc:
        raise ValueError("unsupported role") from exc


def permissions_for_role(role: str | Role) -> tuple[str, ...]:
    normalized = normalize_role(role)
    if normalized == Role.ADMIN.value:
        return tuple(sorted(ADMIN_PERMISSIONS))
    if normalized == Role.OPERATOR.value:
        return tuple(sorted(OPERATOR_PERMISSIONS))
    raise ValueError("unsupported role")


def role_has_permission(role: str | Role, action: str) -> bool:
    normalized_action = normalize_permission_code(action)
    permissions = set(permissions_for_role(role))
    return "*" in permissions or normalized_action in permissions


def normalize_permission_code(action: str | Permission | SensitiveAction) -> str:
    value = str(action.value if isinstance(action, Permission | SensitiveAction) else action)
    if not value or len(value) > 80:
        raise ValueError("unsupported permission code")
    if not value.replace("_", "").replace(":", "").replace(".", "").replace("-", "").isalnum():
        raise ValueError("unsupported permission code")
    return value


def should_increment_permission_version(*, role_changed: bool = False, active_changed: bool = False) -> bool:
    # Sessions carry the permission_version observed at login. Role and active
    # state changes alter authorization decisions, so old sessions must fail a
    # version check instead of relying on hidden UI menu state.
    return role_changed or active_changed
