from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CustomerLicenseRecord:
    id: str
    customer_name: str
    machine_code: str
    license_type: str
    issued_at: str
    expires_at: str | None
    note: str
    authorization_code: str


class CustomerLicenseStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_registry_path()

    def list_records(self) -> tuple[CustomerLicenseRecord, ...]:
        data = self._load()
        records = []
        for item in data:
            try:
                records.append(
                    CustomerLicenseRecord(
                        id=str(item["id"]),
                        customer_name=str(item.get("customer_name") or "未命名客户"),
                        machine_code=str(item["machine_code"]),
                        license_type=str(item.get("license_type") or "permanent"),
                        issued_at=str(item.get("issued_at") or ""),
                        expires_at=str(item["expires_at"]) if item.get("expires_at") else None,
                        note=str(item.get("note") or ""),
                        authorization_code=str(item["authorization_code"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(sorted(records, key=lambda item: item.issued_at, reverse=True))

    def save_record(
        self,
        *,
        customer_name: str,
        machine_code: str,
        license_type: str,
        issued_at: str,
        expires_at: str | None,
        note: str,
        authorization_code: str,
    ) -> CustomerLicenseRecord:
        records = list(self.list_records())
        existing_index = next((index for index, item in enumerate(records) if item.machine_code == machine_code), None)
        record = CustomerLicenseRecord(
            id=records[existing_index].id if existing_index is not None else uuid.uuid4().hex,
            customer_name=customer_name or "未命名客户",
            machine_code=machine_code,
            license_type=license_type,
            issued_at=issued_at,
            expires_at=expires_at,
            note=note,
            authorization_code=authorization_code,
        )
        if existing_index is None:
            records.append(record)
        else:
            records[existing_index] = record
        self._save(records)
        return record

    def _load(self) -> list[dict[str, object]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _save(self, records: list[CustomerLicenseRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(record) for record in records]
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def default_registry_path() -> Path:
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / "KunlianGasLicenseKeygen" / "customers.json"
    return Path.home() / ".kunlian_gas_license_keygen" / "customers.json"
