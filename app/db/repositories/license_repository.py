from __future__ import annotations

from app.db.repositories.base import EntityRepository


class LicenseRepository(EntityRepository):
    table_name = "license_info"
    allowed_sort_columns = frozenset({"id", "status", "updated_at", "activated_at", "expires_at"})
    default_sort = "updated_at"

    def get_current(self):
        return self.fetch_one("SELECT * FROM license_info WHERE id = 1")

    def save_current(
        self,
        *,
        machine_fingerprint_hash: str,
        license_payload: str,
        authorization_signature: str,
        integrity_signature: str,
        status: str,
        activated_at: str,
        expires_at: str | None,
        updated_at: str,
    ) -> None:
        self.execute(
            """
            INSERT INTO license_info(
                id, machine_fingerprint_hash, license_payload, authorization_signature,
                integrity_signature, status, activated_at, expires_at, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                machine_fingerprint_hash = excluded.machine_fingerprint_hash,
                license_payload = excluded.license_payload,
                authorization_signature = excluded.authorization_signature,
                integrity_signature = excluded.integrity_signature,
                status = excluded.status,
                activated_at = excluded.activated_at,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (
                machine_fingerprint_hash,
                license_payload,
                authorization_signature,
                integrity_signature,
                status,
                activated_at,
                expires_at,
                updated_at,
            ),
        )
