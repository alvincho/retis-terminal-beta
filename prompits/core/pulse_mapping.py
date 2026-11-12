"""Bounded declarative projections and explicitly installed mapping methods."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import math
import re
from zoneinfo import ZoneInfo

from prompits.core.capabilities import digest, version

OPERATIONS = {"number", "integer", "string", "scale", "enum", "timestamp"}
PATH = re.compile(r"^[\w -]+(?:\.[\w -]+)*$", re.UNICODE)


def path_value(value, path):
    if path in ("", None):
        return value
    if not isinstance(path, str) or len(path) > 300 or not PATH.fullmatch(path):
        raise ValueError("Use a bounded dotted data path.")
    for key in path.split("."):
        # A numeric segment selects a list position, such as "bids.0.price".
        if isinstance(value, list) and key.isdigit():
            index = int(key)
            value = value[index] if index < len(value) else None
            continue
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


SCALARS = (str, int, float, bool, type(None))


def validate_case(case):
    if not isinstance(case, dict) or set(case) - {"when", "path", "constant"} or sum(key in case for key in ("path", "constant")) != 1:
        raise ValueError("A case declares an optional condition and exactly one path or constant.")
    if "path" in case:
        path_value({}, case["path"])
    when = case.get("when")
    if when is None:
        return
    if not isinstance(when, dict) or set(when) - {"path", "equals", "in"} or sum(key in when for key in ("equals", "in")) != 1:
        raise ValueError("A case condition compares one path with a value or a list of values.")
    if not isinstance(when.get("path"), str) or not when["path"]:
        raise ValueError("A case condition names the data path it tests.")
    path_value({}, when["path"])
    values = [when["equals"]] if "equals" in when else when["in"]
    if not isinstance(values, list) or not 1 <= len(values) <= 20 or any(not isinstance(v, SCALARS) for v in values):
        raise ValueError("Case conditions compare with one to 20 plain values.")


def matches(row, when):
    if when is None:
        return True
    value = path_value(row, when["path"])
    allowed = [when["equals"]] if "equals" in when else when["in"]
    # Booleans never equal numbers here, unlike Python's True == 1.
    return any(type(value) is type(v) and value == v for v in allowed)


def validate_execution(execution):
    if not isinstance(execution, dict) or execution.get("kind") not in ("projection", "registered"):
        raise ValueError("Mapping execution must name a projection or installed method.")
    if execution["kind"] == "registered":
        if not re.fullmatch(r"[a-z][a-z0-9._:-]{1,127}", str(execution.get("method", ""))):
            raise ValueError("Mapping requires a registered method ID.")
        version(execution.get("version"))
        if not re.fullmatch(r"[0-9a-f]{64}", str(execution.get("implementationDigest", ""))):
            raise ValueError("Registered methods require an exact implementation digest.")
        return
    path_value({}, execution.get("rows", ""))
    fields = execution.get("fields")
    if not isinstance(fields, dict) or not fields or len(fields) > 100:
        raise ValueError("Projection requires one to 100 fields.")
    for name, field in fields.items():
        if not isinstance(name, str) or not name or not isinstance(field, dict):
            raise ValueError("Invalid mapping field.")
        if sum(key in field for key in ("path", "parameter", "constant", "coalesce", "cases")) != 1:
            raise ValueError("A field must declare exactly one input.")
        if "cases" in field:
            cases = field["cases"]
            if not isinstance(cases, list) or not 1 <= len(cases) <= 8:
                raise ValueError("A field selects from one to eight cases.")
            for case in cases:
                validate_case(case)
        if "path" in field:
            path_value({}, field["path"])
        if "coalesce" in field:
            paths = field["coalesce"]
            if not isinstance(paths, list) or not paths or len(paths) > 8:
                raise ValueError("Coalescing is limited to eight explicit paths.")
            for path in paths:
                path_value({}, path)
        if "parameter" in field:
            path_value({}, field["parameter"])
        steps = field.get("steps", [])
        if not isinstance(steps, list) or len(steps) > 8:
            raise ValueError("At most eight conversion steps per field.")
        for step in steps:
            if not isinstance(step, dict) or step.get("op") not in OPERATIONS:
                raise ValueError("Unknown mapping conversion operation.")
            if step["op"] == "scale":
                factor = step.get("factor")
                if type(factor) not in (int, float) or not math.isfinite(factor) or factor <= 0 or not step.get("fromUnit") or not step.get("toUnit") or not step.get("unitPath"):
                    raise ValueError("Unit conversion requires a finite positive factor and explicit source unit path.")
                path_value({}, step["unitPath"])
            if step["op"] == "enum" and (not isinstance(step.get("values"), dict) or len(step["values"]) > 100):
                raise ValueError("Enum conversion needs a bounded explicit vocabulary.")
            if step["op"] == "timestamp" and step.get("unit", "iso") not in ("iso", "s", "ms", "us"):
                raise ValueError("Unknown timestamp unit.")


def convert(value, step, row):
    op = step["op"]
    if op in ("number", "integer", "scale"):
        if isinstance(value, bool):
            raise ValueError("Boolean is not a numeric observation.")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Non-finite numeric observation.")
        if op == "integer":
            if not value.is_integer():
                raise ValueError("Fractional observation cannot be truncated.")
            return int(value)
        if op == "scale":
            if path_value(row, step["unitPath"]) != step["fromUnit"]:
                raise ValueError("Source unit is unknown or incompatible.")
            value *= step["factor"]
            if not math.isfinite(value):
                raise ValueError("Conversion overflow.")
        return value
    if op == "string":
        if isinstance(value, (dict, list)):
            raise ValueError("Structured data cannot be cast to a label.")
        return str(value)
    if op == "enum":
        if str(value) not in step["values"]:
            raise ValueError("Unknown source enumeration.")
        return step["values"][str(value)]
    if op == "timestamp":
        unit = step.get("unit", "iso")
        if unit == "iso":
            instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if instant.tzinfo is None:
                if not step.get("timezone"):
                    raise ValueError("Source timezone is required for a local timestamp.")
                instant = instant.replace(tzinfo=ZoneInfo(step["timezone"]))
        else:
            if isinstance(value, bool):
                raise ValueError("Invalid timestamp.")
            instant = datetime.fromtimestamp(float(value) / {"s": 1, "ms": 1000, "us": 1_000_000}[unit], timezone.utc)
        return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise ValueError("Unknown conversion operation.")


def execute_mapping(mapping, payload, parameters=None, *, methods=None):
    """Return per-field missing/error states without manufacturing values."""
    execution = mapping["execution"]
    validate_execution(execution)
    parameters = parameters or {}
    if execution["kind"] == "registered":
        key = (execution["method"], execution["version"], execution["implementationDigest"])
        method = (methods or {}).get(key)
        if method is None:
            raise ValueError("Exact mapping implementation is not installed.")
        return method(copy.deepcopy(payload), copy.deepcopy(parameters))
    data = path_value(payload, execution.get("rows", ""))
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    if len(rows) > 10_000:
        raise ValueError("Mapping input exceeds 10,000 rows.")
    output, lineage, issues = [], [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("Mapping rows must be objects.")
        mapped, fields = {}, {}
        for name, field in execution["fields"].items():
            source_path = field.get("path")
            if "coalesce" in field:
                source_path = next((p for p in field["coalesce"] if path_value(row, p) is not None), None)
                value = path_value(row, source_path) if source_path is not None else None
            elif "cases" in field:
                # The first case whose condition holds decides; no later case is consulted.
                case = next((c for c in field["cases"] if matches(row, c.get("when"))), None)
                source_path = case.get("path") if case else None
                value = None if case is None else copy.deepcopy(case["constant"]) if "constant" in case else path_value(row, source_path)
            elif "constant" in field:
                value = copy.deepcopy(field["constant"])
            elif "parameter" in field:
                value = path_value(parameters, field["parameter"])
            else:
                value = path_value(row, source_path)
            if value is None:
                fields[name] = {"status": "missing", "sourcePath": source_path}
                continue
            raw = copy.deepcopy(value)
            try:
                for step in field.get("steps", []):
                    value = convert(value, step, row)
                mapped[name] = value
                fields[name] = {"status": "available", "sourcePath": source_path, "rawValue": raw,
                                "steps": copy.deepcopy(field.get("steps", []))}
            except (ValueError, TypeError, OverflowError, KeyError) as exc:
                fields[name] = {"status": "invalid", "sourcePath": source_path, "reason": str(exc)}
                issues.append({"row": index, "field": name, "reason": str(exc)})
        output.append(mapped); lineage.append(fields)
    return {"rows": output, "lineage": lineage, "issues": issues, "mappingDigest": digest(mapping)}
