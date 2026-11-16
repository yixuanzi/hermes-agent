#!/usr/bin/env python3
"""Resolve a Feishu/Lark tenant user_id from an app-scoped open_id.

The script deliberately never prints credential values or access tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

# Hermes profiles are isolated under HERMES_HOME.  Falling back to ~/.hermes
# preserves standalone use outside a Hermes-managed process.
DEFAULT_HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
DEFAULT_ENV_FILE = DEFAULT_HERMES_HOME / ".env"
DOMAIN_ALIASES = {
    "lark": "https://open.larksuite.com",
    "feishu": "https://open.feishu.cn",
}


def parse_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"Credential file not found: {path}")

    values: dict[str, str] = {}
    pattern = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        match = pattern.match(raw_line)
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def open_platform_base(domain: str) -> str:
    normalized = domain.strip().rstrip("/")
    base = DOMAIN_ALIASES.get(normalized.lower(), normalized)
    if not base.startswith(("https://", "http://")):
        raise ValueError("FEISHU_DOMAIN must be `lark`, `feishu`, or an https:// Open Platform URL")
    return base


def emit(payload: dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    print(f"status: {payload['status']}")
    print(f"open_id: {payload.get('open_id', '(unknown)')}")
    if payload.get("name"):
        print(f"name: {payload['name']}")
    if payload.get("user_id"):
        print(f"user_id: {payload['user_id']}")
    if payload.get("message"):
        print(f"message: {payload['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve a Feishu/Lark user_id from an open_id.")
    parser.add_argument("--open-id", required=True, help="Target app-scoped Open ID, normally beginning with ou_")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help=f"Credential .env file (default: {DEFAULT_ENV_FILE})")
    parser.add_argument("--domain", help="Override FEISHU_DOMAIN for this request")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="Output format")
    args = parser.parse_args()

    try:
        values = parse_dotenv(Path(args.env_file))
        app_id = values.get("FEISHU_APP_ID")
        app_secret = values.get("FEISHU_APP_SECRET")
        if not app_id or not app_secret:
            raise ValueError("FEISHU_APP_ID or FEISHU_APP_SECRET is missing")
        base = open_platform_base(args.domain or values.get("FEISHU_DOMAIN", "feishu"))

        token_response = requests.post(
            f"{base}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=20,
        )
        token_response.raise_for_status()
        token_body = token_response.json()
        if token_body.get("code") != 0:
            raise RuntimeError(f"Tenant-token request failed: {token_body.get('msg', 'unknown API error')}")
        access_token = token_body["tenant_access_token"]

        user_response = requests.get(
            f"{base}/open-apis/contact/v3/users/{quote(args.open_id, safe='')}?user_id_type=open_id",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        user_response.raise_for_status()
        user_body = user_response.json()
        if user_body.get("code") != 0:
            raise RuntimeError(f"User lookup failed: {user_body.get('msg', 'unknown API error')}")

        user = user_body.get("data", {}).get("user", {})
        result = {
            "status": "resolved" if user.get("user_id") else "user_id_not_returned",
            "open_id": user.get("open_id", args.open_id),
            "name": user.get("name"),
            "user_id": user.get("user_id"),
        }
        if not user.get("user_id"):
            result["message"] = (
                "The user was found, but user_id was not returned. Enable and publish the self-built-app "
                "field permission '获取用户 user ID', and verify contact-directory data scope."
            )
            emit(result, args.format)
            return 3

        emit(result, args.format)
        return 0
    except (OSError, ValueError, requests.RequestException, RuntimeError, KeyError) as error:
        emit(
            {
                "status": "error",
                "open_id": args.open_id,
                "message": str(error),
            },
            args.format,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())