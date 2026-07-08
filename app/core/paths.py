from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from app.config.defaults import APP_NAME, CONFIG_FILE_NAME


class PathSetupError(RuntimeError):
    """Raised when controlled runtime directories cannot be prepared."""


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    data_dir: Path
    maps_dir: Path
    backups_dir: Path
    logs_dir: Path
    config_dir: Path
    db_dir: Path
    config_file: Path
    database_file: Path

    @classmethod
    def create(cls, data_dir: str | os.PathLike[str] | None = None) -> "AppPaths":
        project_root = find_project_root()
        resolved_data_dir = resolve_data_dir(project_root, data_dir)
        paths = cls(
            project_root=project_root,
            data_dir=resolved_data_dir,
            maps_dir=resolved_data_dir / "maps",
            backups_dir=resolved_data_dir / "backups",
            logs_dir=resolved_data_dir / "logs",
            config_dir=resolved_data_dir / "config",
            db_dir=resolved_data_dir / "db",
            config_file=resolved_data_dir / "config" / CONFIG_FILE_NAME,
            database_file=resolved_data_dir / "db" / "app.sqlite3",
        )
        paths.ensure_directories()
        return paths

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.maps_dir,
            self.backups_dir,
            self.logs_dir,
            self.config_dir,
            self.db_dir,
        ):
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise PathSetupError("运行数据目录无法创建，请检查应用数据目录权限。") from exc

    def with_database_filename(self, filename: str) -> "AppPaths":
        return AppPaths(
            project_root=self.project_root,
            data_dir=self.data_dir,
            maps_dir=self.maps_dir,
            backups_dir=self.backups_dir,
            logs_dir=self.logs_dir,
            config_dir=self.config_dir,
            db_dir=self.db_dir,
            config_file=self.config_file,
            database_file=self.db_dir / filename,
        )

    def contains(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.data_dir.resolve())
            return True
        except ValueError:
            return False


def find_project_root() -> Path:
    if is_frozen_runtime():
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return _normalize_candidate(bundle_root)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_data_dir(project_root: Path, data_dir: str | os.PathLike[str] | None = None) -> Path:
    candidate = data_dir or os.environ.get("GAS_ALARM_DATA_DIR")
    if candidate:
        return _normalize_candidate(candidate)
    if is_frozen_runtime():
        return default_user_data_dir()
    return (project_root / "data").resolve()


def default_user_data_dir() -> Path:
    # Frozen builds may run from Program Files, so mutable state belongs under
    # the per-user application data root instead of next to the executable.
    for variable in ("LOCALAPPDATA", "APPDATA"):
        value = os.environ.get(variable)
        if value:
            return _normalize_candidate(value) / APP_NAME
    return _normalize_candidate(Path.home() / APP_NAME)


def _normalize_candidate(candidate: str | os.PathLike[str]) -> Path:
    try:
        path = Path(candidate).expanduser()
        # The path is accepted only as a filesystem location; command-like
        # strings are not interpreted or executed.
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PathSetupError("运行数据目录无效，请检查应用数据目录设置。") from exc


def public_path_label(path: Path) -> str:
    return path.name or "应用数据目录"
