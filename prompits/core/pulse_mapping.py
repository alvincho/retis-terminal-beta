"""Bounded declarative projections and explicitly installed mapping methods.

A projection reads rows from a reply (``execution.rows``, at most 50,000), keeps the rows its ``filter`` allows,
optionally ``pivot``s them, maps at most 10,000 rows through ``fields`` and finally applies ``order``. Every path may be
dotted text or a list of literal segments (see ``path_value``)."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
from zoneinfo import ZoneInfo

from prompits.core.capabilities import digest, version

OPERATIONS = {"number", "integer", "string", "scale", "enum", "timestamp", "suffix", "multiply", "date"}
MAX_ZIP = 100
MAX_READ = 50_000
MAX_ROWS = 10_000
MAX_NAMES = 500
PATH = re.compile(r"^[\w -]+(?:\.[\w -]+)*$", re.UNICODE)
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
SCALARS = (str, int, float, bool, type(None))
# A thousands separator also covers its typographic forms: no-break spaces, a right single quote.
SEPARATORS = {",": (",",), " ": (" ", " ", " "), "'": ("'", "’"), "_": ("_",)}
UNITS = {"s": 1, "ms": 1000, "us": 1_000_000}
DATE_FORMATS = ("iso", "compact", "roc")
DIRECTIONS = ("ascending", "descending")
OPERATORS = {"add": (2, 8), "subtract": (2, 2), "multiply": (2, 8), "divide": (2, 2), "negate": (1, 1), "abs": (1, 1)}
SHAPES = ("zip", "entries", "columns", "tuples", "values", "each", "unpivot")
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[Tt ](?:[01]\d|2[0-3]):[0-5]\d(?::(?:[0-5]\d|60)(?:[.,]\d{1,9})?)?"
                      r"(?:[Zz]|[+-](?:[01]\d|2[0-3])(?::?[0-5]\d)?)?)?")
ROC_DATE = re.compile(r"(\d{1,3})([/-])(\d{1,2})\2(\d{1,2})")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def path_value(value, path):
    """Read a dotted path such as "bids.0.price" (a numeric segment selects a list position), or a list of 1 to 12
    segments read literally, one per level, such as ["Time Series (Daily)", "2024-11-01", "1. open"]: a name of 1 to
    200 characters selects an object key and a non-negative int selects a list position."""
    if path in ("", None):
        return value
    if isinstance(path, list):
        if not 1 <= len(path) <= 12 or not all(type(key) is int and key >= 0 or isinstance(key, str) and 1 <= len(key) <= 200
                                               for key in path):
            raise ValueError("A segment path lists one to 12 names of up to 200 characters or list positions.")
        for key in path:
            if type(key) is int:
                value = value[key] if isinstance(value, list) and key < len(value) else None
            else:
                value = value.get(key) if isinstance(value, dict) else None
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


def needs_path(path, message):
    if path in ("", None):
        raise ValueError(message)
    path_value({}, path)


def given(path):
    """A path as the mapping gave it, for lineage."""
    return list(path) if isinstance(path, list) else path


def path_text(path):
    """A pivot key's column name: dotted text as written, a segment list joined with "."."""
    return ".".join(str(key) for key in path) if isinstance(path, list) else path


def plain(name):
    return isinstance(name, str) and NAME.fullmatch(name) is not None


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def same(value, allowed):
    # Booleans never equal numbers here, unlike Python's True == 1.
    return any(type(value) is type(v) and value == v for v in allowed)


def check_values(condition, message):
    values = [condition["equals"]] if "equals" in condition else condition["in"]
    if not isinstance(values, list) or not 1 <= len(values) <= 20 or any(not isinstance(v, SCALARS) for v in values):
        raise ValueError(message)


def validate_case(case):
    """A case has an optional condition on a row path or a request parameter, one path or constant, and its own
    optional steps, applied before the field's."""
    if (not isinstance(case, dict) or set(case) - {"when", "path", "constant", "steps"}
            or sum(key in case for key in ("path", "constant")) != 1):
        raise ValueError("A case declares an optional condition and exactly one path or constant.")
    if "path" in case:
        path_value({}, case["path"])
    if "steps" in case:
        validate_steps(case["steps"])
    when = case.get("when")
    if when is None:
        return
    if (not isinstance(when, dict) or set(when) - {"path", "parameter", "equals", "in"}
            or sum(key in when for key in ("equals", "in")) != 1):
        raise ValueError("A case condition compares one path with a value or a list of values.")
    if sum(key in when for key in ("path", "parameter")) != 1:
        raise ValueError("A case condition names the data path it tests.")
    needs_path(when.get("path", when.get("parameter")), "A case condition names the data path it tests.")
    check_values(when, "Case conditions compare with one to 20 plain values.")


def matches(row, when, parameters=None):
    if when is None:
        return True
    value = path_value(row, when["path"]) if "path" in when else path_value(parameters or {}, when["parameter"])
    return same(value, [when["equals"]] if "equals" in when else when["in"])


def validate_filter(conditions):
    """A row is kept only when every condition holds: {"path", "equals"} or {"path", "in"} compare strictly, like a
    case condition; {"path", "parameter"} compares the row value with a request parameter as stripped text, ignoring
    case (a missing parameter keeps no row)."""
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 4:
        raise ValueError("A row filter lists one to four conditions.")
    for condition in conditions:
        if (not isinstance(condition, dict) or set(condition) - {"path", "equals", "in", "parameter"}
                or sum(key in condition for key in ("equals", "in", "parameter")) != 1):
            raise ValueError("A row filter condition compares one path with a value, a list of values or a request parameter.")
        needs_path(condition.get("path"), "A row filter condition names the data path it tests.")
        if "parameter" in condition:
            needs_path(condition["parameter"], "A row filter condition names the request parameter it compares.")
        else:
            check_values(condition, "Row filters compare with one to 20 plain values.")


def kept(row, conditions, parameters):
    if not isinstance(row, dict):
        raise ValueError("Mapping rows must be objects.")
    for condition in conditions:
        value = path_value(row, condition["path"])
        if "parameter" in condition:
            wanted = path_value(parameters, condition["parameter"])
            if not (isinstance(value, (str, int, float)) and isinstance(wanted, (str, int, float))
                    and str(value).strip().casefold() == str(wanted).strip().casefold()):
                return False
        elif not same(value, [condition["equals"]] if "equals" in condition else condition["in"]):
            return False
    return True


def check_named(paths, message):
    for name, path in paths.items():
        if not plain(name):
            raise ValueError(message)
        path_value({}, path)


def validate_rows(rows):
    """Rows are one path ("" is the reply itself), or one of these shapes; row names are plain names:
    - {"zip": {name: path, ...}, "keep": {name: path, ...}}: parallel lists of objects read position by position, so
      row i is {"position": i + 1, name: list[i], ...} (a missing item is {}), such as a book's bids and asks by depth;
      "keep" copies values from outside the lists (the reply's update time, say) into every row.
    - {"entries": path, "key": name}: each entry of a map; an object value becomes {**value, name: key}, any other
      value {name: key, "value": value}.
    - {"columns": {name: path, ...}, "keep": {...}}: parallel lists of any items; row i is
      {"position": i + 1, **kept, name: list[i] or None, ...}.
    - {"tuples": path, "names": [name, ...]}: a list of lists; row {name_k: item[k] or None}.
    - {"values": path, "name": name}: a list of plain values; row {name: item}.
    - {"each": path, "then": path, "keep": {...}}: each object of the list at "then" (read from each object of the list
      at "each") is a row {**child, **kept}, kept values read from the parent.
    - {"unpivot": path, "columns": [key, ...] | "except": [key, ...], "keep": {...}, "name": n, "value": v}: the object
      (or each object of a list) gives one row {n: key, v: value, **kept} per listed key present in it, in the listed
      order, or per key not excepted, in the object's order; kept values are read from that object."""
    if not isinstance(rows, dict):
        path_value({}, rows)
        return
    shapes = [key for key in SHAPES if key in rows and not (key == "columns" and "unpivot" in rows)]
    if len(shapes) != 1:
        raise ValueError("Rows take one shape: zip, entries, columns, tuples, values, each or unpivot.")
    shape, kept_paths = shapes[0], rows.get("keep", {})
    if shape == "zip":
        lists = rows.get("zip")
        if (set(rows) - {"zip", "keep"} or not isinstance(lists, dict) or not 1 <= len(lists) <= 4 or not isinstance(kept_paths, dict)
                or len(kept_paths) > 8 or "position" in lists or "position" in kept_paths or set(lists) & set(kept_paths)):
            raise ValueError("Zipped rows name one to four lists, and at most eight kept values, by distinct names.")
        check_named({**lists, **kept_paths}, "Zipped row names are plain names.")
        return
    allowed = {"entries": {"entries", "key"}, "columns": {"columns", "keep"}, "tuples": {"tuples", "names"},
               "values": {"values", "name"}, "each": {"each", "then", "keep"},
               "unpivot": {"unpivot", "columns", "except", "keep", "name", "value"}}[shape]
    if set(rows) - allowed:
        raise ValueError(f"Unknown option for {shape} rows.")
    if not isinstance(kept_paths, dict) or len(kept_paths) > 8:
        raise ValueError("Rows keep at most eight values.")
    check_named(kept_paths, "Row names are plain names.")
    if shape != "columns":
        path_value({}, rows[shape])
    if shape == "entries" and (not plain(rows.get("key")) or rows["key"] == "value"):
        raise ValueError("Entry rows name the entry key with a plain name other than value.")
    if shape == "columns":
        lists = rows["columns"]
        if not isinstance(lists, dict) or not 1 <= len(lists) <= 24 or "position" in lists or "position" in kept_paths or set(lists) & set(kept_paths):
            raise ValueError("Column rows name one to 24 lists, and at most eight kept values, by distinct names.")
        check_named(lists, "Row names are plain names.")
    if shape == "tuples":
        names = rows.get("names")
        if not isinstance(names, list) or not 1 <= len(names) <= 24 or not all(plain(n) for n in names) or len(set(names)) != len(names):
            raise ValueError("Tuple rows name each position: one to 24 distinct plain names.")
    if shape == "values" and not plain(rows.get("name")):
        raise ValueError("Value rows name their items with a plain name.")
    if shape == "each":
        if "then" not in rows:
            raise ValueError("Nested rows name the list inside each item.")
        path_value({}, rows["then"])
    if shape == "unpivot":
        mode = [key for key in ("columns", "except") if key in rows]
        name, value = rows.get("name"), rows.get("value")
        if len(mode) != 1 or not plain(name) or not plain(value) or name == value or {name, value} & set(kept_paths):
            raise ValueError("Unpivoted rows list columns or exceptions, and name the column and value by distinct plain names.")
        listed_keys = rows[mode[0]]
        low, high = (1, 200) if mode[0] == "columns" else (0, 50)
        if (not isinstance(listed_keys, list) or not low <= len(listed_keys) <= high
                or any(not isinstance(key, str) or not 1 <= len(key) <= 200 for key in listed_keys) or len(set(listed_keys)) != len(listed_keys)):
            raise ValueError("Unpivoted rows list one to 200 distinct columns, or up to 50 excepted keys.")


def listed(data):
    return data if isinstance(data, list) else [data] if isinstance(data, dict) else []


def zipped(payload, rows):
    columns = {name: path_value(payload, path) for name, path in rows["zip"].items()}
    columns = {name: value if isinstance(value, list) else [] for name, value in columns.items()}
    kept_values = {name: copy.deepcopy(path_value(payload, path)) for name, path in rows.get("keep", {}).items()}
    length = max((len(value) for value in columns.values()), default=0)
    if length > MAX_ZIP:
        raise ValueError("Zipped lists exceed 100 positions.")
    return [{"position": index + 1, **kept_values, **{name: value[index] if index < len(value) and isinstance(value[index], dict) else {}
                                                      for name, value in columns.items()}} for index in range(length)]


def keep_from(source, rows):
    return {name: copy.deepcopy(path_value(source, path)) for name, path in rows.get("keep", {}).items()}


def read_entries(payload, rows):
    data, name = path_value(payload, rows["entries"]), rows["key"]
    for key, value in (data.items() if isinstance(data, dict) else ()):
        yield {**value, name: key} if isinstance(value, dict) else {name: key, "value": value}


def read_columns(payload, rows):
    lists = {name: path_value(payload, path) for name, path in rows["columns"].items()}
    lists = {name: value if isinstance(value, list) else [] for name, value in lists.items()}
    kept_values = keep_from(payload, rows)
    length = max((len(value) for value in lists.values()), default=0)
    if length > MAX_READ:
        raise ValueError("Column lists exceed 50,000 positions.")
    for index in range(length):
        yield {"position": index + 1, **kept_values, **{name: value[index] if index < len(value) else None for name, value in lists.items()}}


def read_tuples(payload, rows):
    for item in listed(path_value(payload, rows["tuples"])):
        if isinstance(item, list):
            yield {name: item[k] if k < len(item) else None for k, name in enumerate(rows["names"])}


def read_values(payload, rows):
    for item in listed(path_value(payload, rows["values"])):
        if isinstance(item, SCALARS):
            yield {rows["name"]: item}


def read_each(payload, rows):
    for parent in listed(path_value(payload, rows["each"])):
        if isinstance(parent, dict):
            kept_values = keep_from(parent, rows)
            for child in listed(path_value(parent, rows["then"])):
                if isinstance(child, dict):
                    yield {**child, **kept_values}


def read_unpivot(payload, rows):
    name, value = rows["name"], rows["value"]
    for item in listed(path_value(payload, rows["unpivot"])):
        if isinstance(item, dict):
            kept_values = keep_from(item, rows)
            if "columns" in rows:
                keys = [key for key in rows["columns"] if key in item]
            else:
                skip = set(rows["except"])
                keys = [key for key in item if key not in skip]
            for key in keys:
                yield {name: key, value: item[key], **kept_values}


READERS = {"entries": read_entries, "columns": read_columns, "tuples": read_tuples, "values": read_values,
           "each": read_each, "unpivot": read_unpivot}


def read_rows(payload, rows):
    if not isinstance(rows, dict):
        return listed(path_value(payload, rows))
    if "zip" in rows:
        return zipped(payload, rows)
    out = []
    for row in READERS["unpivot" if "unpivot" in rows else next(key for key in SHAPES if key in rows)](payload, rows):
        out.append(row)
        if len(out) > MAX_READ:
            raise ValueError("Mapping input exceeds 50,000 rows.")
    return out


def validate_pivot(pivot):
    """{"key": [path, ...], "name": path, "value": path}: rows sharing key values (first-seen order) become one row
    holding each key value under path_text(path) and str(name): value for each of them (a later name overwrites;
    a name never replaces a key value; a missing or structured name is skipped)."""
    if not isinstance(pivot, dict) or set(pivot) != {"key", "name", "value"} or not isinstance(pivot["key"], list) or not 1 <= len(pivot["key"]) <= 4:
        raise ValueError("A pivot groups rows by one to four key paths and names a name path and a value path.")
    for path in (*pivot["key"], pivot["name"], pivot["value"]):
        needs_path(path, "Pivot paths are not empty.")
    texts = [path_text(path) for path in pivot["key"]]
    if len(set(texts)) != len(texts):
        raise ValueError("Pivot keys are distinct.")


def pivoted(rows, pivot):
    texts, groups, names = [path_text(path) for path in pivot["key"]], {}, set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Mapping rows must be objects.")
        keys = [path_value(row, path) for path in pivot["key"]]
        group = groups.setdefault(json.dumps(keys, sort_keys=True, default=str), dict(zip(texts, keys)))
        name = path_value(row, pivot["name"])
        if name is None or isinstance(name, (dict, list)):
            continue
        names.add(str(name))
        if len(names) > MAX_NAMES:
            raise ValueError("A pivot spreads at most 500 distinct names.")
        if str(name) not in texts:
            group[str(name)] = path_value(row, pivot["value"])
    return list(groups.values())


def check_number_options(options):
    thousands, percent = options.get("thousands"), options.get("percent", False)
    if "thousands" in options and not (isinstance(thousands, str) and thousands in SEPARATORS) or type(percent) is not bool:
        raise ValueError('Number options take a thousands separator of ",", " ", "\'" or "_" and a true or false percent.')


def zone(name):
    try:
        if isinstance(name, str) and 0 < len(name) <= 64:
            return ZoneInfo(name)
    except (KeyError, ValueError, OSError):
        pass
    raise ValueError("A date from an epoch number needs a known IANA timezone.")


def check_date(step):
    if step.get("format", "iso") not in DATE_FORMATS:
        raise ValueError("Unknown date format.")
    if "unit" not in step:
        if "timezone" in step:
            raise ValueError("A date step takes a timezone only with an epoch unit.")
        return
    if step["unit"] not in ("s", "ms", "us"):
        raise ValueError("Unknown date unit.")
    if "format" in step:
        raise ValueError("A date step reads either formatted text or an epoch number.")
    zone(step.get("timezone"))


def validate_steps(steps):
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
        if step["op"] == "suffix" and (not isinstance(step.get("parameter"), (str, list)) or not isinstance(step.get("values"), dict)
                                       or not 1 <= len(step["values"]) <= 20
                                       or any(not isinstance(v, str) or len(v) > 16 for v in step["values"].values())):
            raise ValueError("A suffix step names a request parameter and a bounded suffix for each of its values.")
        if step["op"] == "suffix":
            path_value({}, step["parameter"])
        if step["op"] in ("number", "integer"):
            check_number_options(step)
        if step["op"] == "multiply" and not (finite(step.get("factor")) and step["factor"] != 0):
            raise ValueError("A multiply step needs a finite non-zero factor.")
        if step["op"] == "date":
            check_date(step)


def check_expr(expr, depth=0):
    """Return the node count of a computed expression: a number, {"path": p}, {"parameter": p} or [op, expr, ...]."""
    if isinstance(expr, dict):
        if len(expr) != 1 or not set(expr) <= {"path", "parameter"}:
            raise ValueError('A computed operand is {"path": p}, {"parameter": p} or a number.')
        needs_path(next(iter(expr.values())), "A computed operand names its path.")
        return 1
    if isinstance(expr, list):
        if not expr or not isinstance(expr[0], str) or expr[0] not in OPERATORS:
            raise ValueError("Computed operations are add, subtract, multiply, divide, negate and abs.")
        low, high = OPERATORS[expr[0]]
        if not low <= len(expr) - 1 <= high:
            raise ValueError(f"A computed {expr[0]} takes {low}{'' if low == high else f' to {high}'} operands.")
        if depth >= 4:
            raise ValueError("Computed fields nest at most four operations.")
        return 1 + sum(check_expr(item, depth + 1) for item in expr[1:])
    if not finite(expr):
        raise ValueError('A computed operand is {"path": p}, {"parameter": p} or a finite number.')
    return 1


def check_setting(value, ok, message):
    if isinstance(value, dict):
        if set(value) != {"parameter"}:
            raise ValueError(message)
        needs_path(value["parameter"], message)
    elif not ok(value):
        raise ValueError(message)


def validate_order(order, fields):
    """{"by": field, "direction": "ascending" | "descending", "limit": n, "rank": name}, each optional; by, direction
    and limit may be {"parameter": p}. An absent parameter keeps the default (reply order, ascending, no limit)."""
    if not isinstance(order, dict) or not order or set(order) - {"by", "direction", "limit", "rank"}:
        raise ValueError("An order names a field to sort by, a direction, a limit or a rank.")
    if "by" in order:
        check_setting(order["by"], lambda v: isinstance(v, str) and v in fields, "An order sorts by a mapped field or a request parameter naming one.")
    if "direction" in order:
        if "by" not in order:
            raise ValueError("An order direction needs a field to sort by.")
        check_setting(order["direction"], lambda v: v in DIRECTIONS, "An order direction is ascending, descending or a request parameter.")
    if "limit" in order:
        check_setting(order["limit"], lambda v: type(v) is int and 1 <= v <= MAX_ROWS, "An order limit is 1 to 10,000 rows or a request parameter.")
    if "rank" in order and not (plain(order["rank"]) and order["rank"] not in fields):
        raise ValueError("A rank is a plain name no mapped field uses.")


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
    validate_rows(execution.get("rows", ""))
    fields = execution.get("fields")
    if not isinstance(fields, dict) or not fields or len(fields) > 100:
        raise ValueError("Projection requires one to 100 fields.")
    for name, field in fields.items():
        if not isinstance(name, str) or not name or not isinstance(field, dict):
            raise ValueError("Invalid mapping field.")
        if sum(key in field for key in ("path", "parameter", "constant", "coalesce", "cases", "compute")) != 1:
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
        if "compute" in field and check_expr(field["compute"]) > 16:
            raise ValueError("A computed field has at most 16 nodes.")
        if "number" in field:
            if "compute" not in field or not isinstance(field["number"], dict) or set(field["number"]) - {"thousands", "percent"}:
                raise ValueError("Number options belong to a computed field and name only thousands and percent.")
            check_number_options(field["number"])
        if "missing" in field:
            markers = field["missing"]
            if (not isinstance(markers, list) or not 1 <= len(markers) <= 10
                    or any(not isinstance(m, SCALARS) or isinstance(m, str) and len(m) > 200 for m in markers)):
                raise ValueError("Missing markers are one to ten plain values.")
        validate_steps(field.get("steps", []))
    if "filter" in execution:
        validate_filter(execution["filter"])
    if "pivot" in execution:
        validate_pivot(execution["pivot"])
    if "order" in execution:
        validate_order(execution["order"], fields)


def to_number(value, thousands=None, percent=False):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric observation.")
    if isinstance(value, str) and percent:
        value = value.strip()
        value = value[:-1] if value.endswith(("%", "％")) else value
    if isinstance(value, str) and thousands:
        for mark in SEPARATORS[thousands]:
            value = value.replace(mark, "")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite numeric observation.")
    return value


def to_date(value, step):
    """An ISO calendar date YYYY-MM-DD, never an invented instant."""
    if "unit" in step:
        if isinstance(value, (bool, dict, list)):
            raise ValueError("Invalid epoch date.")
        seconds = float(value) / UNITS[step["unit"]]
        if not math.isfinite(seconds):
            raise ValueError("Invalid epoch date.")
        return (EPOCH + timedelta(seconds=seconds)).astimezone(zone(step["timezone"])).date().isoformat()
    form = step.get("format", "iso")
    if isinstance(value, bool) or not isinstance(value, (str, int)) or isinstance(value, int) and form == "iso":
        raise ValueError("A date is read from text.")
    text = str(value).strip()
    match = ISO_DATE.fullmatch(text) if form == "iso" else ROC_DATE.fullmatch(text) if form == "roc" else None
    if form == "iso" and match:
        year, month, day = (int(part) for part in match.group(1, 2, 3))
    elif form == "compact" and re.fullmatch(r"\d{8}", text):
        year, month, day = int(text[:4]), int(text[4:6]), int(text[6:])
    elif form == "roc" and (match or re.fullmatch(r"\d{6,7}", text)):
        # The Minguo calendar counts years from 1912 (year 1), so its year plus 1911 is the common era year.
        year, month, day = (int(part) for part in (match.group(1, 3, 4) if match else (text[:-4], text[-4:-2], text[-2:])))
        if year < 1:
            raise ValueError("Not a roc date.")
        year += 1911
    else:
        raise ValueError(f"Not a {form} date.")
    return date(year, month, day).isoformat()


def convert(value, step, row, parameters=None):
    op = step["op"]
    if op == "suffix":
        # The request parameter picks the suffix (a market code picks .TW or .TWO); an unknown one is refused.
        key = path_value(parameters or {}, step["parameter"])
        if isinstance(value, (dict, list, bool)) or str(key) not in step["values"]:
            raise ValueError("No suffix is declared for this request.")
        return str(value) + step["values"][str(key)]
    if op in ("number", "integer", "scale"):
        value = to_number(value) if op == "scale" else to_number(value, step.get("thousands"), step.get("percent", False))
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
    if op == "multiply":
        value = to_number(value) * step["factor"]
        if not math.isfinite(value):
            raise ValueError("Conversion overflow.")
        return value
    if op == "date":
        return to_date(value, step)
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
            instant = datetime.fromtimestamp(float(value) / UNITS[unit], timezone.utc)
        return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    raise ValueError("Unknown conversion operation.")


def marked(value, markers):
    """A missing marker: equal text after stripping, an equal number, or another equal plain value of the same type."""
    if isinstance(value, str):
        return any(isinstance(m, str) and m.strip() == value.strip() for m in markers)
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return any((type(m) is type(value) or number and isinstance(m, (int, float)) and not isinstance(m, bool)) and m == value
               for m in markers)


def references(expr):
    if isinstance(expr, dict):
        yield expr
    elif isinstance(expr, list):
        for item in expr[1:]:
            yield from references(item)


def calculate(expr, numbers):
    if isinstance(expr, dict):
        return next(numbers)
    if not isinstance(expr, list):
        return float(expr)
    op, args = expr[0], [calculate(item, numbers) for item in expr[1:]]
    if op == "add":
        return sum(args)
    if op == "multiply":
        return math.prod(args)
    if op == "subtract":
        return args[0] - args[1]
    if op == "divide":
        if args[1] == 0:
            raise ValueError("Division by zero.")
        return args[0] / args[1]
    return -args[0] if op == "negate" else abs(args[0])


def computed(field, operands):
    options = field.get("number", {})
    numbers = []
    for value in operands:
        if isinstance(value, (dict, list)):
            raise ValueError("Computed operands must be numbers.")
        numbers.append(to_number(value, options.get("thousands"), options.get("percent", False)))
    result = calculate(field["compute"], iter(numbers))
    if not math.isfinite(result):
        raise ValueError("Non-finite computed value.")
    return result


def field_input(field, row, parameters):
    """Return (value, sourcePath, case steps). A value of None, or one of the field's missing markers, is missing; a
    computed field's value is the list of its operands, or None when any is missing."""
    markers = field.get("missing")

    def found(value):
        return None if value is None or markers is not None and marked(value, markers) else value

    if "compute" in field:
        refs = list(references(field["compute"]))
        operands = [path_value(row, ref["path"]) if "path" in ref else path_value(parameters, ref["parameter"]) for ref in refs]
        sources = []
        for ref in refs:
            if "path" in ref and ref["path"] not in sources:
                sources.append(given(ref["path"]))
        return (None if any(found(value) is None for value in operands) else operands), sources, []
    if "coalesce" in field:
        path = next((p for p in field["coalesce"] if found(path_value(row, p)) is not None), None)
        return (None if path is None else path_value(row, path)), given(path), []
    if "cases" in field:
        # The first case whose condition holds decides; no later case is consulted.
        case = next((c for c in field["cases"] if matches(row, c.get("when"), parameters)), None)
        if case is None:
            return None, None, []
        if "constant" in case:
            return copy.deepcopy(case["constant"]), None, case.get("steps", [])
        return found(path_value(row, case["path"])), given(case["path"]), case.get("steps", [])
    if "constant" in field:
        return copy.deepcopy(field["constant"]), None, []
    if "parameter" in field:
        return found(path_value(parameters, field["parameter"])), None, []
    return found(path_value(row, field["path"])), given(field["path"]), []


def map_row(fields, row, parameters, index, issues):
    mapped, lineage = {}, {}
    for name, field in fields.items():
        value, source_path, case_steps = field_input(field, row, parameters)
        if value is None:
            lineage[name] = {"status": "missing", "sourcePath": source_path}
            continue
        steps = [*case_steps, *field.get("steps", [])]
        try:
            if "compute" in field:
                value = computed(field, value)
            raw = copy.deepcopy(value)
            for step in steps:
                value = convert(value, step, row, parameters)
            mapped[name] = value
            lineage[name] = {"status": "available", "sourcePath": source_path, "rawValue": raw, "steps": copy.deepcopy(steps)}
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            lineage[name] = {"status": "invalid", "sourcePath": source_path, "reason": str(exc)}
            issues.append({"row": index, "field": name, "reason": str(exc)})
    return mapped, lineage


def setting(value, parameters):
    return path_value(parameters, value["parameter"]) if isinstance(value, dict) else value


def rankable(value):
    return isinstance(value, str) or finite(value)


def ordered(order, fields, output, lineage, issues, parameters):
    """Sort (stably), limit and rank the mapped rows; lineage and issue row numbers follow them."""
    by, direction, limit = (setting(order.get(key), parameters) for key in ("by", "direction", "limit"))
    if by is not None:
        by = by.strip() if isinstance(by, str) else by
        if not isinstance(by, str) or by not in fields:
            raise ValueError("The order names an unknown field.")
    direction = "ascending" if direction is None else direction.strip().lower() if isinstance(direction, str) else direction
    if direction not in DIRECTIONS:
        raise ValueError("Unknown order direction.")
    if isinstance(limit, str) and re.fullmatch(r"[0-9]{1,5}", limit.strip()):
        limit = int(limit.strip())
    if limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_ROWS):
        raise ValueError("The order limit must be a whole number from 1 to 10,000.")
    positions = list(range(len(output)))
    if by is not None:
        # Rows without a number or text for the field cannot be ranked; numbers sort before text.
        positions = sorted((i for i in positions if rankable(output[i].get(by))), reverse=direction == "descending",
                           key=lambda i: (1, output[i][by]) if isinstance(output[i][by], str) else (0, output[i][by]))
    positions = positions[:limit]
    moved = {old: new for new, old in enumerate(positions)}
    output, lineage = [output[i] for i in positions], [lineage[i] for i in positions]
    if "rank" in order:
        for number, (row, fields_lineage) in enumerate(zip(output, lineage), 1):
            row[order["rank"]] = number
            fields_lineage[order["rank"]] = {"status": "available", "sourcePath": None}
    issues = sorted(({**issue, "row": moved[issue["row"]]} for issue in issues if issue["row"] in moved), key=lambda issue: issue["row"])
    return output, lineage, issues


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
    rows = read_rows(payload, execution.get("rows", ""))
    if len(rows) > MAX_READ:
        raise ValueError("Mapping input exceeds 50,000 rows.")
    if "filter" in execution:
        rows = [row for row in rows if kept(row, execution["filter"], parameters)]
    if "pivot" in execution:
        rows = pivoted(rows, execution["pivot"])
    if len(rows) > MAX_ROWS:
        raise ValueError("Mapping input exceeds 10,000 rows.")
    output, lineage, issues = [], [], []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("Mapping rows must be objects.")
        mapped, fields = map_row(execution["fields"], row, parameters, index, issues)
        output.append(mapped); lineage.append(fields)
    if "order" in execution:
        output, lineage, issues = ordered(execution["order"], execution["fields"], output, lineage, issues, parameters)
    return {"rows": output, "lineage": lineage, "issues": issues, "mappingDigest": digest(mapping)}
