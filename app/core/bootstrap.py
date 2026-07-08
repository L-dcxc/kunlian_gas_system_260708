from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config.defaults import AppConfig
from app.config.loader import load_config
from app.core.audit import AuditLogger
from app.core.event_bus import EventBus
from app.core.logging import configure_logging, get_logger, shutdown_logging
from app.core.paths import AppPaths
from app.core.runtime_locks import RuntimeLockManager
from app.core.scheduler import Scheduler
from app.core.state_store import StateStore
from app.core.workers import WorkerPool
from app.db.connection import Database


@dataclass
class CapabilityContainers:
    """Empty registries for later service/device/API composition.

    The skeleton keeps these as plain mappings so the container phase can prove
    wiring points without importing concrete UI pages, protocol adapters, or API
    business routes before their owning modules exist.
    """

    services: dict[str, Any] = field(default_factory=dict)
    devices: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppContext:
    config: AppConfig
    paths: AppPaths
    db: Database
    state_store: StateStore
    event_bus: EventBus
    scheduler: Scheduler
    workers: WorkerPool
    runtime_locks: RuntimeLockManager
    audit: AuditLogger
    containers: CapabilityContainers

    def shutdown(self) -> None:
        api_host = self.containers.api.get("host")
        if api_host is not None and hasattr(api_host, "stop"):
            try:
                api_host.stop()
            except Exception as exc:
                get_logger("bootstrap").warning("api host shutdown failed: %s", exc.__class__.__name__)
        try:
            self.scheduler.shutdown()
        finally:
            try:
                self.workers.shutdown()
            finally:
                # Windows keeps log files locked until handlers close explicitly,
                # so the runtime context owns logging shutdown as part of cleanup.
                shutdown_logging()


def create_app_context(data_dir: str | None = None) -> AppContext:
    """Create the platform runtime context without importing UI or business modules."""

    paths = AppPaths.create(data_dir)
    load_result = load_config(paths.config_file)
    paths = paths.with_database_filename(load_result.config.database.filename)

    logger = configure_logging(
        paths.logs_dir,
        load_result.config.logging,
        sensitive_paths=(paths.data_dir, paths.database_file),
    )
    for warning in load_result.warnings:
        logger.warning(warning)

    runtime_locks = RuntimeLockManager()
    database = Database(paths.database_file, load_result.config.database)
    # Migrations replace schema state and therefore share the same lock family as
    # future restore/backup/acquisition work.
    with runtime_locks.acquire("migration"):
        database.initialize()

    event_bus = EventBus()
    state_store = StateStore(event_bus=event_bus)
    scheduler = Scheduler()
    workers = WorkerPool()
    audit = AuditLogger()
    containers = CapabilityContainers()

    get_logger("bootstrap").info("platform runtime initialized")
    return AppContext(
        config=load_result.config,
        paths=paths,
        db=database,
        state_store=state_store,
        event_bus=event_bus,
        scheduler=scheduler,
        workers=workers,
        runtime_locks=runtime_locks,
        audit=audit,
        containers=containers,
    )
