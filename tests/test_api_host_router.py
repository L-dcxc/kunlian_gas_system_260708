from __future__ import annotations

import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.host import API_PORT_IN_USE_MESSAGE, DEFAULT_API_BIND_ADDRESS, LocalApiHost, create_api_app
from app.api.middleware import configure_api_middleware
from app.api.schemas import (
    AlarmResponse,
    ControllerResponse,
    DetectorResponse,
    DeviceRealtimeResponse,
    HealthResponse,
)
from app.config.defaults import ApiConfig, AppConfig
from app.services.models import Page, Pagination, ServiceResult


class _FakeReadService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def health(self) -> ServiceResult[HealthResponse]:
        self.calls.append("health")
        return ServiceResult.ok(HealthResponse(status="ok", api_enabled=True, acquisition_status="running"))

    def list_realtime_devices(self, query) -> ServiceResult[Page[DeviceRealtimeResponse]]:
        self.calls.append("list_realtime_devices")
        item = DeviceRealtimeResponse(
            detector_id=1,
            position_code="A-001",
            detector_name="detector1",
            controller_id=1,
            controller_name="controller1",
            status="normal",
            concentration=12.3,
            gas_type="methane",
            unit="%LEL",
            alarm_level=None,
            timestamp="2026-01-01T10:00:00+08:00",
        )
        return ServiceResult.ok(Page((item,), Pagination(page=query.page, per_page=query.per_page), 1))

    def get_realtime_device(self, detector_id: int) -> ServiceResult[DeviceRealtimeResponse]:
        self.calls.append("get_realtime_device")
        return ServiceResult.ok(
            DeviceRealtimeResponse(
                detector_id=detector_id,
                position_code="A-001",
                detector_name="detector1",
                controller_id=1,
                controller_name="controller1",
                status="normal",
                concentration=12.3,
                gas_type="methane",
                unit="%LEL",
                alarm_level=None,
                timestamp="2026-01-01T10:00:00+08:00",
            )
        )

    def list_active_alarms(self) -> ServiceResult[tuple[AlarmResponse, ...]]:
        self.calls.append("list_active_alarms")
        return ServiceResult.ok(())

    def list_alarm_history(self, query) -> ServiceResult[Page[AlarmResponse]]:
        self.calls.append("list_alarm_history")
        return ServiceResult.ok(Page((), Pagination(page=query.page, per_page=query.per_page), 0))

    def list_controllers(self) -> ServiceResult[tuple[ControllerResponse, ...]]:
        self.calls.append("list_controllers")
        return ServiceResult.ok(
            (
                ControllerResponse(
                    controller_id=1,
                    port_id=1,
                    controller_name="controller1",
                    address=1,
                    model=None,
                    detector_count=1,
                    enabled=True,
                ),
            )
        )

    def list_detectors(self) -> ServiceResult[tuple[DetectorResponse, ...]]:
        self.calls.append("list_detectors")
        return ServiceResult.ok(
            (
                DetectorResponse(
                    detector_id=1,
                    position_code="A-001",
                    detector_name="detector1",
                    port_id=1,
                    controller_id=1,
                    gas_type_id=1,
                    gas_type="methane",
                    unit="%LEL",
                    range_min=0,
                    range_max=100,
                    alarm_low=20,
                    alarm_high=50,
                    enabled=True,
                ),
            )
        )


class _BlockingServer:
    def __init__(self) -> None:
        self.should_exit = False
        self.started = threading.Event()

    def run(self) -> None:
        self.started.set()
        while not self.should_exit:
            time.sleep(0.01)


class ApiHostRouterTests(unittest.TestCase):
    def test_security_headers_and_get_routes_return_envelope(self) -> None:
        service = _FakeReadService()
        client = TestClient(create_api_app(service))

        response = client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "ok")
        self.assertEqual(body["data"]["acquisition_status"], "running")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("access-control-allow-origin", response.headers)

        self.assertTrue(client.get("/api/v1/devices/realtime").json()["success"])
        self.assertTrue(client.get("/api/v1/devices/1/realtime").json()["success"])
        self.assertTrue(client.get("/api/v1/alarms/active").json()["success"])
        self.assertTrue(client.get("/api/v1/alarms/history").json()["success"])
        self.assertTrue(client.get("/api/v1/controllers").json()["success"])
        self.assertTrue(client.get("/api/v1/detectors").json()["success"])

    def test_invalid_query_and_path_return_controlled_envelope(self) -> None:
        client = TestClient(create_api_app(_FakeReadService()))

        query_response = client.get(
            "/api/v1/devices/realtime",
            params={"page": "0", "per_page": "101", "status": "DROP TABLE alarms"},
        )
        self.assertEqual(query_response.status_code, 400)
        query_body = query_response.json()
        self.assertFalse(query_body["success"])
        self.assertEqual(query_body["message"], "参数校验失败")
        fields = {error["field"] for error in query_body["data"]["errors"]}
        self.assertEqual(fields, {"page", "per_page", "status"})

        path_response = client.get("/api/v1/devices/0/realtime")
        self.assertEqual(path_response.status_code, 400)
        self.assertFalse(path_response.json()["success"])

    def test_state_changing_methods_are_not_registered(self) -> None:
        client = TestClient(create_api_app(_FakeReadService()))
        paths = (
            "/api/v1/health",
            "/api/v1/devices/realtime",
            "/api/v1/devices/1/realtime",
            "/api/v1/alarms/active",
            "/api/v1/alarms/history",
            "/api/v1/controllers",
            "/api/v1/detectors",
        )
        for path in paths:
            for method in (client.post, client.put, client.patch, client.delete):
                with self.subTest(path=path, method=method.__name__):
                    response = method(path)
                    self.assertIn(response.status_code, {404, 405})
                    if response.headers.get("content-type", "").startswith("application/json"):
                        body = response.json()
                        self.assertFalse(body["success"])
                        self.assertNotIn("traceback", str(body).lower())

    def test_middleware_500_does_not_leak_sensitive_details(self) -> None:
        app = FastAPI()
        configure_api_middleware(app)

        @app.get("/boom")
        def boom() -> None:
            raise RuntimeError(
                "sqlite3.OperationalError SELECT password FROM users at E:\\secret\\app.sqlite3 Traceback line 1"
            )

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/boom")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["message"], "操作失败，请稍后重试。")
        output = str(body).lower()
        for forbidden in ("sqlite", "select", "password", "e:\\secret", "traceback", "line 1"):
            self.assertNotIn(forbidden, output)

    def test_host_defaults_loopback_and_stop_is_repeatable(self) -> None:
        servers: list[_BlockingServer] = []

        def server_factory(app, host: str, port: int) -> _BlockingServer:
            self.assertEqual(host, DEFAULT_API_BIND_ADDRESS)
            self.assertIsNotNone(app)
            self.assertGreaterEqual(port, 1024)
            server = _BlockingServer()
            servers.append(server)
            return server

        host = LocalApiHost(
            _FakeReadService(),
            AppConfig(api=ApiConfig(enabled=True)),
            server_factory=server_factory,
        )
        self.assertEqual(host.bind_address, DEFAULT_API_BIND_ADDRESS)
        result = host.start()
        self.assertTrue(result.started)
        self.assertTrue(servers[0].started.wait(timeout=1))
        self.assertTrue(host.is_running)
        host.stop(timeout=1)
        host.stop(timeout=1)
        self.assertFalse(host.is_running)
        self.assertTrue(servers[0].should_exit)

    def test_port_conflict_alerts_without_unhandled_exception(self) -> None:
        alerts: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((DEFAULT_API_BIND_ADDRESS, 0))
            sock.listen(1)
            occupied_port = int(sock.getsockname()[1])
            host = LocalApiHost(
                _FakeReadService(),
                AppConfig(api=ApiConfig(enabled=True, port=occupied_port)),
                alert_callback=alerts.append,
            )
            result = host.start()

        self.assertFalse(result.started)
        self.assertEqual(result.message, API_PORT_IN_USE_MESSAGE)
        self.assertEqual(alerts, [API_PORT_IN_USE_MESSAGE])
        self.assertEqual(host.last_alert, API_PORT_IN_USE_MESSAGE)
        host.stop()
        host.stop()

    def test_lan_binding_is_not_enabled_by_default(self) -> None:
        host = LocalApiHost(_FakeReadService(), AppConfig(api=ApiConfig(enabled=True, bind_address="0.0.0.0")))
        self.assertEqual(host.bind_address, DEFAULT_API_BIND_ADDRESS)


if __name__ == "__main__":
    unittest.main()
