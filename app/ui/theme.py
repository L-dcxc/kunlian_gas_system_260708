from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class ThemeMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"


LIGHT_TOKENS: Mapping[str, str] = MappingProxyType(
    {
        "bg_window": "#F1F5F9",
        "bg_panel": "#FFFFFF",
        "bg_subtle": "#E2E8F0",
        "bg_error": "#FEE2E2",
        "text_primary": "#0F172A",
        "text_secondary": "#475569",
        "text_inverse": "#FFFFFF",
        "border_default": "#CBD5E1",
        "border_strong": "#94A3B8",
        "primary": "#1D4ED8",
        "primary_hover": "#2563EB",
        "data_accent": "#06B6D4",
        "status_normal": "#16A34A",
        "status_running": "#1D4ED8",
        "status_warning": "#F59E0B",
        "status_low_alarm": "#EA580C",
        "status_high_alarm": "#DC2626",
        "status_high_alarm_pulse": "#B91C1C",
        "status_fault": "#B91C1C",
        "status_offline": "#6B7280",
        "status_shielded": "#7C3AED",
        "status_warmup": "#0891B2",
        "disabled_text": "#94A3B8",
        "disabled_bg": "#E2E8F0",
        "font_ui": '"Microsoft YaHei UI", "Segoe UI", Arial, sans-serif',
        "font_mono": '"Consolas", "Cascadia Mono", monospace',
        "font_size_xs": "11px",
        "font_size_sm": "12px",
        "font_size_md": "14px",
        "font_size_lg": "16px",
        "font_size_xl": "20px",
        "font_size_2xl": "24px",
        "font_size_screen_title": "36px",
        "font_size_screen_metric": "56px",
        "font_weight_regular": "400",
        "font_weight_medium": "500",
        "font_weight_bold": "700",
        "space_1": "4px",
        "space_2": "8px",
        "space_3": "12px",
        "space_4": "16px",
        "space_6": "24px",
        "space_8": "32px",
        "radius_sm": "4px",
        "radius_md": "6px",
        "radius_lg": "10px",
        "border_width": "1px",
        "focus_width": "2px",
    }
)

DARK_TOKENS: Mapping[str, str] = MappingProxyType(
    {
        **dict(LIGHT_TOKENS),
        "bg_window": "#0B1220",
        "bg_panel": "#111827",
        "bg_subtle": "#1F2937",
        "bg_error": "#3F1216",
        "text_primary": "#F8FAFC",
        "text_secondary": "#CBD5E1",
        "border_default": "#334155",
        "border_strong": "#64748B",
        "primary": "#60A5FA",
        "primary_hover": "#93C5FD",
        "status_normal": "#22C55E",
        "status_running": "#60A5FA",
        "status_warning": "#FBBF24",
        "status_low_alarm": "#FB923C",
        "status_high_alarm": "#F87171",
        "status_high_alarm_pulse": "#DC2626",
        "status_fault": "#FCA5A5",
        "status_offline": "#94A3B8",
        "status_shielded": "#A78BFA",
        "status_warmup": "#22D3EE",
        "disabled_text": "#94A3B8",
        "disabled_bg": "#1F2937",
    }
)


@dataclass(frozen=True, slots=True)
class AppTheme:
    mode: ThemeMode | str = ThemeMode.LIGHT

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", ThemeMode(self.mode))

    @property
    def tokens(self) -> Mapping[str, str]:
        return tokens_for_mode(self.mode)

    def qss(self) -> str:
        from app.ui.styles import build_global_qss

        return build_global_qss(self.tokens)

    def apply_to(self, target: object) -> None:
        if not hasattr(target, "setStyleSheet"):
            raise TypeError("theme target must provide setStyleSheet")
        if hasattr(target, "setProperty"):
            target.setProperty("appTheme", self.mode.value)
        target.setStyleSheet(self.qss())


def tokens_for_mode(mode: ThemeMode | str = ThemeMode.LIGHT) -> Mapping[str, str]:
    selected = ThemeMode(mode)
    if selected is ThemeMode.DARK:
        return DARK_TOKENS
    return LIGHT_TOKENS
