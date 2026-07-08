from __future__ import annotations

import concurrent.futures
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.runtime_locks import RuntimeLockError, RuntimeLockManager
from app.core.state_store import StateStore
from app.core.workers import WorkerError, WorkerHandle, WorkerPool
from app.db.connection import Database
from app.db.repositories.device_config_repository import ControllerRepository, DetectorRepository, PortRepository
from app.db.repositories.operation_log_repository import OperationLogRepository
from app.db.repositories.settings_repository import SettingsRepository
from app.db.unit_of_work import UnitOfWork
from app.device.channels.base import Channel, ChannelConfig, ChannelType, Parity, SerialParameters, TcpParameters
from app.device.polling.port_worker import ChannelFactory, PollingTarget, PortPollingConfig, PortTargetGroup, PortWorker
from app.device.protocols.factory import create_protocol_adapter
from app.services.alarm_service import AlarmService
from app.services.auth_service import Session, SessionStore
from app.services.errors import ErrorCode
from app.services.models import AcquisitionState, AcquisitionStatus, DeviceSourceType, ProtocolMode, ServiceResult
from app.services.permissions import Permission

ACQUISITION_STATUS_STATE_KEY = "acquisition.status"


@dataclass(frozen=True, slots=True)
class AcquisitionServiceOptions:
    stop_timeout_sec: float = 5.0


class AcquisitionService:
    """Application-internal acquisition lifecycle service.

    UI/view models may call this service after permission checks; local API routes
    must keep their read-only boundary and must not expose start/stop/restart.
    """

    def __init__(
        self,
        *,
        database: Database,
        session_store: SessionStore,
        state_store: StateStore,
        worker_pool: WorkerPool,
        runtime_locks: RuntimeLockManager,
        alarm_service: AlarmService | None = None,
        channel_factory: ChannelFactory | None = None,
        worker_factory: Callable[..., PortWorker] | None = None,
        options: AcquisitionServiceOptions | None = None,
    ) -> None:
        self._database = database
        self._session_store = session_store
        self._state_store = state_store
        self._worker_pool = worker_pool
        self._runtime_locks = runtime_locks
        self._alarm_service = alarm_service or AlarmService(database)
        self._channel_factory = channel_factory
        self._worker_factory = worker_factory or PortWorker
        self._options = options or AcquisitionServiceOptions()
        self._lock = threading.RLock()
        self._status = AcquisitionState(AcquisitionStatus.NOT_STARTED)
        self._handles: dict[int, WorkerHandle] = {}
        self._workers: dict[int, PortWorker] = {}
        self._lock_acquired = False
        self._state_store.set_value(ACQUISITION_STATUS_STATE_KEY, self._status)

    def start(self, session_or_id: Session | str) -> ServiceResult[AcquisitionState]:
        actor = self._require_permission(session_or_id, "启动采集")
        if isinstance(actor, ServiceResult):
            return actor
        with self._lock:
            if self._is_running_locked():
                return ServiceResult.ok(self.get_status(), message="采集已在运行")
            try:
                self._runtime_locks.acquire_operation("acquisition", timeout=0)
                self._lock_acquired = True
            except RuntimeLockError as exc:
                self._set_status(AcquisitionStatus.ERROR, str(exc), ())
                return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message=str(exc))
            try:
                configs = self._load_polling_configs()
                if not configs:
                    self._release_runtime_lock_locked()
                    self._set_status(AcquisitionStatus.STOPPED, "无启用采集端口", ())
                    return ServiceResult.fail(code=int(ErrorCode.CONFLICT), message="无启用采集端口")
                self._handles.clear()
                self._workers.clear()
                for config in configs:
                    adapter = create_protocol_adapter(config.protocol)
                    worker = self._worker_factory(
                        config=config,
                        adapter=adapter,
                        database=self._database,
                        state_store=self._state_store,
                        alarm_service=self._alarm_service,
                        channel_factory=self._channel_factory,
                    )
                    self._workers[config.port_id] = worker
                    handle = self._worker_pool.submit(
                        f"acquisition-port-{config.port_id}",
                        worker.run,
                        on_error=self._worker_failed,
                    )
                    self._handles[config.port_id] = handle
                self._set_status(AcquisitionStatus.RUNNING, "采集已启动", tuple(sorted(self._handles)))
                self._add_operation_log(actor, "acquisition.start", "启动采集。", {"ports": tuple(sorted(self._handles))})
                return ServiceResult.ok(self._status)
            except Exception as exc:
                self._cancel_workers_locked()
                self._release_runtime_lock_locked()
                self._set_status(AcquisitionStatus.ERROR, "采集启动失败", ())
                return ServiceResult.fail(code=int(ErrorCode.INTERNAL_ERROR), message="采集启动失败")

    def stop(self, session_or_id: Session | str) -> ServiceResult[AcquisitionState]:
        actor = self._require_permission(session_or_id, "停止采集")
        if isinstance(actor, ServiceResult):
            return actor
        with self._lock:
            if not self._is_running_locked():
                self._set_status(AcquisitionStatus.STOPPED, "采集已停止", ())
                return ServiceResult.ok(self._status, message="采集已停止")
            self._cancel_workers_locked()
            self._release_runtime_lock_locked()
            self._set_status(AcquisitionStatus.STOPPED, "采集已停止", ())
            self._add_operation_log(actor, "acquisition.stop", "停止采集。")
            return ServiceResult.ok(self._status)

    def restart(self, session_or_id: Session | str) -> ServiceResult[AcquisitionState]:
        stop_result = self.stop(session_or_id)
        if not stop_result.success:
            return stop_result
        start_result = self.start(session_or_id)
        if start_result.success:
            actor = self._resolve_actor(session_or_id)
            if actor is not None:
                self._add_operation_log(actor, "acquisition.restart", "重启采集。")
        return start_result

    def get_status(self) -> AcquisitionState:
        with self._lock:
            if self._status.status is AcquisitionStatus.RUNNING:
                snapshots = tuple(worker.snapshot() for worker in self._workers.values())
                if any(item.consecutive_failures > 0 for item in snapshots):
                    return AcquisitionState(
                        AcquisitionStatus.RECONNECTING,
                        "部分端口正在重连",
                        self._status.active_port_ids,
                        datetime.now(timezone.utc),
                    )
            return self._status

    def _load_polling_configs(self) -> tuple[PortPollingConfig, ...]:
        with UnitOfWork(self._database) as uow:
            mode = ProtocolMode(SettingsRepository(uow).get_value("protocol_mode", ProtocolMode.PROTOCOL_1.value))
            ports = [row for row in PortRepository(uow).list_active() if int(row["is_enabled"]) == 1]
            controllers = ControllerRepository(uow).list_active()
            detectors = [row for row in DetectorRepository(uow).list_active() if int(row["is_enabled"]) == 1]
            uow.commit()
        controllers_by_id = {int(row["id"]): row for row in controllers if int(row["is_enabled"]) == 1}
        targets_by_port_source: dict[tuple[int, DeviceSourceType], list[PollingTarget]] = {}
        for detector in detectors:
            port_id = int(detector["port_id"])
            controller_id = detector["controller_id"]
            if controller_id is None:
                source_type = DeviceSourceType.PROBE
                controller_address = None
            else:
                controller = controllers_by_id.get(int(controller_id))
                if controller is None:
                    continue
                source_type = DeviceSourceType.CONTROLLER
                controller_address = int(controller["address"])
            targets_by_port_source.setdefault((port_id, source_type), []).append(
                PollingTarget(
                    detector_id=int(detector["id"]),
                    detector_address=int(detector["protocol_address"]),
                    controller_id=None if controller_id is None else int(controller_id),
                    controller_address=controller_address,
                    store_interval_sec=int(detector["store_interval_sec"]),
                )
            )
        configs: list[PortPollingConfig] = []
        for port in ports:
            port_id = int(port["id"])
            groups = tuple(
                PortTargetGroup(source_type=source, targets=tuple(targets))
                for (group_port_id, source), targets in targets_by_port_source.items()
                if group_port_id == port_id and targets
            )
            if not groups:
                continue
            configs.append(
                PortPollingConfig(
                    port_id=port_id,
                    protocol=mode,
                    channel_config=_channel_config_from_port(port),
                    target_groups=groups,
                    poll_interval_ms=int(port["poll_interval_ms"]),
                    timeout_ms=int(port["timeout_ms"]),
                    failure_threshold=int(port["failure_threshold"]),
                    reconnect_interval_ms=int(port["reconnect_interval_ms"]),
                    labels=(str(port["name"]),),
                )
            )
        return tuple(configs)

    def _worker_failed(self, error: WorkerError) -> None:
        with self._lock:
            if self._status.status in {AcquisitionStatus.RUNNING, AcquisitionStatus.RECONNECTING}:
                self._set_status(AcquisitionStatus.ERROR, error.message, tuple(sorted(self._handles)))

    def _is_running_locked(self) -> bool:
        return self._status.status in {AcquisitionStatus.RUNNING, AcquisitionStatus.RECONNECTING} and bool(self._handles)

    def _cancel_workers_locked(self) -> None:
        handles = tuple(self._handles.values())
        self._handles.clear()
        self._workers.clear()
        for handle in handles:
            handle.cancel()
        for handle in handles:
            try:
                handle.future.result(timeout=self._options.stop_timeout_sec)
            except (concurrent.futures.CancelledError, TimeoutError):
                continue
            except Exception:
                continue

    def _release_runtime_lock_locked(self) -> None:
        if not self._lock_acquired:
            return
        self._runtime_locks.release_operation("acquisition")
        self._lock_acquired = False

    def _set_status(self, status: AcquisitionStatus, message: str = "", active_port_ids: tuple[int, ...] = ()) -> None:
        self._status = AcquisitionState(status, message, active_port_ids, datetime.now(timezone.utc))
        self._state_store.set_value(ACQUISITION_STATUS_STATE_KEY, self._status)

    def _require_permission(self, session_or_id: Session | str, target_summary: str) -> Session | ServiceResult:
        try:
            return self._session_store.require_permission(
                self._database,
                session_or_id,
                Permission.SYSTEM_SETTINGS.value,
                target_summary,
            )
        except Exception as exc:
            return ServiceResult.fail(code=int(ErrorCode.PERMISSION_DENIED), message=str(exc))

    def _resolve_actor(self, session_or_id: Session | str) -> Session | None:
        try:
            return self._session_store.validate(self._database, session_or_id)
        except Exception:
            return None

    def _add_operation_log(
        self,
        actor: Session,
        action_type: str,
        summary: str,
        details: dict[str, object] | None = None,
    ) -> None:
        try:
            with UnitOfWork(self._database) as uow:
                OperationLogRepository(uow).add(
                    action_type=action_type,
                    result="success",
                    actor_id=actor.user_id,
                    actor_name=actor.username,
                    target_type="acquisition",
                    target_id=None,
                    summary=summary,
                    details=details or {},
                )
                uow.commit()
        except sqlite3.Error:
            # Lifecycle state must remain idempotent even if audit storage is temporarily busy.
            pass


def _channel_config_from_port(row) -> ChannelConfig:
    port_id = int(row["id"])
    channel_type = ChannelType(str(row["channel_type"]))
    if channel_type is ChannelType.SERIAL:
        return ChannelConfig(
            port_id=port_id,
            channel_type=channel_type,
            serial=SerialParameters(
                port_name=str(row["serial_port_name"]),
                baud_rate=int(row["baud_rate"]),
                data_bits=int(row["data_bits"]),
                stop_bits=int(row["stop_bits"]),
                parity=_parity(str(row["parity"])),
            ),
            timeout_ms=int(row["timeout_ms"]),
            retry_count=0,
            labels=(str(row["name"]),),
        )
    return ChannelConfig(
        port_id=port_id,
        channel_type=channel_type,
        tcp=TcpParameters(host=str(row["tcp_host"]), port=int(row["tcp_port"])),
        timeout_ms=int(row["timeout_ms"]),
        retry_count=0,
        labels=(str(row["name"]),),
    )


def _parity(value: str) -> Parity:
    if value == "E":
        return Parity.EVEN
    if value == "O":
        return Parity.ODD
    return Parity.NONE
