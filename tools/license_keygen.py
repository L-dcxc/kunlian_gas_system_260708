from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.license_codes import DEFAULT_LICENSE_SIGNING_KEY, build_authorization_code

SIGNING_KEY_ENV = "GAS_ALARM_LICENSE_SIGNING_KEY"
MACHINE_CODE_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        signing_key = _load_signing_key(args)
        machine_code = normalize_machine_code(args.machine_code)
        expires_at = normalize_expires_at(args.expires_at)
        payload = build_license_payload(
            machine_code=machine_code,
            expires_at=expires_at,
            customer_name=args.customer_name,
            note=args.note,
        )
        authorization_code = build_authorization_code(payload, signing_key)
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(authorization_code + "\n", encoding="utf-8")
            print(f"授权文件已生成：{output}")
        else:
            print("授权码：")
            print(authorization_code)
        print(f"机器码：{mask_machine_code(machine_code)}")
        print(f"授权类型：{'期限授权' if expires_at else '永久授权'}")
        if expires_at:
            print(f"到期时间：{expires_at}")
        return 0
    except ValueError as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 2
    except OSError:
        print("生成失败：授权文件无法写入，请检查输出目录权限。", file=sys.stderr)
        return 1


def build_license_payload(
    *,
    machine_code: str,
    expires_at: str | None = None,
    customer_name: str | None = None,
    note: str | None = None,
) -> dict[str, str]:
    payload = {
        "machine_fingerprint_hash": normalize_machine_code(machine_code),
        "issued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "license_type": "term" if expires_at else "permanent",
    }
    if expires_at:
        payload["expires_at"] = normalize_expires_at(expires_at)
    if customer_name:
        payload["customer_name"] = _safe_optional_text(customer_name, "客户名称")
    if note:
        payload["note"] = _safe_optional_text(note, "备注")
    return payload


def normalize_machine_code(value: str) -> str:
    normalized = "".join(str(value or "").split()).lower()
    if not MACHINE_CODE_RE.fullmatch(normalized):
        raise ValueError("机器码必须是 64 位十六进制哈希，请从客户授权窗口复制完整机器码。")
    return normalized


def normalize_expires_at(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T23:59:59+08:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("到期时间必须是 YYYY-MM-DD 或 ISO 时间格式。") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def mask_machine_code(value: str) -> str:
    machine_code = normalize_machine_code(value)
    return f"{machine_code[:8]}...{machine_code[-4:]}"


def _load_signing_key(args: argparse.Namespace) -> bytes:
    values = [bool(args.signing_key), bool(args.signing_key_file)]
    if sum(values) > 1:
        raise ValueError("签名密钥只能通过 --signing-key 或 --signing-key-file 指定一种。")
    if args.signing_key:
        key = str(args.signing_key)
    elif args.signing_key_file:
        try:
            key = Path(args.signing_key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("签名密钥文件不可读取。") from exc
    else:
        key = os.environ.get(SIGNING_KEY_ENV, "")
    if not key:
        return DEFAULT_LICENSE_SIGNING_KEY
    return key.encode("utf-8")


def _safe_optional_text(value: str, field_name: str) -> str:
    text = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    if len(text) > 120:
        raise ValueError(f"{field_name}不能超过 120 个字符。")
    return text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="气体安全报警监控系统授权码生成工具")
    parser.add_argument("--machine-code", required=True, help="客户授权窗口复制出的完整机器码。")
    parser.add_argument("--expires-at", help="授权到期时间，支持 YYYY-MM-DD 或 ISO 时间；不填为永久授权。")
    parser.add_argument("--customer-name", help="客户名称，会写入授权载荷用于内部核对。")
    parser.add_argument("--note", help="备注，会写入授权载荷用于内部核对。")
    parser.add_argument("--output", help="输出 .lic 文件路径；不填则打印授权码。")
    parser.add_argument("--signing-key", help="覆盖默认产品签名密钥，仅供内部调试。")
    parser.add_argument("--signing-key-file", help="覆盖默认产品签名密钥文件路径，仅供内部调试。")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
