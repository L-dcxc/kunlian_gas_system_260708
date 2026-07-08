from __future__ import annotations

import csv
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.services.errors import ValidationError, validation_error
from app.services.models import ServiceError

MAP_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAP_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
}
IMPORT_EXTENSIONS = {".csv", ".xlsx"}
BACKUP_EXTENSIONS = {".zip"}
DEFAULT_MAX_MAP_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_IMPORT_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_BACKUP_BYTES = 1024 * 1024 * 1024
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
OFFICE_MACRO_SUFFIXES = ("vbaProject.bin", ".vbs", ".js", ".exe", ".bat", ".cmd", ".ps1")


@dataclass(frozen=True, slots=True)
class FileValidationResult:
    path: Path
    size_bytes: int
    errors: tuple[ServiceError, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class ImportRowError:
    row_number: int
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportValidationResult:
    rows: tuple[dict[str, str], ...]
    errors: tuple[ImportRowError, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


class FileValidator:
    def __init__(self, *, data_root: Path) -> None:
        self.data_root = data_root.resolve()

    def validate_map_file(self, path: Path, *, max_bytes: int = DEFAULT_MAX_MAP_BYTES) -> FileValidationResult:
        result = self._validate_candidate(path, MAP_EXTENSIONS, max_bytes=max_bytes, require_contained=False)
        errors = list(result.errors)
        if result.ok and not _has_expected_signature(result.path):
            errors.append(validation_error("content", "地图文件内容结构无效"))
        return FileValidationResult(path=result.path, size_bytes=result.size_bytes, errors=tuple(errors))

    def validate_import_file(self, path: Path, *, max_bytes: int = DEFAULT_MAX_IMPORT_BYTES) -> FileValidationResult:
        result = self._validate_candidate(path, IMPORT_EXTENSIONS, max_bytes=max_bytes, require_contained=False)
        if result.ok and path.suffix.lower() == ".xlsx":
            errors = list(result.errors)
            errors.extend(_scan_xlsx_for_active_content(path))
            return FileValidationResult(path=result.path, size_bytes=result.size_bytes, errors=tuple(errors))
        return result

    def validate_backup_candidate(self, path: Path, *, max_bytes: int = DEFAULT_MAX_BACKUP_BYTES) -> FileValidationResult:
        result = self._validate_candidate(path, BACKUP_EXTENSIONS, max_bytes=max_bytes, require_contained=False)
        errors = list(result.errors)
        if result.ok:
            try:
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        if not name or name.endswith("/"):
                            continue
                        if _is_zip_path_unsafe(name):
                            errors.append(validation_error("backup", "备份文件包含非法路径"))
                            break
                    if "manifest.json" not in archive.namelist():
                        errors.append(validation_error("backup", "备份文件缺少清单"))
            except zipfile.BadZipFile:
                errors.append(validation_error("backup", "备份文件格式无效"))
        return FileValidationResult(path=result.path, size_bytes=result.size_bytes, errors=tuple(errors))

    def ensure_within_data_root(self, path: Path) -> Path:
        resolved = path.expanduser().resolve()
        if not _contains(self.data_root, resolved):
            raise ValidationError("路径不在受控数据目录内")
        return resolved

    def _validate_candidate(
        self,
        path: Path,
        allowed_extensions: set[str],
        *,
        max_bytes: int,
        require_contained: bool,
    ) -> FileValidationResult:
        errors: list[ServiceError] = []
        resolved = path.expanduser().resolve()
        if require_contained and not _contains(self.data_root, resolved):
            errors.append(validation_error("path", "路径不在受控数据目录内"))
        if resolved.suffix.lower() not in allowed_extensions:
            errors.append(validation_error("extension", "文件类型不受支持"))
        try:
            size = resolved.stat().st_size
        except OSError:
            errors.append(validation_error("path", "文件不可读取"))
            return FileValidationResult(path=resolved, size_bytes=0, errors=tuple(errors))
        if size <= 0:
            errors.append(validation_error("size", "文件为空"))
        if size > max_bytes:
            errors.append(validation_error("size", "文件大小超出限制"))
        return FileValidationResult(path=resolved, size_bytes=size, errors=tuple(errors))


def validate_csv_import(path: Path, *, required_fields: Iterable[str], allowed_fields: Iterable[str]) -> ImportValidationResult:
    required = tuple(required_fields)
    allowed = set(allowed_fields)
    rows: list[dict[str, str]] = []
    errors: list[ImportRowError] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        missing = [field for field in required if field not in headers]
        unsupported = [field for field in headers if field not in allowed]
        for field in missing:
            errors.append(ImportRowError(row_number=1, field=field, message="缺少模板字段"))
        for field in unsupported:
            errors.append(ImportRowError(row_number=1, field=field, message="字段不在模板白名单内"))
        if errors:
            return ImportValidationResult(rows=(), errors=tuple(errors))
        for index, row in enumerate(reader, start=2):
            clean_row: dict[str, str] = {}
            for field in headers:
                value = str(row.get(field) or "").strip()
                if _looks_like_formula(value):
                    # Spreadsheet formulas can execute when a future operator opens
                    # exports/import error reports; keep them as invalid data here.
                    errors.append(ImportRowError(index, field, "单元格公式不允许导入"))
                    continue
                clean_row[field] = value[:512]
            rows.append(clean_row)
    return ImportValidationResult(rows=tuple(rows), errors=tuple(errors))


def safe_relative_path(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if not _contains(resolved_root, resolved_candidate):
        raise ValidationError("路径不在受控目录内")
    return resolved_candidate.relative_to(resolved_root)


def _has_expected_signature(path: Path) -> bool:
    signatures = MAP_SIGNATURES.get(path.suffix.lower(), ())
    if not signatures:
        return False
    try:
        header = path.read_bytes()[:16]
    except OSError:
        return False
    if path.suffix.lower() == ".webp":
        return header.startswith(b"RIFF") and b"WEBP" in header[8:16]
    return any(header.startswith(signature) for signature in signatures)


def _scan_xlsx_for_active_content(path: Path) -> list[ServiceError]:
    errors: list[ServiceError] = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            for name in names:
                lower = name.lower()
                if any(lower.endswith(suffix.lower()) for suffix in OFFICE_MACRO_SUFFIXES):
                    errors.append(validation_error("xlsx", "Excel 文件包含宏或嵌入内容"))
                    break
                if lower.startswith("xl/worksheets/") and lower.endswith(".xml"):
                    with archive.open(name) as member:
                        if b"<f" in member.read(1024 * 1024):
                            errors.append(validation_error("xlsx", "Excel 文件包含公式"))
                            break
    except zipfile.BadZipFile:
        errors.append(validation_error("xlsx", "Excel 文件结构无效"))
    return errors


def _is_zip_path_unsafe(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return True
    return bool(Path(normalized).drive) or os.path.isabs(normalized)


def _looks_like_formula(value: str) -> bool:
    return bool(value) and value[0] in CSV_FORMULA_PREFIXES


def _contains(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
