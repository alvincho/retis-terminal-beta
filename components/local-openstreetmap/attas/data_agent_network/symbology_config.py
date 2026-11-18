"""Mandatory source-owned identifier declarations, separate from conversion Pulses."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACT = "source-symbology/1"
TYPE = {"type": "string", "pattern": r"^(?:[A-Z][A-Z0-9_]*|[a-z][a-z0-9_.-]*:[a-zA-Z0-9_.-]+)$"}
SCHEMA = json.loads(Path(__file__).with_name("symbology.schema.json").read_text())
PUBLIC_SCHEMA = {
    **copy.deepcopy(SCHEMA),
    "properties": {
        **copy.deepcopy(SCHEMA["properties"]),
        "supported_types": {"type": "array", "items": TYPE, "uniqueItems": True},
        "verification": {"const": "declared"},
    },
    "required": [*SCHEMA["required"], "supported_types", "verification"],
}
CATALOG = Path(__file__).with_name("source_symbology.json")


def declarations():
    return json.loads(CATALOG.read_text())


def validate(value):
    if not isinstance(value, dict) or list(Draft202012Validator(SCHEMA).iter_errors(value)):
        raise ValueError("Configure source_profile.symbology with supported input/output types and evidence.")
    result = copy.deepcopy(value)
    types = result["input_types"] + result["output_types"]
    if (result["status"] == "supported") != bool(types):
        raise ValueError("Supported symbology needs identifiers; non-applicable/delegated declarations must be empty.")
    return result


def configured(source_id, profile, *, default=None):
    # Built-in defaults are explicit, reviewed package configuration. An absent
    # custom declaration never means that every identifier is accepted.
    value = profile.get("symbology") if "symbology" in profile else declarations().get(source_id, default)
    if value is None:
        raise ValueError("Source " + source_id + " must configure source_profile.symbology.")
    return validate(value)


def advertised(value):
    return {**copy.deepcopy(value), "supported_types": sorted(set(value["input_types"] + value["output_types"])),
            "verification": "declared"}
