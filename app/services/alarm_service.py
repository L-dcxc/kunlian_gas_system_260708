from __future__ import annotations

from dataclasses import dataclass, field

from app.db.connection import Database
from app.db.repositories.alarm_repository import AlarmRepository
from app.db.unit_of_work import UnitOfWork
from app.services.linkage_service import LinkageService
from app.services.models import DeviceReading, DeviceStatus

ALARM_STATUS_TYPES: dict[DeviceStatus, str] = {
    DeviceStatus.ALARM_LOW: "alarm_low",
    DeviceStatus.ALARM_HIGH: "alarm_high",
    DeviceStatus.OVER_RANGE: "over_range",
    DeviceStatus.FAULT: "fault",
    DeviceStatus.OFFLINE: "offline",
    DeviceStatus.DISABLED: "disabled",
    DeviceStatus.WARMING: "warming",
}


@dataclass(frozen=True, slots=True)
class AlarmTransition:
    detector_id: int
    alarm_type: str
    alarm_record_id: int
    created: bool = False
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class AlarmProcessResult:
    created: tuple[AlarmTransition, ...] = field(default_factory=tuple)
    recovered: tuple[AlarmTransition, ...] = field(default_factory=tuple)
    linkage_record_ids: tuple[int, ...] = field(default_factory=tuple)
    ignored_invalid_count: int = 0


class AlarmService:
    def __init__(self, database: Database, linkage_service: LinkageService | None = None) -> None:
        self._database = database
        self._linkage_service = linkage_service or LinkageService(database)

    def ingest_readings(self, readings: list[DeviceReading] | tuple[DeviceReading, ...]) -> AlarmProcessResult:
        created: list[AlarmTransition] = []
        recovered: list[AlarmTransition] = []
        linkage_record_ids: list[int] = []
        ignored_invalid = 0
        with UnitOfWork(self._database) as uow:
            alarms = AlarmRepository(uow)
            for reading in readings:
                if reading.status is DeviceStatus.INVALID:
                    ignored_invalid += 1
                    continue
                alarm_type = ALARM_STATUS_TYPES.get(reading.status)
                active_rows = alarms.active_for_detector(reading.detector_id)
                if alarm_type is None:
                    for row in active_rows:
                        if alarms.recover_active(reading.detector_id, str(row["alarm_type"]), reading.timestamp.isoformat()):
                            recovered.append(
                                AlarmTransition(
                                    detector_id=reading.detector_id,
                                    alarm_type=str(row["alarm_type"]),
                                    alarm_record_id=int(row["id"]),
                                    recovered=True,
                                )
                            )
                    continue

                for row in active_rows:
                    if str(row["alarm_type"]) == alarm_type:
                        continue
                    if alarms.recover_active(reading.detector_id, str(row["alarm_type"]), reading.timestamp.isoformat()):
                        recovered.append(
                            AlarmTransition(
                                detector_id=reading.detector_id,
                                alarm_type=str(row["alarm_type"]),
                                alarm_record_id=int(row["id"]),
                                recovered=True,
                            )
                        )

                # Creation and linkage trigger share this transaction so the
                # unique active alarm row is also the idempotency key for linkage.
                alarm_id, was_created = alarms.create_active(
                    detector_id=reading.detector_id,
                    alarm_type=alarm_type,
                    alarm_level=reading.alarm_level,
                    trigger_value=reading.concentration,
                    start_time=reading.timestamp.isoformat(),
                )
                if was_created:
                    transition = AlarmTransition(
                        detector_id=reading.detector_id,
                        alarm_type=alarm_type,
                        alarm_record_id=alarm_id,
                        created=True,
                    )
                    created.append(transition)
                    alarm_row = alarms.find_by_id(alarm_id)
                    linkage_record_ids.extend(self._linkage_service.trigger_for_alarm(uow, alarm_row=alarm_row, reading=reading))
            uow.commit()
        return AlarmProcessResult(
            created=tuple(created),
            recovered=tuple(recovered),
            linkage_record_ids=tuple(linkage_record_ids),
            ignored_invalid_count=ignored_invalid,
        )
