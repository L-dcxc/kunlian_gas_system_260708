from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from app.services.bigscreen_service import BigscreenSnapshot
from app.ui.common.errors import controlled_error_text

LOAD_FAILED_TEXT = "大屏数据读取失败"
SUPPORTED_PAGES = ("data", "map", "devices")
DEFAULT_ROTATION_MS = 15000
DEFAULT_REFRESH_MS = 1000
MIN_TIMER_MS = 250
MAX_TIMER_MS = 3600000


@dataclass(frozen=True, slots=True)
class BigscreenPage:
    key: str
    title: str


PAGE_TITLES = {
    "data": "数据监控",
    "map": "地图监控",
    "devices": "设备状态",
}


class BigscreenViewModel(QObject):
    snapshotChanged = Signal(object)
    pageChanged = Signal(str)
    alarmFocusChanged = Signal(object)
    errorChanged = Signal(str)
    loadingChanged = Signal(bool)

    def __init__(self, service: object, parent: QObject | None = None, *, auto_start: bool = True) -> None:
        super().__init__(parent)
        self._service = service
        self._snapshot: BigscreenSnapshot | None = None
        self._pages: tuple[str, ...] = SUPPORTED_PAGES
        self._current_page = "data"
        self._loading = False
        self._refresh_pending = False
        self._disposed = False

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(DEFAULT_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh_from_timer)

        self._rotator_timer = QTimer(self)
        self._rotator_timer.setInterval(DEFAULT_ROTATION_MS)
        self._rotator_timer.timeout.connect(self.next_page)

        if auto_start:
            self.load()

    @property
    def snapshot(self) -> BigscreenSnapshot | None:
        return self._snapshot

    @property
    def current_page(self) -> str:
        return self._current_page

    @property
    def pages(self) -> tuple[BigscreenPage, ...]:
        return tuple(BigscreenPage(key=key, title=PAGE_TITLES[key]) for key in self._pages)

    @property
    def refresh_interval_ms(self) -> int:
        return self._refresh_timer.interval()

    @property
    def carousel_interval_ms(self) -> int:
        return self._rotator_timer.interval()

    def dispose(self) -> None:
        self._disposed = True
        self._refresh_timer.stop()
        self._rotator_timer.stop()

    def load(self) -> None:
        self._load(show_loading=True)

    def refresh(self) -> None:
        self._load(show_loading=False)

    def schedule_refresh(self) -> None:
        if self._disposed or self._refresh_pending:
            return
        self._refresh_pending = True
        self._refresh_timer.start()

    def next_page(self) -> None:
        if self._snapshot is not None and self._snapshot.alarm_focus is not None:
            self._set_page("alarm")
            return
        if not self._pages:
            return
        try:
            index = self._pages.index(self._current_page)
        except ValueError:
            index = -1
        self._set_page(self._pages[(index + 1) % len(self._pages)])

    def set_page(self, page_key: str) -> None:
        if page_key == "alarm" or page_key in self._pages:
            self._set_page(page_key)

    def _load(self, *, show_loading: bool) -> None:
        if self._disposed:
            return
        if show_loading:
            self._set_loading(True)
        try:
            result = self._service.get_snapshot()
            if not bool(getattr(result, "success", False)) or getattr(result, "data", None) is None:
                message = controlled_error_text(getattr(result, "message", ""), fallback=LOAD_FAILED_TEXT)
                self.errorChanged.emit(message)
                return
            self._apply_snapshot(getattr(result, "data"))
        except Exception as exc:  # UI boundary: hide internals, emit controlled text only.
            self.errorChanged.emit(controlled_error_text(str(exc), fallback=LOAD_FAILED_TEXT))
        finally:
            if show_loading:
                self._set_loading(False)

    def _apply_snapshot(self, snapshot: BigscreenSnapshot) -> None:
        self._snapshot = snapshot
        self._pages = _normalize_pages(getattr(snapshot.config, "pages", ()))
        refresh_ms = _bounded_ms(getattr(snapshot.config, "refresh_after_ms", DEFAULT_REFRESH_MS), DEFAULT_REFRESH_MS)
        interval_ms = _bounded_ms(
            int(getattr(snapshot.config, "interval_seconds", 15)) * 1000,
            DEFAULT_ROTATION_MS,
            minimum=5000,
        )
        self._refresh_timer.setInterval(refresh_ms)
        self._rotator_timer.setInterval(interval_ms)
        self.snapshotChanged.emit(snapshot)
        self.alarmFocusChanged.emit(snapshot.alarm_focus)
        if snapshot.alarm_focus is not None:
            # Alarm priority intentionally overrides carousel state but keeps timers alive for recovery.
            self._set_page("alarm")
        elif self._current_page == "alarm" or self._current_page not in self._pages:
            self._set_page(self._pages[0])
        if len(self._pages) > 1 and not self._rotator_timer.isActive():
            self._rotator_timer.start()
        elif len(self._pages) <= 1:
            self._rotator_timer.stop()
        self.schedule_refresh()

    def _refresh_from_timer(self) -> None:
        self._refresh_pending = False
        self.refresh()

    def _set_page(self, page_key: str) -> None:
        if self._current_page == page_key:
            return
        self._current_page = page_key
        self.pageChanged.emit(page_key)

    def _set_loading(self, loading: bool) -> None:
        if self._loading == loading:
            return
        self._loading = loading
        self.loadingChanged.emit(loading)


def _normalize_pages(value: object) -> tuple[str, ...]:
    pages = tuple(str(item) for item in (value or ()) if str(item) in SUPPORTED_PAGES)
    return pages or SUPPORTED_PAGES


def _bounded_ms(value: object, default: int, *, minimum: int = MIN_TIMER_MS, maximum: int = MAX_TIMER_MS) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default
