from __future__ import annotations

import json
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget

from app.ui.common.safe_text import SafeTextLabel


@dataclass(frozen=True, slots=True)
class ApiEndpointDoc:
    title: str
    method: str
    path: str
    description: str
    fields: tuple[str, ...]
    example: dict[str, object]


class ApiDocsPanel(QFrame):
    def __init__(self, parent: QWidget | None = None, endpoints: tuple[ApiEndpointDoc, ...] | None = None) -> None:
        super().__init__(parent)
        self.setProperty("panel", "true")
        self.setObjectName("ApiDocsPanel")
        self._endpoints = endpoints or DEFAULT_ENDPOINTS

        self.title_label = SafeTextLabel("本地 API 接口说明", selectable=False)
        self.title_label.setProperty("role", "panelTitle")
        self.subtitle_label = SafeTextLabel("仅展示只读接口字段与统一响应结构。", selectable=False)
        self.subtitle_label.setProperty("role", "muted")
        self.endpoint_list = QListWidget()
        self.endpoint_list.setObjectName("ApiEndpointList")
        self.detail_title = SafeTextLabel(selectable=False)
        self.detail_title.setProperty("role", "panelTitle")
        self.description_label = SafeTextLabel(selectable=True)
        self.description_label.setProperty("role", "muted")
        self.fields_label = SafeTextLabel(selectable=True)
        self.example_preview = QPlainTextEdit()
        self.example_preview.setReadOnly(True)
        self.example_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.example_preview.setMinimumHeight(180)

        for index, endpoint in enumerate(self._endpoints):
            item = QListWidgetItem(f"{endpoint.method} {endpoint.path}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.endpoint_list.addItem(item)
        self.endpoint_list.currentItemChanged.connect(self._endpoint_changed)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.title_label)
        left.addWidget(self.subtitle_label)
        left.addWidget(self.endpoint_list, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(self.detail_title)
        right.addWidget(self.description_label)
        right.addWidget(self.fields_label)
        right.addWidget(self.example_preview, 1)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(left, 1)
        body.addLayout(right, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addLayout(body)

        if self.endpoint_list.count() > 0:
            self.endpoint_list.setCurrentRow(0)

    def current_endpoint(self) -> ApiEndpointDoc | None:
        item = self.endpoint_list.currentItem()
        if item is None:
            return None
        index = int(item.data(Qt.ItemDataRole.UserRole))
        if 0 <= index < len(self._endpoints):
            return self._endpoints[index]
        return None

    def _endpoint_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            self.detail_title.set_safe_text("")
            self.description_label.set_safe_text("")
            self.fields_label.set_safe_text("")
            self.example_preview.setPlainText("")
            return
        endpoint = self._endpoints[int(current.data(Qt.ItemDataRole.UserRole))]
        # Endpoint selection is documentation-only; it must never call the API
        # host or read model because this settings page has no business writes.
        self.detail_title.set_safe_text(f"{endpoint.title}：{endpoint.method} {endpoint.path}")
        self.description_label.set_safe_text(endpoint.description)
        self.fields_label.set_safe_text("字段说明：\n" + "\n".join(endpoint.fields))
        self.example_preview.setPlainText(json.dumps(endpoint.example, ensure_ascii=False, indent=2))


def _envelope(data: object) -> dict[str, object]:
    return {"success": True, "code": 0, "message": "ok", "data": data}


DEFAULT_ENDPOINTS: tuple[ApiEndpointDoc, ...] = (
    ApiEndpointDoc(
        title="健康检查",
        method="GET",
        path="/api/v1/health",
        description="返回本地 API 与桌面读模型状态，不改变采集或配置。",
        fields=("status: API 读状态", "api_enabled: 是否启用", "acquisition_status: 采集服务状态"),
        example=_envelope({"status": "ok", "api_enabled": True, "acquisition_status": "running"}),
    ),
    ApiEndpointDoc(
        title="实时设备列表",
        method="GET",
        path="/api/v1/devices/realtime",
        description="按可选筛选条件读取探测器实时数据，字段来自统一 DeviceReading 读模型。",
        fields=(
            "detector_id: 探测器 ID",
            "position_code: 点位编号",
            "status: normal/alarm_low/alarm_high/fault/offline 等状态",
            "concentration: 当前浓度",
            "timestamp: 读数时间",
        ),
        example=_envelope(
            {
                "items": [
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
                        "alarm_level": None,
                        "timestamp": "2026-01-01T10:00:00+08:00",
                    }
                ],
                "pagination": {"page": 1, "per_page": 20, "total": 1, "total_pages": 1},
            }
        ),
    ),
    ApiEndpointDoc(
        title="单设备实时数据",
        method="GET",
        path="/api/v1/devices/{detector_id}/realtime",
        description="读取指定探测器的实时状态。detector_id 必须为正整数。",
        fields=("detector_id: 路径参数", "status: 当前状态", "concentration/unit: 当前浓度与单位"),
        example=_envelope({"detector_id": 1, "status": "normal", "concentration": 12.3, "unit": "%LEL"}),
    ),
    ApiEndpointDoc(
        title="当前报警",
        method="GET",
        path="/api/v1/alarms/active",
        description="返回未恢复报警记录与当前显示数据，不生成新报警。",
        fields=("alarm_id: 报警记录 ID", "alarm_type: 报警类型", "start_time: 触发时间", "current_status: 当前设备状态"),
        example=_envelope([{"alarm_id": 10, "detector_id": 1, "alarm_type": "alarm_high", "status": "active"}]),
    ),
    ApiEndpointDoc(
        title="历史报警",
        method="GET",
        path="/api/v1/alarms/history",
        description="按时间范围、探测器、控制器、报警类型和分页条件读取历史报警。",
        fields=("page/per_page: 分页", "start_time/end_time: ISO-8601 时间", "status: active 或 recovered"),
        example=_envelope({"items": [], "pagination": {"page": 1, "per_page": 20, "total": 0, "total_pages": 0}}),
    ),
    ApiEndpointDoc(
        title="控制器",
        method="GET",
        path="/api/v1/controllers",
        description="读取控制器配置读模型，不暴露内部路径、授权或密钥数据。",
        fields=("controller_id: 控制器 ID", "port_id: 端口 ID", "address: 通讯地址", "enabled: 是否启用"),
        example=_envelope([{"controller_id": 1, "port_id": 1, "controller_name": "一号控制器", "address": 1, "enabled": True}]),
    ),
    ApiEndpointDoc(
        title="探测器",
        method="GET",
        path="/api/v1/detectors",
        description="读取探测器配置读模型，用于第三方系统建立点位映射。",
        fields=("detector_id: 探测器 ID", "position_code: 点位编号", "gas_type/unit: 气体类型与单位", "enabled: 是否启用"),
        example=_envelope([{"detector_id": 1, "position_code": "A-001", "detector_name": "一号探头", "unit": "%LEL", "enabled": True}]),
    ),
)


__all__ = ["ApiDocsPanel", "ApiEndpointDoc", "DEFAULT_ENDPOINTS"]
