from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Iterable, Sequence

from app.services.errors import ValidationError

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
APP_VERSION = "0.1.0"
ALLOWED_TOP_LEVELS = {"db", "config", "maps"}
ALLOWED_FILE_KINDS = {"database", "config", "map"}
MAX_BACKUP_BYTES = 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 512 * 1024 * 1024
MAX_FILE_COUNT = 10000
INCLUDE_LICENSE_DEFAULT = False  # [待确认] License artifacts are excluded until product restore rules are confirmed.


@dataclass(frozen=True, slots=True)
class PackageSource:
    source_path: Path
    archive_path: str
    kind: str


@dataclass(frozen=True, slots=True)
class ManifestFile:
    path: str
    kind: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    manifest_version: int
    app_version: str
    created_at: str
    schema_version: str
    include_license: bool
    files: tuple[ManifestFile, ...]

    def to_json_bytes(self) -> bytes:
        payload = {
            "manifest_version": self.manifest_version,
            "app_version": self.app_version,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "include_license": self.include_license,
            "files": [
                {
                    "path": item.path,
                    "kind": item.kind,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in self.files
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ValidatedBackupPackage:
    package_path: Path
    manifest: BackupManifest
    total_size_bytes: int


class BackupPackageError(ValidationError):
    public_message = "备份文件校验失败"


def create_backup_package(
    *,
    target_file: Path,
    sources: Sequence[PackageSource],
    schema_version: str,
) -> BackupManifest:
    if not sources:
        raise BackupPackageError("备份内容为空")
    target_file.parent.mkdir(parents=True, exist_ok=True)
    files = tuple(_manifest_file(source) for source in sources)
    manifest = BackupManifest(
        manifest_version=MANIFEST_VERSION,
        app_version=APP_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        schema_version=_schema_version(schema_version),
        include_license=INCLUDE_LICENSE_DEFAULT,
        files=files,
    )
    with zipfile.ZipFile(target_file, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, manifest.to_json_bytes())
        for source in sources:
            archive.write(source.source_path, _safe_archive_path(source.archive_path))
    return manifest


def validate_backup_package(package_path: Path, *, current_schema_version: str) -> ValidatedBackupPackage:
    resolved = package_path.expanduser().resolve()
    if resolved.suffix.lower() != ".zip":
        raise BackupPackageError("备份文件类型无效")
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        raise BackupPackageError("备份文件不可读取") from exc
    if size <= 0 or size > MAX_BACKUP_BYTES:
        raise BackupPackageError("备份文件大小超出限制")

    try:
        with zipfile.ZipFile(resolved) as archive:
            names = archive.namelist()
            if MANIFEST_NAME not in names:
                raise BackupPackageError("备份文件缺少清单")
            if len([name for name in names if name and not name.endswith("/")]) > MAX_FILE_COUNT + 1:
                raise BackupPackageError("备份文件数量超出限制")
            _validate_zip_infos(archive.infolist())
            manifest = parse_manifest(archive.read(MANIFEST_NAME))
            _validate_manifest(manifest, current_schema_version=current_schema_version)
            _validate_zip_manifest_match(archive, manifest)
            return ValidatedBackupPackage(package_path=resolved, manifest=manifest, total_size_bytes=size)
    except BackupPackageError:
        raise
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupPackageError("备份文件结构无效") from exc


def extract_validated_package(package_path: Path, *, destination_dir: Path, current_schema_version: str) -> tuple[ValidatedBackupPackage, tuple[Path, ...]]:
    validated = validate_backup_package(package_path, current_schema_version=current_schema_version)
    destination = destination_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(validated.package_path) as archive:
        for item in validated.manifest.files:
            archive_path = _safe_archive_path(item.path)
            target = _contained_path(destination, archive_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(archive_path) as source, target.open("wb") as output:
                output.write(source.read(MAX_ENTRY_BYTES + 1))
            if target.stat().st_size != item.size_bytes or _sha256_file(target) != item.sha256:
                raise BackupPackageError("备份文件内容校验失败")
            extracted.append(target)
    return validated, tuple(extracted)


def stage_validated_package(package_path: Path, *, current_schema_version: str) -> tuple[ValidatedBackupPackage, Path, TemporaryDirectory[str]]:
    temporary = TemporaryDirectory(prefix="restore_stage_")
    stage_dir = Path(temporary.name)
    try:
        validated, _ = extract_validated_package(
            package_path,
            destination_dir=stage_dir,
            current_schema_version=current_schema_version,
        )
        return validated, stage_dir, temporary
    except Exception:
        temporary.cleanup()
        raise


def parse_manifest(data: bytes) -> BackupManifest:
    if len(data) > 1024 * 1024:
        raise BackupPackageError("备份清单过大")
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise BackupPackageError("备份清单格式无效")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise BackupPackageError("备份清单缺少文件列表")
    files: list[ManifestFile] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise BackupPackageError("备份清单文件项无效")
        files.append(
            ManifestFile(
                path=_safe_archive_path(str(item.get("path", ""))),
                kind=_file_kind(str(item.get("kind", ""))),
                size_bytes=_size(item.get("size_bytes")),
                sha256=_sha256_text(str(item.get("sha256", ""))),
            )
        )
    return BackupManifest(
        manifest_version=_manifest_version(payload.get("manifest_version")),
        app_version=_text(payload.get("app_version"), 40, "app_version"),
        created_at=_iso_datetime(payload.get("created_at")),
        schema_version=_schema_version(str(payload.get("schema_version", ""))),
        include_license=_bool(payload.get("include_license"), "include_license"),
        files=tuple(files),
    )


def relative_restore_path(archive_path: str) -> Path:
    return Path(_safe_archive_path(archive_path))


def _manifest_file(source: PackageSource) -> ManifestFile:
    archive_path = _safe_archive_path(source.archive_path)
    kind = _file_kind(source.kind)
    if not source.source_path.is_file():
        raise BackupPackageError("备份源文件不可读取")
    size = source.source_path.stat().st_size
    if size <= 0 or size > MAX_ENTRY_BYTES:
        raise BackupPackageError("备份源文件大小超出限制")
    return ManifestFile(path=archive_path, kind=kind, size_bytes=size, sha256=_sha256_file(source.source_path))


def _validate_manifest(manifest: BackupManifest, *, current_schema_version: str) -> None:
    if manifest.manifest_version != MANIFEST_VERSION:
        raise BackupPackageError("备份清单版本不兼容")
    if not _schema_compatible(manifest.schema_version, current_schema_version):
        raise BackupPackageError("数据库结构版本不兼容")
    if manifest.include_license:
        raise BackupPackageError("备份包含授权文件，当前版本不支持恢复")
    seen: set[str] = set()
    for item in manifest.files:
        if item.path in seen:
            raise BackupPackageError("备份清单包含重复文件")
        seen.add(item.path)
        top = PurePosixPath(item.path).parts[0]
        if top not in ALLOWED_TOP_LEVELS:
            raise BackupPackageError("备份清单包含非法目录")
        if "license" in item.path.lower():
            raise BackupPackageError("备份包含未确认的授权文件")
    if not any(item.kind == "database" and item.path == "db/app.sqlite3" for item in manifest.files):
        raise BackupPackageError("备份缺少数据库文件")


def _validate_zip_manifest_match(archive: zipfile.ZipFile, manifest: BackupManifest) -> None:
    manifest_paths = {item.path: item for item in manifest.files}
    names = {name for name in archive.namelist() if name and not name.endswith("/")}
    expected = set(manifest_paths) | {MANIFEST_NAME}
    if names != expected:
        raise BackupPackageError("备份文件清单不一致")
    for path, item in manifest_paths.items():
        with archive.open(path) as member:
            data = member.read(MAX_ENTRY_BYTES + 1)
        if len(data) != item.size_bytes:
            raise BackupPackageError("备份文件大小校验失败")
        if len(data) > MAX_ENTRY_BYTES:
            raise BackupPackageError("备份文件内容超出限制")
        if hashlib.sha256(data).hexdigest() != item.sha256:
            raise BackupPackageError("备份文件哈希校验失败")


def _validate_zip_infos(infos: Iterable[zipfile.ZipInfo]) -> None:
    total = 0
    for info in infos:
        if not info.filename or info.filename.endswith("/"):
            continue
        if info.filename != MANIFEST_NAME:
            _safe_archive_path(info.filename)
        total += int(info.file_size)
        if info.file_size < 0 or info.file_size > MAX_ENTRY_BYTES or total > MAX_BACKUP_BYTES:
            raise BackupPackageError("备份文件内容超出限制")


def _safe_archive_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BackupPackageError("备份文件路径无效")
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if path.is_absolute() or normalized.startswith("../") or "/../" in normalized or ".." in path.parts:
        raise BackupPackageError("备份文件包含非法路径")
    if Path(normalized).drive or os.path.isabs(normalized):
        raise BackupPackageError("备份文件包含非法路径")
    if len(path.parts) < 2 or path.parts[0] not in ALLOWED_TOP_LEVELS:
        raise BackupPackageError("备份文件目录无效")
    if len(normalized) > 260:
        raise BackupPackageError("备份文件路径过长")
    return path.as_posix()


def _contained_path(root: Path, archive_path: str) -> Path:
    candidate = (root / Path(archive_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        # Archive paths are untrusted even after manifest parsing; keep the final
        # extraction target under the staging directory as a last containment check.
        raise BackupPackageError("备份文件包含非法路径") from exc
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_compatible(backup_version: str, current_version: str) -> bool:
    return int(_schema_version(backup_version)) <= int(_schema_version(current_version))


def _schema_version(value: str) -> str:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 8:
        raise BackupPackageError("数据库结构版本无效")
    return value.zfill(4)


def _manifest_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BackupPackageError("备份清单版本无效")
    return value


def _file_kind(value: str) -> str:
    if value not in ALLOWED_FILE_KINDS:
        raise BackupPackageError("备份文件类型无效")
    return value


def _size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > MAX_ENTRY_BYTES:
        raise BackupPackageError("备份文件大小无效")
    return value


def _sha256_text(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not all(ch in "0123456789abcdef" for ch in value):
        raise BackupPackageError("备份文件哈希无效")
    return value


def _text(value: object, max_length: int, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise BackupPackageError(f"{field}:格式无效")
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


def _iso_datetime(value: object) -> str:
    text = _text(value, 80, "created_at")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise BackupPackageError("备份创建时间无效") from exc
    return text


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise BackupPackageError(f"{field}:格式无效")
    return value
