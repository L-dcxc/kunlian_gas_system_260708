from __future__ import annotations

from app.db.repositories.alarm_repository import AlarmRepository
from app.db.repositories.backup_repository import BackupRecordRepository, BackupSettingsRepository
from app.db.repositories.base import BaseRepository, EntityRepository, Pagination, order_by_clause, validate_pagination
from app.db.repositories.device_config_repository import (
    ControllerRepository,
    DetectorRepository,
    GasTypeRepository,
    PortRepository,
)
from app.db.repositories.linkage_repository import (
    LinkageObjectRepository,
    LinkageRecordRepository,
    LinkageRuleRepository,
)
from app.db.repositories.license_repository import LicenseRepository
from app.db.repositories.maintenance_repository import MaintenanceRepository
from app.db.repositories.map_repository import MapPointRepository, MapRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.record_repository import RecordRepository
from app.db.repositories.runtime_repository import RealtimeSnapshotRepository, RunningRecordRepository
from app.db.repositories.settings_repository import SettingsRepository
from app.db.repositories.user_repository import UserRepository

__all__ = [
    "AlarmRepository",
    "BackupRecordRepository",
    "BackupSettingsRepository",
    "BaseRepository",
    "ControllerRepository",
    "DetectorRepository",
    "EntityRepository",
    "GasTypeRepository",
    "LinkageObjectRepository",
    "LinkageRecordRepository",
    "LinkageRuleRepository",
    "LicenseRepository",
    "MaintenanceRepository",
    "MapPointRepository",
    "MapRepository",
    "OperationLogRepository",
    "Pagination",
    "PortRepository",
    "RealtimeSnapshotRepository",
    "RecordRepository",
    "RunningRecordRepository",
    "SettingsRepository",
    "UserRepository",
    "order_by_clause",
    "validate_pagination",
]
