from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repositories.base import EntityRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PortRepository(EntityRepository):
    table_name = "ports"
    allowed_sort_columns = frozenset({"id", "name", "channel_type", "created_at", "updated_at"})
    default_sort = "id"

    def list_active(self):
        return self.fetch_all("SELECT * FROM ports WHERE deleted_at IS NULL ORDER BY id ASC")

    def find_active_by_id(self, port_id: int):
        return self.fetch_one("SELECT * FROM ports WHERE id = ? AND deleted_at IS NULL", (port_id,))

    def find_active_by_name(self, name: str):
        return self.fetch_one("SELECT * FROM ports WHERE name = ? AND deleted_at IS NULL", (name,))

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO ports(
                name, channel_type, serial_port_name, baud_rate, data_bits, parity, stop_bits,
                tcp_host, tcp_port, poll_interval_ms, timeout_ms, failure_threshold,
                reconnect_interval_ms, is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["name"],
                values["channel_type"],
                values.get("serial_port_name"),
                values.get("baud_rate"),
                values.get("data_bits"),
                values.get("parity"),
                values.get("stop_bits"),
                values.get("tcp_host"),
                values.get("tcp_port"),
                values["poll_interval_ms"],
                values["timeout_ms"],
                values["failure_threshold"],
                values["reconnect_interval_ms"],
                1 if values.get("is_enabled", True) else 0,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update(self, port_id: int, values: dict[str, Any]) -> None:
        now = _now()
        self.execute(
            """
            UPDATE ports
            SET name = ?, channel_type = ?, serial_port_name = ?, baud_rate = ?, data_bits = ?, parity = ?,
                stop_bits = ?, tcp_host = ?, tcp_port = ?, poll_interval_ms = ?, timeout_ms = ?,
                failure_threshold = ?, reconnect_interval_ms = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                values["name"],
                values["channel_type"],
                values.get("serial_port_name"),
                values.get("baud_rate"),
                values.get("data_bits"),
                values.get("parity"),
                values.get("stop_bits"),
                values.get("tcp_host"),
                values.get("tcp_port"),
                values["poll_interval_ms"],
                values["timeout_ms"],
                values["failure_threshold"],
                values["reconnect_interval_ms"],
                1 if values.get("is_enabled", True) else 0,
                now,
                port_id,
            ),
        )

    def soft_delete(self, port_id: int) -> None:
        now = _now()
        self.execute(
            "UPDATE ports SET deleted_at = ?, is_enabled = 0, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, port_id),
        )


class ControllerRepository(EntityRepository):
    table_name = "controllers"
    allowed_sort_columns = frozenset({"id", "name", "port_id", "address", "created_at", "updated_at"})
    default_sort = "id"

    def list_active(self):
        return self.fetch_all("SELECT * FROM controllers WHERE deleted_at IS NULL ORDER BY id ASC")

    def find_active_by_id(self, controller_id: int):
        return self.fetch_one("SELECT * FROM controllers WHERE id = ? AND deleted_at IS NULL", (controller_id,))

    def find_active_by_port_address(self, port_id: int, address: int):
        return self.fetch_one(
            "SELECT * FROM controllers WHERE port_id = ? AND address = ? AND deleted_at IS NULL",
            (port_id, address),
        )

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO controllers(port_id, name, address, model, detector_count, is_enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["port_id"],
                values["name"],
                values["address"],
                values.get("model"),
                values["detector_count"],
                1 if values.get("is_enabled", True) else 0,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update(self, controller_id: int, values: dict[str, Any]) -> None:
        now = _now()
        self.execute(
            """
            UPDATE controllers
            SET port_id = ?, name = ?, address = ?, model = ?, detector_count = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                values["port_id"],
                values["name"],
                values["address"],
                values.get("model"),
                values["detector_count"],
                1 if values.get("is_enabled", True) else 0,
                now,
                controller_id,
            ),
        )

    def soft_delete(self, controller_id: int) -> None:
        now = _now()
        self.execute(
            """
            UPDATE controllers SET deleted_at = ?, is_enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, controller_id),
        )


class GasTypeRepository(EntityRepository):
    table_name = "gas_types"
    allowed_sort_columns = frozenset({"id", "name", "unit", "created_at", "updated_at"})
    default_sort = "name"

    def list_active(self):
        return self.fetch_all("SELECT * FROM gas_types WHERE deleted_at IS NULL ORDER BY name ASC")

    def find_active_by_id(self, gas_type_id: int):
        return self.fetch_one("SELECT * FROM gas_types WHERE id = ? AND deleted_at IS NULL", (gas_type_id,))

    def find_active_by_name(self, name: str):
        return self.fetch_one("SELECT * FROM gas_types WHERE name = ? AND deleted_at IS NULL", (name,))

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO gas_types(
                name, unit, range_min, range_max, default_alarm_low, default_alarm_high,
                is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                values["name"],
                values["unit"],
                values["range_min"],
                values["range_max"],
                values.get("default_alarm_low"),
                values.get("default_alarm_high"),
                1 if values.get("is_enabled", True) else 0,
                now,
                now,
            ),
        )
        return int(cursor.lastrowid)

    def update(self, gas_type_id: int, values: dict[str, Any]) -> None:
        now = _now()
        self.execute(
            """
            UPDATE gas_types
            SET name = ?, unit = ?, range_min = ?, range_max = ?, default_alarm_low = ?,
                default_alarm_high = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (
                values["name"],
                values["unit"],
                values["range_min"],
                values["range_max"],
                values.get("default_alarm_low"),
                values.get("default_alarm_high"),
                1 if values.get("is_enabled", True) else 0,
                now,
                gas_type_id,
            ),
        )

    def soft_delete(self, gas_type_id: int) -> None:
        now = _now()
        self.execute(
            "UPDATE gas_types SET deleted_at = ?, is_enabled = 0, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, gas_type_id),
        )


class DetectorRepository(EntityRepository):
    table_name = "detectors"
    allowed_sort_columns = frozenset(
        {"id", "name", "controller_id", "port_id", "position_code", "protocol_address", "created_at", "updated_at"}
    )
    default_sort = "id"

    def list_active(self):
        return self.fetch_all("SELECT * FROM detectors WHERE deleted_at IS NULL ORDER BY id ASC")

    def find_active_by_id(self, detector_id: int):
        return self.fetch_one("SELECT * FROM detectors WHERE id = ? AND deleted_at IS NULL", (detector_id,))

    def find_active_by_position_code(self, position_code: str):
        return self.fetch_one(
            "SELECT * FROM detectors WHERE position_code = ? AND deleted_at IS NULL",
            (position_code,),
        )

    def count_for_port(self, port_id: int) -> int:
        row = self.fetch_one(
            "SELECT COUNT(*) AS total FROM detectors WHERE port_id = ? AND deleted_at IS NULL",
            (port_id,),
        )
        return int(row["total"] if row is not None else 0)

    def count_for_controller(self, controller_id: int) -> int:
        row = self.fetch_one(
            "SELECT COUNT(*) AS total FROM detectors WHERE controller_id = ? AND deleted_at IS NULL",
            (controller_id,),
        )
        return int(row["total"] if row is not None else 0)

    def count_for_gas_type(self, gas_type_id: int) -> int:
        row = self.fetch_one(
            "SELECT COUNT(*) AS total FROM detectors WHERE gas_type_id = ? AND deleted_at IS NULL",
            (gas_type_id,),
        )
        return int(row["total"] if row is not None else 0)

    def create(self, values: dict[str, Any]) -> int:
        now = _now()
        cursor = self.execute(
            """
            INSERT INTO detectors(
                controller_id, port_id, position_code, name, model, protocol_address, register_index,
                gas_type_id, unit, range_min, range_max, alarm_low, alarm_high, alarm_type,
                sound_enabled, store_interval_sec, sensor_life_until, calibration_cycle_days,
                is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _detector_parameters(values, now),
        )
        return int(cursor.lastrowid)

    def update(self, detector_id: int, values: dict[str, Any]) -> None:
        now = _now()
        self.execute(
            """
            UPDATE detectors
            SET controller_id = ?, port_id = ?, position_code = ?, name = ?, model = ?, protocol_address = ?,
                register_index = ?, gas_type_id = ?, unit = ?, range_min = ?, range_max = ?, alarm_low = ?,
                alarm_high = ?, alarm_type = ?, sound_enabled = ?, store_interval_sec = ?, sensor_life_until = ?,
                calibration_cycle_days = ?, is_enabled = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            _detector_parameters(values, now, detector_id),
        )

    def soft_delete(self, detector_id: int) -> None:
        now = _now()
        self.execute(
            """
            UPDATE detectors SET deleted_at = ?, is_enabled = 0, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, detector_id),
        )


def _detector_parameters(values: dict[str, Any], now: str, detector_id: int | None = None) -> tuple[object, ...]:
    params: tuple[object, ...] = (
        values.get("controller_id"),
        values["port_id"],
        values["position_code"],
        values["name"],
        values.get("model"),
        values["protocol_address"],
        values["register_index"],
        values["gas_type_id"],
        values["unit"],
        values["range_min"],
        values["range_max"],
        values.get("alarm_low"),
        values.get("alarm_high"),
        values["alarm_type"],
        1 if values.get("sound_enabled", True) else 0,
        values["store_interval_sec"],
        values.get("sensor_life_until"),
        values.get("calibration_cycle_days"),
        1 if values.get("is_enabled", True) else 0,
        now,
        now,
    )
    if detector_id is not None:
        return params[:-1] + (detector_id,)
    return params
