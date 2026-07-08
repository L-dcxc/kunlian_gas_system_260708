from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from app.core.logging import get_logger
from app.core.state_store import StateStore
from app.core.workers import CancellationToken
from app.db.connection import Database
from app.db.repositories.runtime_repository import RealtimeSnapshotRepository, RunningRecordRepository
from app.db.unit_of_work import UnitOfWork
from app.device.channels.base import Channel, ChannelConfig, ChannelError, ChannelErrorCode, TransactResult
from app.device.channels.serial_channel import SerialChannel
from app.device.channels.tcp_channel import TcpChannel
from app.device.protocols.base import PollBuildContext, PollRequest, PollTarget, ProtocolAdapter
from app.services.alarm_service import AlarmService
from app.services.models import DeviceReading, DeviceSourceType, DeviceStatus, ProtocolMode

ChannelFactory = Callable[[ChannelConfig], Channel]


@dataclass(frozen=True, slots=True)
class PollingTarget:
    detector_id: int
    detector_address: int | None = None
    controller_id: int | None = None
    controller_address: int | None = None
    store_interval_sec: int = 60

    def to_poll_target(self) -> PollTarget:
        return PollTarget(
            detector_id=self.detector_id,
            detector_address=self.detector_address,
            controller_id=self.controller_id,
            controller_address=self.controller_address,
        )


@dataclass(frozen=True, slots=True)
class PortTargetGroup:
    source_type: DeviceSourceType
    targets: tuple[PollingTarget, ...]


@dataclass(frozen=True, slots=True)
class PortPollingConfig:
    port_id: int
    protocol: ProtocolMode
    channel_config: ChannelConfig
    target_groups: tuple[PortTargetGroup, ...]
    poll_interval_ms: int = 1000
    timeout_ms: int = 1500
    failure_threshold: int = 3
    reconnect_interval_ms: int = 3000
    labels: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PortWorkerSnapshot:
    port_id: int
    opened: bool
    consecutive_failures: int
    offline_detector_ids: tuple[int, ...]
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class PollOnceResult:
    valid_readings: tuple[DeviceReading, ...]
    offline_readings: tuple[DeviceReading, ...]
    invalid_response_count: int = 0
    channel_failure_count: int = 0


class PortWorker:
    def __init__(
        self,
        *,
        config: PortPollingConfig,
        adapter: ProtocolAdapter,
        database: Database,
        state_store: StateStore,
        alarm_service: AlarmService,
        channel_factory: ChannelFactory | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._adapter = adapter
        self._database = database
        self._state_store = state_store
        self._alarm_service = alarm_service
        self._channel_factory = channel_factory or _default_channel_factory
        self._sleep = sleep
        self._channel: Channel | None = None
        self._opened = False
        self._consecutive_failures = 0
        self._failures_by_detector: dict[int, int] = {}
        self._offline_detector_ids: set[int] = set()
        self._last_record_monotonic: dict[int, float] = {}
        self._last_error = ""
        self._logger = get_logger("device.port_worker")
        self._store_intervals = {
            target.detector_id: max(1, target.store_interval_sec)
            for group in config.target_groups
            for target in group.targets
        }
        self._targets = {target.detector_id: target for group in config.target_groups for target in group.targets}

    def run(self, token: CancellationToken) -> PortWorkerSnapshot:
        try:
            self.open()
            while not token.is_cancelled:
                self.poll_once()
                self._sleep(self.config.poll_interval_ms / 1000)
        finally:
            self.close()
        return self.snapshot()

    def open(self) -> None:
        if self._opened:
            return
        self._channel = self._channel_factory(self.config.channel_config)
        try:
            self._channel.open()
        except ChannelError as exc:
            self._record_port_failure(exc.message)
            raise
        self._opened = True
        self._consecutive_failures = 0
        self._last_error = ""

    def close(self) -> None:
        channel = self._channel
        self._channel = None
        self._opened = False
        if channel is None:
            return
        try:
            channel.close()
        except ChannelError as exc:
            self._logger.warning("port close failed port_id=%s error=%s", self.config.port_id, exc.code.value)

    def poll_once(self) -> PollOnceResult:
        if not self._opened:
            self.open()
        channel = self._channel
        if channel is None:
            raise RuntimeError("channel is not open")

        valid_readings: list[DeviceReading] = []
        offline_readings: list[DeviceReading] = []
        invalid_count = 0
        failure_count = 0
        for request in self._build_requests():
            result = channel.transact(request.payload, request.timeout_ms)
            if not result.ok:
                failure_count += 1
                offline = self._handle_channel_failure(request, result)
                if offline is not None:
                    offline_readings.append(offline)
                continue
            validation = self._adapter.validate_response(request, result.payload)
            if not validation.ok:
                invalid_count += 1
                self._last_error = validation.message
                self._logger.warning(
                    "invalid device response port_id=%s detector_id=%s code=%s",
                    self.config.port_id,
                    request.detector_id,
                    validation.error_code.value if validation.error_code else "unknown",
                )
                continue
            readings = tuple(reading for reading in self._adapter.parse_response(request, result.payload) if reading.status is not DeviceStatus.INVALID)
            if not readings:
                invalid_count += 1
                continue
            self._record_success(readings)
            valid_readings.extend(readings)

        if valid_readings:
            self._persist_and_publish(valid_readings, quality="valid")
        if offline_readings:
            self._persist_and_publish(offline_readings, quality="offline")
        return PollOnceResult(tuple(valid_readings), tuple(offline_readings), invalid_count, failure_count)

    def snapshot(self) -> PortWorkerSnapshot:
        return PortWorkerSnapshot(
            port_id=self.config.port_id,
            opened=self._opened,
            consecutive_failures=self._consecutive_failures,
            offline_detector_ids=tuple(sorted(self._offline_detector_ids)),
            last_error=self._last_error,
        )

    def _build_requests(self) -> list[PollRequest]:
        requests: list[PollRequest] = []
        for group in self.config.target_groups:
            context = PollBuildContext(
                protocol=self.config.protocol,
                source_type=group.source_type,
                port_id=self.config.port_id,
                targets=tuple(target.to_poll_target() for target in group.targets),
                default_timeout_ms=self.config.timeout_ms,
                labels=self.config.labels,
            )
            requests.extend(self._adapter.build_poll_requests(context))
        return requests

    def _handle_channel_failure(self, request: PollRequest, result: TransactResult) -> DeviceReading | None:
        self._consecutive_failures += 1
        detector_id = request.detector_id
        if detector_id is not None:
            self._failures_by_detector[detector_id] = self._failures_by_detector.get(detector_id, 0) + 1
        self._last_error = result.message
        if result.error_code in {ChannelErrorCode.NOT_OPEN, ChannelErrorCode.CONNECTION_FAILED, ChannelErrorCode.IO_ERROR}:
            self._reopen_after_failure()
        if detector_id is None or self._failures_by_detector.get(detector_id, 0) < self.config.failure_threshold:
            return None
        return self._offline_reading_for(request)

    def _reopen_after_failure(self) -> None:
        # Reopen is local to this worker so one broken port cannot pause polling
        # on another port managed by a different worker.
        self.close()
        self._sleep(self.config.reconnect_interval_ms / 1000)
        try:
            self.open()
        except ChannelError as exc:
            self._record_port_failure(exc.message)

    def _record_port_failure(self, message: str) -> None:
        self._consecutive_failures += 1
        self._last_error = message

    def _record_success(self, readings: tuple[DeviceReading, ...]) -> None:
        self._consecutive_failures = 0
        self._last_error = ""
        for reading in readings:
            self._failures_by_detector[reading.detector_id] = 0
            self._offline_detector_ids.discard(reading.detector_id)

    def _offline_reading_for(self, request: PollRequest) -> DeviceReading:
        detector_id = request.detector_id or request.unit_address
        self._offline_detector_ids.add(detector_id)
        target = self._targets.get(detector_id)
        return DeviceReading(
            protocol=request.protocol,
            source_type=request.source_type,
            port_id=request.port_id,
            controller_id=request.controller_id,
            detector_id=detector_id,
            controller_address=target.controller_address if target is not None else None,
            detector_address=target.detector_address if target is not None else request.unit_address,
            status=DeviceStatus.OFFLINE,
            concentration=None,
            gas_type=None,
            unit=None,
            alarm_level=None,
            raw_status="offline",
            raw_value=None,
            timestamp=datetime.now(timezone.utc),
        )

    def _persist_and_publish(self, readings: list[DeviceReading], *, quality: str) -> None:
        now_monotonic = time.monotonic()
        with UnitOfWork(self._database) as uow:
            snapshots = RealtimeSnapshotRepository(uow)
            records = RunningRecordRepository(uow)
            for reading in readings:
                snapshots.upsert_reading(reading, quality=quality)
                interval = self._store_intervals.get(reading.detector_id, 60)
                last_recorded = self._last_record_monotonic.get(reading.detector_id)
                if last_recorded is None or now_monotonic - last_recorded >= interval:
                    records.add_reading(reading, quality=quality)
                    self._last_record_monotonic[reading.detector_id] = now_monotonic
            uow.commit()
        self._state_store.update_readings(readings)
        self._alarm_service.ingest_readings(readings)


def _default_channel_factory(config: ChannelConfig) -> Channel:
    if config.channel_type.value == "serial":
        return SerialChannel(config)
    return TcpChannel(config)
