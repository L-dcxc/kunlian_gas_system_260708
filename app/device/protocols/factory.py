from __future__ import annotations

from collections.abc import Callable

from app.db.connection import Database
from app.db.repositories.settings_repository import SettingsRepository
from app.db.unit_of_work import UnitOfWork
from app.device.protocols.base import ProtocolAdapter
from app.device.protocols.protocol_1 import Protocol1Adapter
from app.device.protocols.protocol_2 import Protocol2Adapter
from app.services.models import ProtocolMode

PROTOCOL_MODE_SETTING_KEY = "protocol_mode"


class ProtocolAdapterFactory:
    """Load exactly one protocol adapter for a runtime context."""

    def __init__(
        self,
        *,
        database: Database | None = None,
        mode_provider: Callable[[], str | ProtocolMode | None] | None = None,
    ) -> None:
        self._database = database
        self._mode_provider = mode_provider
        self._locked_mode: ProtocolMode | None = None

    def get_adapter(self, mode: str | ProtocolMode | None = None) -> ProtocolAdapter:
        normalized = _resolve_mode(mode, self._mode_provider, self._database)
        if self._locked_mode is None:
            self._locked_mode = normalized
        elif self._locked_mode is not normalized:
            # A single acquisition/debug context must not silently swap adapters;
            # protocol 1 and 2 use different CRC byte order and register layout.
            raise ValueError("protocol mode cannot be mixed in one context")
        return create_protocol_adapter(normalized)

    @property
    def locked_mode(self) -> ProtocolMode | None:
        return self._locked_mode


def create_protocol_adapter(mode: str | ProtocolMode) -> ProtocolAdapter:
    normalized = _normalize_mode(mode)
    if normalized is ProtocolMode.PROTOCOL_1:
        return Protocol1Adapter()
    if normalized is ProtocolMode.PROTOCOL_2:
        return Protocol2Adapter()
    raise ValueError("unsupported protocol mode")


def load_protocol_adapter(
    *,
    mode: str | ProtocolMode | None = None,
    database: Database | None = None,
    mode_provider: Callable[[], str | ProtocolMode | None] | None = None,
) -> ProtocolAdapter:
    return create_protocol_adapter(_resolve_mode(mode, mode_provider, database))


def _resolve_mode(
    mode: str | ProtocolMode | None,
    mode_provider: Callable[[], str | ProtocolMode | None] | None,
    database: Database | None,
) -> ProtocolMode:
    if mode is not None:
        return _normalize_mode(mode)
    if mode_provider is not None:
        provided = mode_provider()
        if provided is not None:
            return _normalize_mode(provided)
    if database is not None:
        with UnitOfWork(database) as uow:
            value = SettingsRepository(uow).get_value(PROTOCOL_MODE_SETTING_KEY, ProtocolMode.PROTOCOL_1.value)
            uow.commit()
        return _normalize_mode(value)
    return ProtocolMode.PROTOCOL_1


def _normalize_mode(mode: str | ProtocolMode | None) -> ProtocolMode:
    try:
        return ProtocolMode(mode or ProtocolMode.PROTOCOL_1.value)
    except ValueError as exc:
        raise ValueError("protocol mode must be protocol_1 or protocol_2") from exc
