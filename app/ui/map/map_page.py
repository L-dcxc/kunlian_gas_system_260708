from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.models import DeviceStatus
from app.services.permissions import Permission, role_has_permission
from app.ui.common.errors import ErrorBanner, controlled_error_text
from app.ui.common.permission_hint import PermissionHint
from app.ui.common.safe_text import SafeTextLabel
from app.ui.common.status import StatusBadge
from app.ui.map.map_canvas import MapCanvas
from app.ui.map.view_models import MapListItemDisplay, MapMonitoringViewModel, MapPointDisplay, MapRuntimeDisplay

MAP_ERROR_TEXT = "地图加载失败"
DELETE_CONFIRM_TEXT = "确认删除当前地图？"

PathProvider = Callable[[], Path | str | None]
ImageResolver = Callable[[MapListItemDisplay], Path | None]
ConfirmDelete = Callable[[MapListItemDisplay], bool]


class MapMonitoringPage(QWidget):
    mapSelected = Signal(int)
    pointSelected = Signal(int)

    def __init__(
        self,
        view_model: MapMonitoringViewModel | None = None,
        session: object | None = None,
        parent: QWidget | None = None,
        *,
        can_configure: bool | None = None,
        map_image_resolver: ImageResolver | None = None,
        upload_path_provider: PathProvider | None = None,
        confirm_delete: ConfirmDelete | None = None,
        auto_load: bool = True,
    ) -> None:
        super().__init__(parent)
        self.view_model = view_model or MapMonitoringViewModel()
        self._owns_view_model = view_model is None
        self._session = session
        self._can_configure = _can_configure_from_session(session) if can_configure is None else bool(can_configure)
        self._map_image_resolver = map_image_resolver
        self._upload_path_provider = upload_path_provider
        self._confirm_delete = confirm_delete or self._confirm_delete_dialog
        self._runtime: MapRuntimeDisplay | None = None
        self._selected_point_id: int | None = None
        self._state = "loading" if auto_load else "empty"
        self._rendering_maps = False
        self._dirty = False

        self.error_banner = ErrorBanner(); self.error_banner.clear()
        self.permission_hint = PermissionHint(); self.permission_hint.setVisible(not self._can_configure)
        self.map_list = QListWidget(); self.map_list.setObjectName("MapList")
        self.map_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.upload_button = QPushButton("上传地图"); self.upload_button.setProperty("variant", "primary")
        self.delete_button = QPushButton("删除地图"); self.delete_button.setProperty("variant", "danger")
        self.refresh_button = QPushButton("刷新")
        self.save_button = QPushButton("保存点位")
        self.cancel_button = QPushButton("取消拖拽")
        self.upload_result_label = SafeTextLabel("", selectable=True); self.upload_result_label.setProperty("role", "muted")
        self.canvas = MapCanvas(editable=self._can_configure)
        self.detail_panel = self._build_detail_panel()
        self.alarm_body = QVBoxLayout(); self.alarm_body.setContentsMargins(0, 0, 0, 0); self.alarm_body.setSpacing(8)
        self.alarm_panel = self._build_alarm_panel()

        self._build_layout()
        self._connect_signals()
        self._apply_permission_state()
        self.set_state(self._state)
        if auto_load:
            self.view_model.load()

    def current_state(self) -> str:
        return self._state

    def selected_map_id(self) -> int | None:
        return self.view_model.selected_map_id

    def selected_point_id(self) -> int | None:
        return self._selected_point_id

    def closeEvent(self, event) -> None:  # noqa: N802 ANN001
        if self._owns_view_model:
            self.view_model.dispose()
        super().closeEvent(event)

    def set_state(self, state: str) -> None:
        self._state = state if state in {"loading", "empty", "error", "ready"} else "ready"
        if self._state == "loading":
            self.canvas.set_loading()
        elif self._state == "empty":
            self.canvas.clear_runtime("暂无地图，请上传厂区平面图")
        elif self._state == "error":
            self.canvas.clear_runtime(MAP_ERROR_TEXT)

    def upload_map(self) -> bool:
        if not self._require_permission():
            return False
        path = self._choose_upload_path()
        if path is None:
            return False
        self.upload_result_label.set_safe_text("正在校验地图文件...")
        ok = self.view_model.upload_map(self._session, Path(path))
        return bool(ok)

    def delete_selected_map(self, *, confirm: bool = True) -> bool:
        if not self._require_permission():
            return False
        item = self._selected_map_item()
        if item is None:
            self.error_banner.set_error("请先选择地图。")
            return False
        if confirm and not self._confirm_delete(item):
            return False
        return bool(self.view_model.delete_map(self._session, item.map_id))

    def save_pending_positions(self) -> bool:
        if not self._require_permission():
            return False
        pending = self.canvas.pending_ratios()
        if not pending:
            return True
        points = {point.point_id: point for point in self._runtime.points} if self._runtime is not None else {}
        saved_all = True
        for point_id, (x_ratio, y_ratio) in pending.items():
            point = points.get(point_id)
            if point is None:
                continue
            if not self.view_model.save_point_position(self._session, point, x_ratio, y_ratio):
                saved_all = False
                break
        if saved_all:
            self._set_dirty(False)
        return saved_all

    def cancel_pending_positions(self) -> None:
        self.canvas.cancel_pending()
        self._set_dirty(False)
        if self._selected_point_id is not None:
            point = self.view_model.find_point(self._selected_point_id)
            self._render_detail(point)

    def select_point(self, point_id: int) -> None:
        self._selected_point_id = int(point_id)
        self.view_model.select_point(int(point_id))
        self.pointSelected.emit(int(point_id))

    def _connect_signals(self) -> None:
        self.upload_button.clicked.connect(self.upload_map)
        self.delete_button.clicked.connect(lambda _checked=False: self.delete_selected_map())
        self.refresh_button.clicked.connect(self.view_model.retry)
        self.save_button.clicked.connect(self.save_pending_positions)
        self.cancel_button.clicked.connect(self.cancel_pending_positions)
        self.map_list.itemSelectionChanged.connect(self._on_map_selection_changed)
        self.canvas.pointClicked.connect(self.select_point)
        self.canvas.pointMoved.connect(self._on_point_moved)
        self.canvas.dirtyChanged.connect(self._set_dirty)
        self.view_model.loading_changed.connect(self._set_loading)
        self.view_model.maps_changed.connect(self._render_maps)
        self.view_model.runtime_changed.connect(self._render_runtime)
        self.view_model.error_changed.connect(self._set_error)
        self.view_model.detail_changed.connect(self._render_detail)
        self.view_model.upload_result_changed.connect(self._show_upload_result)
        self.view_model.point_save_result_changed.connect(self._show_point_save_result)

    def _set_loading(self, loading: bool) -> None:
        if loading:
            self.error_banner.clear()
            self.set_state("loading")

    def _set_error(self, message: str) -> None:
        if not message:
            self.error_banner.clear()
            return
        text = controlled_error_text(message, fallback=MAP_ERROR_TEXT)
        self.error_banner.set_error(text)
        if self._runtime is None:
            self.set_state("error")

    def _render_maps(self, maps: object) -> None:
        self._rendering_maps = True
        self.map_list.clear()
        for item in tuple(maps or ()):
            if not isinstance(item, MapListItemDisplay):
                continue
            row = QListWidgetItem(item.name)
            row.setData(Qt.ItemDataRole.UserRole, item.map_id)
            row.setToolTip(item.subtitle)
            self.map_list.addItem(row)
            if item.selected:
                self.map_list.setCurrentItem(row)
        self._rendering_maps = False
        self.delete_button.setEnabled(self._can_configure and self.map_list.currentItem() is not None)

    def _render_runtime(self, runtime: object) -> None:
        if not isinstance(runtime, MapRuntimeDisplay):
            self._runtime = None
            self._selected_point_id = None
            self._render_detail(None)
            self._render_alarm_list(())
            self._set_dirty(False)
            self.set_state("empty" if self.map_list.count() == 0 else "error")
            return
        self._runtime = runtime
        self.set_state("ready")
        self.canvas.set_runtime(runtime, self._resolve_image_path(runtime.map))
        self._render_alarm_list(tuple(point for point in runtime.points if point.active_alarm or point.is_offline))
        if self._selected_point_id is not None:
            self._render_detail(self.view_model.find_point(self._selected_point_id))
        else:
            self._render_detail(None)
        self._set_dirty(False)

    def _render_detail(self, point: object | None) -> None:
        if not isinstance(point, MapPointDisplay):
            self.detail_title.set_safe_text("未选择点位")
            self.detail_status.set_status(DeviceStatus.INVALID, text="未选择")
            for label in self.detail_fields.values():
                label.set_safe_text("-")
            return
        self._selected_point_id = point.point_id
        self.detail_title.set_safe_text(point.display_name)
        self.detail_status.set_status(point.status, active_alarm=point.pulse_eligible)
        self.detail_fields["detector"].set_safe_text(point.detector_name or point.detector_position_code or f"探测器 {point.detector_id}")
        self.detail_fields["controller"].set_safe_text(point.controller_name or "-")
        self.detail_fields["value"].set_safe_text(point.value_text)
        self.detail_fields["gas"].set_safe_text(point.gas_type or "-")
        self.detail_fields["coords"].set_safe_text(f"x={point.x_ratio:.3f}, y={point.y_ratio:.3f}")
        self.detail_fields["updated"].set_safe_text(point.timestamp or "-")
        self.detail_fields["alarm"].set_safe_text(point.active_alarm_type or "-")

    def _render_alarm_list(self, points: tuple[MapPointDisplay, ...]) -> None:
        _clear_layout(self.alarm_body)
        if not points:
            empty = SafeTextLabel("当前无未恢复警情", selectable=False); empty.setProperty("role", "muted")
            self.alarm_body.addWidget(empty)
        for point in points:
            item = _AlarmPointItem(point)
            item.clicked.connect(self.select_point)
            self.alarm_body.addWidget(item)
        self.alarm_body.addStretch(1)

    def _on_map_selection_changed(self) -> None:
        if self._rendering_maps:
            return
        item = self.map_list.currentItem()
        if item is None:
            return
        map_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if map_id > 0 and map_id != self.view_model.selected_map_id:
            self._selected_point_id = None
            self.view_model.select_map(map_id)
            self.mapSelected.emit(map_id)
        self.delete_button.setEnabled(self._can_configure)

    def _on_point_moved(self, point_id: int, x_ratio: float, y_ratio: float) -> None:
        point = self.view_model.find_point(point_id)
        if point is None:
            return
        self._selected_point_id = point_id
        self.view_model.detail_changed.emit(point)
        self.detail_fields["coords"].set_safe_text(f"x={x_ratio:.3f}, y={y_ratio:.3f}（未保存）")

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = bool(dirty)
        self.save_button.setEnabled(self._can_configure and self._dirty)
        self.cancel_button.setEnabled(self._can_configure and self._dirty)

    def _show_upload_result(self, message: str, success: bool) -> None:
        text = controlled_error_text(message, fallback="图片格式或大小不符合要求")
        self.upload_result_label.set_safe_text(text)
        if success:
            self.error_banner.clear()
        else:
            self.error_banner.set_error(text)

    def _show_point_save_result(self, message: str, success: bool) -> None:
        text = controlled_error_text(message, fallback="地图点位保存失败")
        if success:
            self.error_banner.clear()
            self.upload_result_label.set_safe_text(text)
        else:
            self.error_banner.set_error(text)

    def _require_permission(self) -> bool:
        if self._can_configure:
            return True
        self.error_banner.show_permission_denied()
        return False

    def _apply_permission_state(self) -> None:
        self.permission_hint.setVisible(not self._can_configure)
        self.canvas.set_editable(self._can_configure)
        self.upload_button.setEnabled(self._can_configure)
        self.delete_button.setEnabled(self._can_configure and self.map_list.currentItem() is not None)
        self._set_dirty(False)

    def _selected_map_item(self) -> MapListItemDisplay | None:
        map_id = self.view_model.selected_map_id
        return next((item for item in self.view_model.maps if item.map_id == map_id), None)

    def _choose_upload_path(self) -> Path | None:
        if self._upload_path_provider is not None:
            value = self._upload_path_provider()
            return None if value in {None, ""} else Path(value)
        path, _ = QFileDialog.getOpenFileName(self, "选择厂区平面图", "", "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)")
        return Path(path) if path else None

    def _resolve_image_path(self, item: MapListItemDisplay) -> Path | None:
        if self._map_image_resolver is None:
            return None
        try:
            return self._map_image_resolver(item)
        except Exception:
            self.error_banner.set_error("地图图片不可用")
            return None

    def _confirm_delete_dialog(self, item: MapListItemDisplay) -> bool:
        message = f"{DELETE_CONFIRM_TEXT}\n{item.name}"
        return QMessageBox.question(self, "删除地图", message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _build_layout(self) -> None:
        left = QFrame(); left.setProperty("panel", "true")
        left_title = SafeTextLabel("平面图列表", selectable=False); left_title.setProperty("role", "panelTitle")
        left_layout = QVBoxLayout(left); left_layout.setContentsMargins(12, 12, 12, 12); left_layout.setSpacing(8)
        actions = QHBoxLayout(); actions.addWidget(self.upload_button); actions.addWidget(self.delete_button)
        left_layout.addWidget(left_title); left_layout.addLayout(actions); left_layout.addWidget(self.upload_result_label); left_layout.addWidget(self.map_list, 1)

        toolbar = QFrame(); toolbar.setObjectName("MapToolbar")
        toolbar_layout = QHBoxLayout(toolbar); toolbar_layout.setContentsMargins(12, 8, 12, 8); toolbar_layout.setSpacing(8)
        toolbar_layout.addWidget(self.save_button); toolbar_layout.addWidget(self.cancel_button); toolbar_layout.addStretch(1); toolbar_layout.addWidget(self.refresh_button)
        center = QVBoxLayout(); center.setContentsMargins(0, 0, 0, 0); center.setSpacing(8)
        center.addWidget(toolbar); center.addWidget(self.canvas, 1)

        right = QVBoxLayout(); right.setContentsMargins(0, 0, 0, 0); right.setSpacing(12)
        right.addWidget(self.detail_panel); right.addWidget(self.alarm_panel, 1)
        body = QHBoxLayout(); body.setSpacing(12); body.addWidget(left, 1); body.addLayout(center, 3); body.addLayout(right, 1)
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 16, 16, 16); layout.setSpacing(12)
        layout.addWidget(self.error_banner); layout.addWidget(self.permission_hint); layout.addLayout(body, 1)

    def _build_detail_panel(self) -> QFrame:
        panel = QFrame(); panel.setObjectName("PointDetail"); panel.setProperty("panel", "true")
        self.detail_title = SafeTextLabel("未选择点位", selectable=True); self.detail_title.setProperty("role", "panelTitle")
        self.detail_status = StatusBadge(DeviceStatus.INVALID)
        self.detail_fields = {key: SafeTextLabel("-", selectable=True) for key in ("detector", "controller", "value", "gas", "coords", "updated", "alarm")}
        grid = QGridLayout(); grid.setContentsMargins(0, 0, 0, 0); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        for row, (key, title) in enumerate((("detector", "设备"), ("controller", "控制器"), ("value", "实时值"), ("gas", "气体"), ("coords", "比例坐标"), ("updated", "更新时间"), ("alarm", "警情"))):
            label = QLabel(title); label.setProperty("role", "fieldLabel")
            grid.addWidget(label, row, 0); grid.addWidget(self.detail_fields[key], row, 1)
        top = QHBoxLayout(); top.addWidget(self.detail_title, 1); top.addWidget(self.detail_status)
        layout = QVBoxLayout(panel); layout.setContentsMargins(14, 14, 14, 14); layout.setSpacing(12)
        layout.addLayout(top); layout.addLayout(grid); layout.addStretch(1)
        return panel

    def _build_alarm_panel(self) -> QFrame:
        panel = QFrame(); panel.setProperty("panel", "true")
        title = SafeTextLabel("警情列表", selectable=False); title.setProperty("role", "panelTitle")
        content = QWidget(); content.setLayout(self.alarm_body)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(content)
        layout = QVBoxLayout(panel); layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(8)
        layout.addWidget(title); layout.addWidget(scroll, 1)
        return panel


class _AlarmPointItem(QFrame):
    clicked = Signal(int)

    def __init__(self, point: MapPointDisplay, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._point_id = point.point_id
        self.setProperty("role", "alarmListItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status = StatusBadge(point.status); self.status.set_status(point.status, active_alarm=point.pulse_eligible)
        self.title = SafeTextLabel(point.display_name, selectable=True); self.title.setProperty("role", "panelTitle")
        self.message = SafeTextLabel(f"{point.status_text}：{point.value_text}", selectable=True)
        self.message.setProperty("role", "warningText" if point.active_alarm else "muted")
        layout = QVBoxLayout(self); layout.setContentsMargins(10, 10, 10, 10); layout.setSpacing(6)
        row = QHBoxLayout(); row.addWidget(self.status); row.addWidget(self.title, 1)
        layout.addLayout(row); layout.addWidget(self.message)

    def mousePressEvent(self, event) -> None:  # noqa: N802 ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._point_id)
        super().mousePressEvent(event)


def _can_configure_from_session(session: object | None) -> bool:
    role = getattr(session, "role", None)
    if role is None:
        return False
    try:
        return role_has_permission(str(role), Permission.SYSTEM_SETTINGS.value)
    except ValueError:
        return False


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
