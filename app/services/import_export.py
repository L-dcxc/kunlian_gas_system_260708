from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.services.errors import ValidationError
from app.services.file_validation import FileValidator, ImportValidationResult, validate_csv_import


@dataclass(frozen=True, slots=True)
class ImportTemplate:
    required_fields: tuple[str, ...]
    allowed_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.required_fields:
            raise ValueError("required_fields are required")
        if not set(self.required_fields).issubset(set(self.allowed_fields)):
            raise ValueError("required_fields must be allowed")


@dataclass(frozen=True, slots=True)
class ImportPlan:
    source: Path
    template: ImportTemplate
    validation: ImportValidationResult


class ImportExportService:
    def __init__(self, validator: FileValidator) -> None:
        self._validator = validator

    def prepare_import(self, source: Path, template: ImportTemplate) -> ImportPlan:
        file_result = self._validator.validate_import_file(source)
        if not file_result.ok:
            raise ValidationError("导入文件校验失败", details=list(file_result.errors))
        if file_result.path.suffix.lower() != ".csv":
            # XLSX parsing will be added when a vetted dependency/reader policy is
            # chosen; we already validate the zip container for active content.
            return ImportPlan(source=file_result.path, template=template, validation=ImportValidationResult(rows=()))
        validation = validate_csv_import(
            file_result.path,
            required_fields=template.required_fields,
            allowed_fields=template.allowed_fields,
        )
        return ImportPlan(source=file_result.path, template=template, validation=validation)

    def export_csv(self, destination: Path, fields: Iterable[str], rows: Iterable[dict[str, object]]) -> Path:
        safe_destination = self._validator.ensure_within_data_root(destination)
        field_list = tuple(fields)
        if not field_list:
            raise ValidationError("导出字段不能为空")
        safe_destination.parent.mkdir(parents=True, exist_ok=True)
        with safe_destination.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_list, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: _neutralize_formula(str(row.get(field, ""))) for field in field_list})
        return safe_destination


def _neutralize_formula(value: str) -> str:
    # Exported CSV may later be opened by spreadsheet software; prefixing a quote
    # prevents formula execution while preserving user-visible text content.
    if value and value[0] in {"=", "+", "-", "@"}:
        return "'" + value
    return value
