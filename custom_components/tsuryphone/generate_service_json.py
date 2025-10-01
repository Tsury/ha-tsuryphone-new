"""Utility script to extract service translations from services.yaml."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(__file__).resolve().parent
SERVICES_YAML = ROOT / "services.yaml"
TRANSLATIONS_FILE = ROOT / "translations" / "en.json"


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _stringify(value: Any) -> str:
    """Convert translation values to strings suitable for Home Assistant."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _build_translations(services: Dict[str, Any]) -> Dict[str, Any]:
    translations: Dict[str, Any] = {}
    for service_key, service_data in sorted(services.items()):
        if not isinstance(service_data, dict):
            continue

        entry: Dict[str, Any] = {}
        for key in ("name", "description", "response"):
            if key in service_data:
                entry[key] = _stringify(service_data[key])

        fields = service_data.get("fields")
        if isinstance(fields, dict) and fields:
            translated_fields: Dict[str, Any] = {}
            for field_key, field_data in sorted(fields.items()):
                if not isinstance(field_data, dict):
                    continue
                field_entry: Dict[str, Any] = {}
                for key in ("name", "description", "example"):
                    if key in field_data:
                        field_entry[key] = _stringify(field_data[key])
                if field_entry:
                    translated_fields[field_key] = field_entry
            if translated_fields:
                entry["fields"] = translated_fields

        if entry:
            translations[service_key] = entry
    return translations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Update translations/en.json with the generated services block",
    )
    args = parser.parse_args()

    services = _load_yaml(SERVICES_YAML)
    translations = _build_translations(services)
    if args.write:
        translations_path = TRANSLATIONS_FILE
        with translations_path.open("r", encoding="utf-8") as handle:
            existing = json.load(handle)
        existing["services"] = translations
        with translations_path.open("w", encoding="utf-8") as handle:
            json.dump(existing, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    else:
        print(json.dumps(translations, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
