"""Parsers that normalize vendor documentation into endpoint specifications."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping as MappingABC
import html
from html.parser import HTMLParser
import inspect
import json
import re
from typing import Any, Literal, Mapping, Tuple, get_args, get_origin
from urllib.parse import parse_qs, urljoin, urlparse

from .connections import provider_endpoint_is_executable
from .financial import concise_financial_description
from .models import EndpointParameterSpec, EndpointSpec, VendorSpecSource


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return " ".join(self.parts)


def _html_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(value or "")
    return " ".join(parser.text().split())


def _json_type(annotation: Any, default: Any = inspect.Parameter.empty) -> Mapping[str, Any]:
    if annotation is inspect.Parameter.empty:
        annotation = type(default) if default is not inspect.Parameter.empty and default is not None else str
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        values = [_json_default(item) for item in args]
        value_types = {type(item) for item in values if item is not None}
        schema: dict[str, Any] = {"enum": values}
        if value_types == {bool}:
            schema["type"] = "boolean"
        elif value_types and value_types <= {int}:
            schema["type"] = "integer"
        elif value_types and value_types <= {int, float}:
            schema["type"] = "number"
        else:
            schema["type"] = "string"
        return schema
    if origin is list or origin is tuple or origin is set:
        return {"type": "array", "items": _json_type(args[0]) if args else {}}
    if origin is dict or origin is Mapping or origin is MappingABC:
        return {"type": "object"}
    if origin is not None and args:
        non_none = [item for item in args if item is not type(None)]
        if len(non_none) == 1:
            return _json_type(non_none[0], default)
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is dict or annotation is Mapping or annotation is MappingABC:
        return {"type": "object"}
    return {"type": "string"}


def _json_default(value: Any) -> Any:
    if value is inspect.Parameter.empty:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_json_default(item) for item in value]
    if isinstance(value, MappingABC):
        return {str(key): _json_default(item) for key, item in value.items()}
    return str(value)


def _signature_parameters(callable_value: Any, *, synthetic: tuple[EndpointParameterSpec, ...] = ()) -> tuple[EndpointParameterSpec, ...]:
    try:
        signature = inspect.signature(callable_value)
    except (TypeError, ValueError):
        return synthetic
    parameters: list[EndpointParameterSpec] = list(synthetic)
    synthetic_names = {item.name for item in synthetic}
    for parameter in signature.parameters.values():
        if parameter.name in {"self", "cls", "session"} or parameter.name in synthetic_names:
            continue
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue
        required = parameter.default is inspect.Parameter.empty
        default = None if required else _json_default(parameter.default)
        schema = dict(_json_type(parameter.annotation, parameter.default))
        parameters.append(
            EndpointParameterSpec(
                name=parameter.name,
                required=required,
                schema=schema,
                default=default,
            )
        )
    return tuple(parameters)


class AbstractSpecParser(ABC):
    """Convert documentation text into normalized endpoint specifications."""

    VERSION = "2"

    @abstractmethod
    def supports(self, source: VendorSpecSource) -> bool:
        """Return whether this parser supports the source vendor."""

    @abstractmethod
    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        """Parse all documented operations found in the source."""


class FinnhubSpecParser(AbstractSpecParser):
    """Normalize Finnhub's official public Swagger document."""

    VERSION = "2"
    _HTTP_METHODS = ("get", "post")
    _SECRET_PARAMETERS = {"token", "api_key", "apikey"}

    @staticmethod
    def _resolve_reference(value: Any, document: Mapping[str, Any]) -> Any:
        """Resolve one local OpenAPI reference while preserving sibling fields."""
        if not isinstance(value, Mapping):
            return value
        reference = str(value.get("$ref") or "")
        if not reference.startswith("#/"):
            return value
        target: Any = document
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, Mapping) or part not in target:
                return value
            target = target[part]
        if not isinstance(target, Mapping):
            return target
        return {**dict(target), **{key: item for key, item in value.items() if key != "$ref"}}

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == "finnhub"

    @staticmethod
    def _resolve_schema(
        schema: Any,
        document: Mapping[str, Any],
        *,
        resolving: tuple[str, ...] = (),
    ) -> Mapping[str, Any]:
        if not isinstance(schema, Mapping):
            return {}
        raw = dict(schema)
        reference = str(raw.pop("$ref", "") or "")
        resolved: dict[str, Any] = {}
        if reference:
            if reference in resolving or not reference.startswith("#/"):
                return {"type": "object", "additionalProperties": True}
            target: Any = document
            for raw_part in reference[2:].split("/"):
                part = raw_part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, Mapping) or part not in target:
                    target = None
                    break
                target = target[part]
            if isinstance(target, Mapping):
                resolved.update(
                    FinnhubSpecParser._resolve_schema(
                        target,
                        document,
                        resolving=(*resolving, reference),
                    )
                )

        all_of = raw.pop("allOf", ())
        for member in all_of if isinstance(all_of, list) else ():
            member_schema = FinnhubSpecParser._resolve_schema(member, document, resolving=resolving)
            member_properties = member_schema.pop("properties", None)
            if isinstance(member_properties, Mapping):
                resolved.setdefault("properties", {}).update(member_properties)
            member_required = member_schema.pop("required", None)
            if isinstance(member_required, list):
                resolved["required"] = list(
                    dict.fromkeys([*(resolved.get("required") or []), *member_required])
                )
            resolved.update(member_schema)

        resolved.update(raw)
        properties = resolved.get("properties")
        if isinstance(properties, Mapping):
            resolved["properties"] = {
                str(name): FinnhubSpecParser._resolve_schema(value, document, resolving=resolving)
                for name, value in properties.items()
            }
        if isinstance(resolved.get("items"), Mapping):
            resolved["items"] = FinnhubSpecParser._resolve_schema(
                resolved["items"], document, resolving=resolving
            )
        additional = resolved.get("additionalProperties")
        if isinstance(additional, Mapping):
            resolved["additionalProperties"] = FinnhubSpecParser._resolve_schema(
                additional, document, resolving=resolving
            )
        return resolved

    @classmethod
    def _parameter_specs(
        cls,
        parameters: Any,
        document: Mapping[str, Any],
    ) -> tuple[EndpointParameterSpec, ...]:
        normalized: list[EndpointParameterSpec] = []
        for raw_parameter in parameters if isinstance(parameters, list) else ():
            if not isinstance(raw_parameter, Mapping):
                continue
            parameter = dict(cls._resolve_reference(raw_parameter, document))
            name = str(parameter.get("name") or "").strip()
            if not name or name.lower() in cls._SECRET_PARAMETERS:
                continue
            location = str(parameter.get("in") or "query")
            raw_schema = parameter.get("schema")
            if not isinstance(raw_schema, Mapping):
                raw_schema = {
                    key: parameter[key]
                    for key in (
                        "type",
                        "format",
                        "enum",
                        "items",
                        "minimum",
                        "maximum",
                        "minLength",
                        "maxLength",
                        "pattern",
                    )
                    if key in parameter
                }
            schema = cls._resolve_schema(raw_schema, document)
            default = parameter.get("default", schema.get("default"))
            normalized.append(
                EndpointParameterSpec(
                    name=name,
                    location=location,
                    description=concise_financial_description(
                        _html_text(str(parameter.get("description") or "")),
                        limit=500,
                    ),
                    required=bool(parameter.get("required")),
                    schema=schema,
                    default=default,
                )
            )
        return tuple(normalized)

    @classmethod
    def _response_schema(
        cls,
        responses: Any,
        document: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], tuple[str, ...]]:
        if isinstance(responses, Mapping):
            for status in sorted(responses, key=str):
                if not str(status).startswith("2"):
                    continue
                response = cls._resolve_reference(responses[status], document)
                if isinstance(response, Mapping) and isinstance(response.get("schema"), Mapping):
                    schema = cls._resolve_schema(response["schema"], document)
                    if schema:
                        return schema, ()
        return (
            {"type": "object", "additionalProperties": True},
            ("The official Swagger operation does not publish a structured success response schema.",),
        )

    @staticmethod
    def _cost(operation: Mapping[str, Any]) -> Mapping[str, Any]:
        premium = bool(operation.get("premium"))
        free_tier = bool(operation.get("freeTier"))
        if premium:
            model = "premium_subscription"
            provider_cost = "Premium Finnhub endpoint entitlement is required."
        elif free_tier:
            model = "free_or_subscription"
            provider_cost = "Finnhub documents free-tier access, subject to provider limits."
        else:
            model = "provider_terms"
            provider_cost = "Access depends on the user's Finnhub plan and endpoint entitlement."
        return {
            "model": model,
            "amount": 0,
            "currency": "USD",
            "unit": "request",
            "provider_cost": provider_cost,
            "notes": [],
        }

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if not self.supports(source):
            raise ValueError(f"FinnhubSpecParser does not support vendor {source.vendor_id!r}")
        try:
            document = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Finnhub specification is not valid JSON") from exc
        if not isinstance(document, Mapping) or str(document.get("swagger")) != "2.0":
            raise ValueError("Finnhub specification must be a Swagger 2.0 document")
        paths = document.get("paths")
        if not isinstance(paths, Mapping):
            raise ValueError("Finnhub specification does not contain paths")

        endpoints: list[EndpointSpec] = []
        seen_ids: set[str] = set()
        for path in sorted(paths):
            path_item = paths[path]
            if not isinstance(path_item, Mapping):
                continue
            shared_parameters = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
            for method in self._HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, Mapping):
                    continue
                operation_id = str(operation.get("operationId") or f"{method}-{path}").strip()
                slug = re.sub(r"[^a-z0-9]+", "_", operation_id.lower()).strip("_")
                endpoint_id = f"finnhub.{slug}"
                if endpoint_id in seen_ids:
                    raise ValueError(f"Duplicate Finnhub endpoint identifier: {endpoint_id}")
                seen_ids.add(endpoint_id)
                schema, schema_warnings = self._response_schema(operation.get("responses"), document)
                description = concise_financial_description(
                    _html_text(str(operation.get("description") or operation.get("summary") or operation_id)),
                    limit=1_000,
                )
                url_id = str(operation.get("urlId") or slug).strip()
                quality_notes = list(schema_warnings)
                if operation.get("highUsage"):
                    quality_notes.append("Finnhub marks this endpoint as high usage.")
                endpoints.append(
                    EndpointSpec(
                        endpoint_id=endpoint_id,
                        vendor_id="finnhub",
                        name=str(operation.get("summary") or operation.get("navHeader") or operation_id),
                        description=description,
                        category=str(operation.get("section") or "Finnhub API"),
                        transport="https",
                        operation=operation_id,
                        parameters=self._parameter_specs(
                            [*shared_parameters, *(operation.get("parameters") or [])], document
                        ),
                        response_schema=schema,
                        authentication={
                            "scheme": "delegated_connection",
                            "provider_scheme": "api_key",
                            "required": True,
                            "credential_reference": "connection_id",
                            "header": "X-Finnhub-Token",
                        },
                        cost=self._cost(operation),
                        quality={"status": "provider_documented", "notes": quality_notes},
                        documentation_url=f"https://finnhub.io/docs/api/{url_id}",
                        executable=True,
                        extensions={
                            "attas.schema_provenance": "official_swagger",
                            "attas.premium": bool(operation.get("premium")),
                            "attas.vendor_version": source.vendor_version or str((document.get("info") or {}).get("version") or ""),
                            "finnhub.base_url": "https://finnhub.io/api/v1",
                            "finnhub.http_method": method.upper(),
                            "finnhub.path": str(path),
                        },
                    )
                )
        if not endpoints:
            raise ValueError("No Finnhub operations were found in the official Swagger document")
        return tuple(endpoints)


class PublicOpenAPISpecParser(AbstractSpecParser):
    """Normalize callable routes from a provider-owned Swagger/OpenAPI document."""

    VERSION = "2"
    VENDOR_ID = ""
    # Data Sources are read/analytics agents. Provider mutation operations are
    # intentionally excluded even when they appear in a public OpenAPI file.
    _HTTP_METHODS = ("get", "post")
    _SECRET_PARAMETERS = FinnhubSpecParser._SECRET_PARAMETERS | {
        "api-key",
        "api_token",
        "authorization",
        "auth_token",
        "x-api-key",
    }

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == self.VENDOR_ID

    @staticmethod
    def _load_document(content: str) -> Mapping[str, Any]:
        try:
            document = json.loads(content)
        except json.JSONDecodeError:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError("Public OpenAPI specification is neither valid JSON nor YAML") from exc
            try:
                document = yaml.safe_load(content)
            except (yaml.YAMLError, ValueError, TypeError) as exc:
                raise ValueError("Public OpenAPI specification is neither valid JSON nor YAML") from exc
        if not isinstance(document, Mapping):
            raise ValueError("Public OpenAPI specification must be an object")
        if not document.get("openapi") and not document.get("swagger"):
            raise ValueError("Public specification is not an OpenAPI or Swagger document")
        return document

    @classmethod
    def _parameters(
        cls,
        raw_parameters: Any,
        document: Mapping[str, Any],
    ) -> tuple[EndpointParameterSpec, ...]:
        parameters = FinnhubSpecParser._parameter_specs(raw_parameters, document)
        return tuple(item for item in parameters if item.name.lower() not in cls._SECRET_PARAMETERS)

    @staticmethod
    def _success_schema(
        responses: Any,
        document: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], tuple[str, ...]]:
        if isinstance(responses, Mapping):
            for status in sorted(responses, key=str):
                if not str(status).startswith("2"):
                    continue
                response = FinnhubSpecParser._resolve_reference(responses[status], document)
                if not isinstance(response, Mapping):
                    continue
                raw_schema = response.get("schema")
                if not isinstance(raw_schema, Mapping):
                    content = response.get("content")
                    if isinstance(content, Mapping):
                        for media_type in sorted(content):
                            media = content[media_type]
                            if isinstance(media, Mapping) and isinstance(media.get("schema"), Mapping):
                                raw_schema = media["schema"]
                                break
                if isinstance(raw_schema, Mapping):
                    schema = FinnhubSpecParser._resolve_schema(raw_schema, document)
                    if schema:
                        return schema, ()
        return (
            {"type": "object", "additionalProperties": True},
            ("The official OpenAPI operation does not publish a structured success response schema.",),
        )

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if not self.supports(source):
            raise ValueError(f"{self.__class__.__name__} does not support vendor {source.vendor_id!r}")
        document = self._load_document(content)
        raw_paths = document.get("paths")
        if not isinstance(raw_paths, Mapping):
            raise ValueError("Public OpenAPI specification does not contain paths")
        paths = dict(raw_paths)
        document_servers = document.get("servers")
        document_base_url = ""
        if isinstance(document_servers, list):
            for server in document_servers:
                if isinstance(server, Mapping) and str(server.get("url") or "").startswith("https://"):
                    document_base_url = str(server["url"]).rstrip("/")
                    break
        if not document_base_url:
            schemes = document.get("schemes") if isinstance(document.get("schemes"), list) else []
            host = str(document.get("host") or "")
            base_path = str(document.get("basePath") or "")
            if host and (not schemes or "https" in schemes):
                document_base_url = f"https://{host}{base_path}".rstrip("/")
        for declared in source.options.get("declared_endpoints") or ():
            if not isinstance(declared, Mapping):
                continue
            path = str(declared.get("operation") or "").strip()
            method = str(declared.get("method") or "get").strip().lower()
            if not path or method not in self._HTTP_METHODS:
                continue
            path_item = dict(paths.get(path) or {})
            path_item.setdefault(
                method,
                {
                    "operationId": str(declared.get("identity") or f"{method}_{path}"),
                    "summary": str(declared.get("name") or path),
                    "tags": [str(declared.get("category") or "Provider OpenAPI")],
                    "responses": {"200": {"description": "Provider response"}},
                },
            )
            paths[path] = path_item
        auth_required = bool(source.options.get("authentication_required", True))
        endpoints: list[EndpointSpec] = []
        seen: set[str] = set()
        for path in sorted(paths):
            path_item = paths[path]
            if not isinstance(path_item, Mapping):
                continue
            call_path = "/" + str(path).split("?", 1)[0].lstrip("/")
            shared = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
            for method in self._HTTP_METHODS:
                operation = path_item.get(method)
                if not isinstance(operation, Mapping):
                    continue
                identity = str(operation.get("operationId") or f"{method}_{path}")
                slug = re.sub(r"[^a-z0-9]+", "_", identity.lower()).strip("_")
                endpoint_id = f"{source.vendor_id}.{slug}"
                if endpoint_id in seen:
                    endpoint_id = f"{endpoint_id}_{method}"
                seen.add(endpoint_id)
                raw_parameters = [*shared, *(operation.get("parameters") or [])]
                request_body = FinnhubSpecParser._resolve_reference(
                    operation.get("requestBody"), document
                )
                if isinstance(request_body, Mapping):
                    body_content = request_body.get("content")
                    body_schema: Mapping[str, Any] = {}
                    if isinstance(body_content, Mapping):
                        for media_type in sorted(body_content):
                            media = body_content[media_type]
                            if isinstance(media, Mapping) and isinstance(media.get("schema"), Mapping):
                                body_schema = FinnhubSpecParser._resolve_schema(media["schema"], document)
                                break
                    if body_schema:
                        raw_parameters.append(
                            {
                                "name": "body",
                                "in": "body",
                                "required": bool(request_body.get("required")),
                                "description": str(request_body.get("description") or "Request body."),
                                "schema": body_schema,
                            }
                        )
                schema, warnings = self._success_schema(operation.get("responses"), document)
                tags = operation.get("tags") if isinstance(operation.get("tags"), list) else []
                parameter_specs = list(self._parameters(raw_parameters, document))
                parameter_names = {item.name for item in parameter_specs}
                for placeholder in re.findall(r"\{([^}]+)\}", call_path):
                    if placeholder not in parameter_names:
                        parameter_specs.append(
                            EndpointParameterSpec(
                                name=placeholder,
                                location="path",
                                required=True,
                                schema={"type": "string"},
                            )
                        )
                endpoints.append(
                    EndpointSpec(
                        endpoint_id=endpoint_id,
                        vendor_id=source.vendor_id,
                        name=str(operation.get("summary") or operation.get("operationId") or f"{method.upper()} {path}"),
                        description=concise_financial_description(
                            _html_text(str(operation.get("description") or operation.get("summary") or identity)),
                            limit=1_000,
                        ),
                        category=str(tags[0] if tags else "Provider OpenAPI"),
                        transport="https",
                        operation=call_path,
                        parameters=tuple(parameter_specs),
                        response_schema=schema,
                        authentication=(
                            {
                                "scheme": "delegated_connection",
                                "provider_scheme": "provider_defined",
                                "required": True,
                                "credential_reference": "connection_id",
                            }
                            if auth_required
                            else {"scheme": "public_provider", "required": False}
                        ),
                        cost={
                            "model": "provider_terms" if auth_required else "public_access",
                            "amount": 0,
                            "currency": "USD",
                            "unit": "request",
                            "provider_cost": (
                                "Access depends on the user's provider plan and endpoint entitlement."
                                if auth_required
                                else "No provider API key is required; provider access policies still apply."
                            ),
                            "notes": [],
                        },
                        quality={"status": "provider_documented", "notes": list(warnings)},
                        documentation_url=source.location,
                        executable=provider_endpoint_is_executable(source.vendor_id, call_path),
                        extensions={
                            "attas.schema_provenance": "provider_openapi",
                            "attas.specification_only": not provider_endpoint_is_executable(source.vendor_id, call_path),
                            "attas.vendor_version": source.vendor_version or str((document.get("info") or {}).get("version") or ""),
                            "attas.http_method": method.upper(),
                            "attas.base_url": document_base_url,
                        },
                    )
                )
        if not endpoints:
            raise ValueError("No operations were found in the public OpenAPI specification")
        return tuple(endpoints)


class EmbeddedOpenAPISpecParser(PublicOpenAPISpecParser):
    """Parse every ReadMe-hosted OpenAPI document embedded in Markdown pages."""

    VERSION = "1"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        decoder = json.JSONDecoder()
        documents: list[Mapping[str, Any]] = []
        for marker in re.finditer(r"```json\s*", content, flags=re.IGNORECASE):
            try:
                document, _ = decoder.raw_decode(content, marker.end())
            except json.JSONDecodeError:
                continue
            if isinstance(document, Mapping) and (document.get("openapi") or document.get("swagger")):
                documents.append(document)
        if not documents:
            return super().parse(source, content)
        endpoints: dict[str, EndpointSpec] = {}
        for document in documents:
            try:
                parsed = super().parse(source, json.dumps(document))
            except ValueError as exc:
                if "No operations were found" in str(exc):
                    continue
                raise
            for endpoint in parsed:
                method = str(endpoint.extensions.get("attas.http_method") or "GET")
                base_url = str(endpoint.extensions.get("attas.base_url") or "")
                key = f"{method}:{base_url}:{endpoint.operation}"
                if endpoint.endpoint_id in {item.endpoint_id for item in endpoints.values()}:
                    endpoint = EndpointSpec(
                        **{
                            **endpoint.__dict__,
                            "endpoint_id": f"{endpoint.endpoint_id}_{self._server_slug(base_url)}",
                        }
                    )
                endpoints[key] = endpoint
        if not endpoints:
            raise ValueError("No operations were found in embedded public OpenAPI definitions")
        return tuple(endpoints[key] for key in sorted(endpoints))

    @staticmethod
    def _server_slug(base_url: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "_", base_url.lower()).strip("_")
        return value[-80:] or "route"


class RedocEmbeddedOpenAPISpecParser(PublicOpenAPISpecParser):
    """Read an official Redoc page whose complete OpenAPI is serialized in page state."""

    VERSION = "1"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        marker = "const __redoc_state = "
        start = content.find(marker)
        if start < 0:
            return super().parse(source, content)
        decoder = json.JSONDecoder()
        state, _ = decoder.raw_decode(content, start + len(marker))
        document = state.get("spec", {}).get("data") if isinstance(state, Mapping) else None
        if not isinstance(document, Mapping):
            raise ValueError("Redoc page does not contain serialized OpenAPI state")
        return super().parse(source, json.dumps(document))


class PublicFinancialDocsSpecParser(AbstractSpecParser):
    """Extract executable routes and reference entries from approved public API docs."""

    VERSION = "2"
    VENDOR_ID = ""
    _SECRET_PARAMETERS = {"api_key", "apikey", "api-token", "api_token", "token", "auth_token"}
    _URL_RE = re.compile(r"https?://[^\s\"'<>`]+", re.IGNORECASE)
    _PLACEHOLDER_RE = re.compile(r"\{([A-Za-z][A-Za-z0-9_-]*)\}")
    _FRED_RE = re.compile(
        r"<a\b[^>]*\bname=[\"']([^\"']+)[\"'][^>]*>\s*(fred/[A-Za-z0-9_/-]+)\s*</a>\s*(?:<[^>]+>)*\s*-?\s*([^<]+)?",
        re.IGNORECASE,
    )
    _MASSIVE_LINK_RE = re.compile(
        r'"displayText":"([^"]+)"[^{}]{0,500}?"href":"(/rest/[^"]+)"',
        re.IGNORECASE,
    )
    _HTML_LINK_RE = re.compile(
        r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _MARKDOWN_ENDPOINT_RE = re.compile(r"\[\s*(/[^\]\s]+)\s*\]\(", re.IGNORECASE)

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == self.VENDOR_ID

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")

    @classmethod
    def _normalize_path(cls, value: str) -> str:
        path = html.unescape(str(value or "")).strip().split("#", 1)[0]
        path = re.sub(r"<([A-Za-z][A-Za-z0-9_-]*)>", r"{\1}", path)
        path = re.sub(r"CIK#+", "{cik}", path, flags=re.IGNORECASE)
        segments: list[str] = []
        for segment in path.split("/"):
            if re.fullmatch(r"[A-Z]{1,6}(?:,[A-Z]{1,6})+", segment):
                segment = "{symbols}"
            elif re.fullmatch(r"[A-Z]{1,6}(?:\.[A-Z]{1,6})?", segment) and segment not in {"GET", "POST"}:
                segment = "{symbol}"
            elif re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", segment):
                segment = "{date}"
            elif re.fullmatch(r"\d{4,}", segment):
                segment = "{value}"
            segments.append(segment)
        normalized = "/".join(segments)
        return normalized if normalized.startswith("/") else f"/{normalized}"

    @staticmethod
    def _allowed_api_host(host: str, configured: Any) -> bool:
        if not isinstance(configured, (list, tuple, set)):
            return False
        allowed = {
            str(item).strip().lower()
            for item in configured
        }
        normalized = str(host or "").lower()
        return normalized in allowed or any(normalized.endswith(f".{item}") for item in allowed)

    @classmethod
    def _query_parameters(cls, query: str) -> dict[str, EndpointParameterSpec]:
        parameters: dict[str, EndpointParameterSpec] = {}
        for name, values in parse_qs(html.unescape(query), keep_blank_values=True).items():
            if name.lower() in cls._SECRET_PARAMETERS:
                continue
            examples = tuple(
                value
                for value in dict.fromkeys(values)
                if value and not re.search(r"(?:your|demo|secret).*(?:key|token)", value, re.IGNORECASE)
            )[:3]
            parameters[name] = EndpointParameterSpec(
                name=name,
                location="query",
                required=False,
                schema={"type": "string"},
                examples=examples,
            )
        return parameters

    @classmethod
    def _endpoint(
        cls,
        source: VendorSpecSource,
        *,
        identity: str,
        name: str,
        operation: str,
        documentation_url: str,
        parameters: Mapping[str, EndpointParameterSpec] | None = None,
        category: str = "",
        method: str = "GET",
    ) -> EndpointSpec:
        all_parameters = dict(parameters or {})
        for placeholder in cls._PLACEHOLDER_RE.findall(operation):
            all_parameters.setdefault(
                placeholder,
                EndpointParameterSpec(
                    name=placeholder,
                    location="path",
                    required=True,
                    schema={"type": "string"},
                ),
            )
        auth_required = bool(source.options.get("authentication_required", True))
        return EndpointSpec(
            endpoint_id=f"{source.vendor_id}.{cls._slug(identity)}",
            vendor_id=source.vendor_id,
            name=" ".join(str(name or identity).replace("_", " ").replace("-", " ").split()),
            description=source.description or f"Provider-documented {source.vendor_id} API operation.",
            category=category or "Public API documentation",
            transport="https",
            operation=operation,
            parameters=tuple(all_parameters.values()),
            response_schema={"type": "object", "additionalProperties": True},
            authentication=(
                {
                    "scheme": "delegated_connection",
                    "provider_scheme": "api_key",
                    "required": True,
                    "credential_reference": "connection_id",
                }
                if auth_required
                else {"scheme": "public_provider", "required": False}
            ),
            cost={
                "model": "provider_terms" if auth_required else "public_access",
                "amount": 0,
                "currency": "USD",
                "unit": "request",
                "provider_cost": (
                    "Access depends on the user's provider plan and endpoint entitlement."
                    if auth_required
                    else "No provider API key is required; provider access policies still apply."
                ),
                "notes": [],
            },
            quality={
                "status": "provider_documented",
                "notes": [
                    "The public documentation does not expose a complete machine-readable response schema; verify fields against the linked provider page."
                ],
            },
            documentation_url=documentation_url,
            executable=provider_endpoint_is_executable(source.vendor_id, operation),
            extensions={
                "attas.schema_provenance": "public_provider_documentation",
                "attas.specification_only": not provider_endpoint_is_executable(source.vendor_id, operation),
                "attas.vendor_version": source.vendor_version,
                "attas.http_method": str(method or "GET").upper(),
            },
        )

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if not self.supports(source):
            raise ValueError(f"{self.__class__.__name__} does not support vendor {source.vendor_id!r}")
        normalized_content = html.unescape(content).replace('\\"', '"').replace("\\u0026", "&")
        endpoints: dict[str, EndpointSpec] = {}

        for declared in source.options.get("declared_endpoints") or ():
            if not isinstance(declared, Mapping):
                continue
            operation = str(declared.get("operation") or "").strip()
            if not operation:
                continue
            parameters: dict[str, EndpointParameterSpec] = {}
            for raw_parameter in declared.get("parameters") or ():
                if not isinstance(raw_parameter, Mapping):
                    continue
                name = str(raw_parameter.get("name") or "").strip()
                if not name:
                    continue
                examples = raw_parameter.get("examples")
                parameters[name] = EndpointParameterSpec(
                    name=name,
                    location=str(raw_parameter.get("location") or "query"),
                    description=str(raw_parameter.get("description") or ""),
                    required=bool(raw_parameter.get("required", False)),
                    schema=dict(raw_parameter.get("schema") or {"type": "string"}),
                    default=raw_parameter.get("default"),
                    examples=tuple(examples) if isinstance(examples, (list, tuple)) else (),
                )
            endpoint = self._endpoint(
                source,
                identity=str(declared.get("identity") or operation),
                name=str(declared.get("name") or operation),
                operation=operation,
                documentation_url=str(declared.get("documentation_url") or source.location),
                parameters=parameters,
                category=str(declared.get("category") or "Documented API family"),
                method=str(declared.get("method") or "GET"),
            )
            endpoints[endpoint.endpoint_id] = endpoint

        if source.options.get("declared_only"):
            if not endpoints:
                raise ValueError(f"No declared API operations were configured for {source.source_id}")
            return tuple(endpoints.values())

        documented_prefixes = tuple(str(item) for item in source.options.get("documented_endpoint_prefixes") or ())
        if documented_prefixes:
            candidates = set(self._MARKDOWN_ENDPOINT_RE.findall(normalized_content))
            candidates.update(
                re.findall(
                    r"\bGET\s+(/[A-Za-z0-9_{}./-]+)",
                    normalized_content,
                    flags=re.IGNORECASE,
                )
            )
            candidates.update(
                re.findall(
                    r"(?<![A-Za-z0-9_])(/(?:apis/)?v[1-9]/[A-Za-z][A-Za-z0-9_{}./-]+)",
                    normalized_content,
                )
            )
            for raw_path in sorted(candidates):
                path = html.unescape(raw_path).replace("\\_", "_")
                path = re.sub(r"<br\s*/?>", "", path, flags=re.IGNORECASE)
                path = re.sub(r"[),.;:]+$", "", path)
                if not path.startswith(documented_prefixes):
                    continue
                endpoint = self._endpoint(
                    source,
                    identity=path,
                    name=path.strip("/").replace("/", " "),
                    operation=self._normalize_path(path),
                    documentation_url=source.location,
                    category=str(source.options.get("documented_endpoint_category") or "Documented API endpoint"),
                )
                endpoints[endpoint.endpoint_id] = endpoint

        if source.options.get("fred_index"):
            for name, operation, description in self._FRED_RE.findall(normalized_content):
                path = f"/{operation.strip('/')}"
                endpoint = self._endpoint(
                    source,
                    identity=operation,
                    name=operation,
                    operation=path,
                    documentation_url=urljoin(source.location, f"{name}.html"),
                    category="FRED API",
                )
                endpoints[endpoint.endpoint_id] = endpoint

        # Navigation pages are evidence sources, not callable data operations.
        # Only declared routes and concrete provider API URLs become endpoints.

        api_hosts = source.options.get("api_hosts") or ()
        for match in self._URL_RE.finditer(normalized_content):
            raw_url = re.sub(r"CIK#+", "CIK{cik}", match.group(0).rstrip(".,);]}"), flags=re.IGNORECASE)
            try:
                parsed = urlparse(raw_url)
                parsed_host = parsed.hostname or ""
            except ValueError:
                continue
            if not self._allowed_api_host(parsed_host, api_hosts):
                continue
            lower_path = parsed.path.lower()
            if not parsed.path.strip("/") or any(lower_path.endswith(suffix) for suffix in (".css", ".js", ".png", ".jpg", ".svg", ".zip", ".pdf")):
                continue
            if source.vendor_id == "eodhd" and not (lower_path.startswith("/api/") or "eodhistoricaldata" in parsed_host):
                continue
            api_path = self._normalize_path(parsed.path)
            parameters = self._query_parameters(parsed.query)
            identity = api_path
            existing_id = f"{source.vendor_id}.{self._slug(identity)}"
            existing = endpoints.get(existing_id)
            if existing is not None:
                merged = {item.name: item for item in existing.parameters}
                merged.update(parameters)
                endpoints[existing_id] = self._endpoint(
                    source,
                    identity=identity,
                    name=existing.name,
                    operation=api_path,
                    documentation_url=source.location,
                    parameters=merged,
                    category=existing.category,
                )
                continue
            endpoint = self._endpoint(
                source,
                identity=identity,
                name=api_path.strip("/").split("/")[-1] or source.vendor_id,
                operation=api_path,
                documentation_url=source.location,
                parameters=parameters,
                category="API endpoint",
            )
            endpoints[endpoint.endpoint_id] = endpoint

        if not endpoints:
            raise ValueError(f"No documented API operations were found in {source.source_id}")
        executable = {
            key: endpoint for key, endpoint in endpoints.items() if endpoint.executable
        }
        if not executable:
            raise ValueError(f"No executable data operations were found in {source.source_id}")
        return tuple(executable[key] for key in sorted(executable))


class TwelveDataSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "twelve_data"

    _MARKDOWN_PARAMETER_RE = re.compile(
        r"(?m)^-\s+`([^`]+)`\s+\(([^,\)]+),\s*([^\)]+)\)"
    )

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        parsed = {endpoint.operation: endpoint for endpoint in super().parse(source, content)}
        for section in re.split(r"(?m)(?=^#\s+)", content):
            api_paths = {
                self._normalize_path(urlparse(url.rstrip(".,);]}")).path)
                for url in self._URL_RE.findall(section)
                if (urlparse(url.rstrip(".,);]}")).hostname or "").lower() == "api.twelvedata.com"
            }
            if not api_paths:
                continue
            parameters: dict[str, EndpointParameterSpec] = {}
            for name, raw_type, requirement in self._MARKDOWN_PARAMETER_RE.findall(section):
                lowered = raw_type.strip().lower()
                schema: Mapping[str, Any]
                if "integer" in lowered:
                    schema = {"type": "integer"}
                elif "number" in lowered or "float" in lowered:
                    schema = {"type": "number"}
                elif "boolean" in lowered:
                    schema = {"type": "boolean"}
                elif "array" in lowered:
                    schema = {"type": "array", "items": {"type": "string"}}
                else:
                    schema = {"type": "string"}
                parameters[name] = EndpointParameterSpec(
                    name=name,
                    required=requirement.strip().lower() == "required",
                    schema=schema,
                )
            if not parameters:
                continue
            for path in api_paths:
                existing = parsed.get(path)
                if existing is None:
                    continue
                merged = {item.name: item for item in existing.parameters}
                merged.update(parameters)
                parsed[path] = self._endpoint(
                    source,
                    identity=path,
                    name=existing.name,
                    operation=path,
                    documentation_url=existing.documentation_url,
                    parameters=merged,
                    category=existing.category,
                    method=str(existing.extensions.get("attas.http_method") or "GET"),
                )
        return tuple(parsed[key] for key in sorted(parsed))


class MassiveSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "massive"

    _SDK_ROUTE_RE = re.compile(r"\burl\s*=\s*f?[\"'](/[A-Za-z0-9_{}./-]+)[\"']")
    @staticmethod
    def _python_parameter(raw: str) -> tuple[str, bool, Mapping[str, Any]] | None:
        declaration = raw.strip().rstrip(",")
        if not declaration or declaration.startswith(("#", "self", "*")):
            return None
        name = declaration.split(":", 1)[0].split("=", 1)[0].strip()
        if name in {"params", "raw", "options", "pagination", "merge_all_pages", "max_pages"}:
            return None
        required = "=" not in declaration and "Optional[" not in declaration
        lowered = declaration.lower()
        if "bool" in lowered:
            schema: Mapping[str, Any] = {"type": "boolean"}
        elif "int" in lowered:
            schema = {"type": "integer"}
        elif "float" in lowered:
            schema = {"type": "number"}
        elif "list[" in lowered or "sequence[" in lowered:
            schema = {"type": "array", "items": {"type": "string"}}
        else:
            schema = {"type": "string"}
        return name, required, schema

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if source.kind != "repository":
            return super().parse(source, content)
        endpoints: dict[str, EndpointSpec] = {}
        for route_match in self._SDK_ROUTE_RE.finditer(content):
            method_start = max(
                content.rfind("\n    def ", 0, route_match.start()),
                content.rfind("\n    async def ", 0, route_match.start()),
            )
            class_start = content.rfind("\nclass ", 0, route_match.start())
            if method_start < 0 or class_start > method_start:
                continue
            route = route_match.group(1)
            header = content[method_start:route_match.start()]
            header_end = header.find("\n    )")
            if header_end < 0:
                continue
            header = header[:header_end]
            method_name_match = re.search(r"(?:async\s+)?def\s+([A-Za-z0-9_]+)\(", header)
            if method_name_match is None:
                continue
            method_name = method_name_match.group(1)
            signature = header[method_name_match.end():]
            parameters: dict[str, EndpointParameterSpec] = {}
            placeholders = set(self._PLACEHOLDER_RE.findall(route))
            for raw_parameter in signature.splitlines():
                parsed = self._python_parameter(raw_parameter)
                if parsed is None:
                    continue
                name, required, schema = parsed
                parameters[name] = EndpointParameterSpec(
                    name=name,
                    location="path" if name in placeholders else "query",
                    required=required or name in placeholders,
                    schema=schema,
                )
            endpoint = self._endpoint(
                source,
                identity=method_name,
                name=method_name.replace("_", " "),
                operation=route,
                documentation_url="https://massive.com/docs/rest",
                category=route.strip("/").split("/", 1)[0].replace("-", " ").title(),
                parameters=parameters,
            )
            endpoints[endpoint.endpoint_id] = endpoint
        if not endpoints:
            raise ValueError("No executable REST routes were found in the official Massive client")
        return tuple(endpoints[key] for key in sorted(endpoints))


class FinancialModelingPrepSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "financial_modeling_prep"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        endpoints = super().parse(source, content)
        enriched: list[EndpointSpec] = []
        for endpoint in endpoints:
            operation = endpoint.operation
            parameters = {item.name: item for item in endpoint.parameters}

            def add(name: str, schema: Mapping[str, Any] | None = None) -> None:
                parameters.setdefault(name, EndpointParameterSpec(name=name, schema=schema or {"type": "string"}))

            company_tokens = (
                "statement", "ratios", "metrics", "profile", "quote", "rating", "score", "dcf",
                "enterprise", "growth", "estimate", "surprise", "dividend", "split", "transcript",
                "insider", "institutional", "holder", "peer", "executive", "filing", "news/stock",
            )
            if any(token in operation for token in company_tokens) and not operation.endswith(("-bulk", "-list")):
                add("symbol")
            if any(token in operation for token in ("historical", "chart", "calendar", "news", "filing", "transcript", "dividend", "split")):
                add("from")
                add("to")
            if any(token in operation for token in ("statement", "ratios", "metrics", "growth", "estimate", "enterprise", "transcript")):
                add("period")
                add("limit", {"type": "integer"})
            if any(token in operation for token in ("search", "screener")):
                add("query")
                add("limit", {"type": "integer"})
                add("exchange")
            if "screener" in operation:
                for name in ("marketCapMoreThan", "marketCapLowerThan", "priceMoreThan", "priceLowerThan", "volumeMoreThan", "volumeLowerThan", "betaMoreThan", "betaLowerThan", "sector", "industry", "country", "isEtf", "isFund", "isActivelyTrading"):
                    add(name, {"type": "boolean"} if name.startswith("is") else None)
            if any(token in operation for token in ("calendar", "list", "bulk", "search", "news")):
                add("page", {"type": "integer"})
                add("limit", {"type": "integer"})
            enriched.append(
                self._endpoint(
                    source,
                    identity=operation,
                    name=endpoint.name,
                    operation=operation,
                    documentation_url=endpoint.documentation_url,
                    parameters=parameters,
                    category=endpoint.category,
                    method=str(endpoint.extensions.get("attas.http_method") or "GET"),
                )
            )
        return tuple(enriched)


class EODHDSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "eodhd"


class TiingoSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "tiingo"


class NasdaqDataLinkSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "nasdaq_data_link"


class SECEdgarSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "sec_edgar"


class FREDSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "fred"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        endpoints = super().parse(source, content)
        enriched: list[EndpointSpec] = []
        for endpoint in endpoints:
            parameters = {item.name: item for item in endpoint.parameters}

            def add(name: str, *, required: bool = False, schema: Mapping[str, Any] | None = None) -> None:
                parameters.setdefault(name, EndpointParameterSpec(name=name, required=required, schema=schema or {"type": "string"}))

            operation = endpoint.operation
            if operation.startswith("/fred/category/") or operation == "/fred/category":
                add("category_id", required=True, schema={"type": "integer"})
            if operation.startswith("/fred/release/"):
                add("release_id", required=True, schema={"type": "integer"})
            if operation.startswith("/fred/series/"):
                add("series_id", required=True)
            if operation.startswith("/fred/source/"):
                add("source_id", required=True, schema={"type": "integer"})
            if operation.startswith("/fred/tags/") or operation in {"/fred/tags", "/fred/series/search/tags", "/fred/series/search/related_tags"}:
                add("tag_names", required=operation.startswith("/fred/tags/"))
            if "/search" in operation:
                add("search_text", required=operation == "/fred/series/search")
                add("filter_variable")
                add("filter_value")
            if operation.endswith("/observations"):
                for name in ("observation_start", "observation_end", "units", "frequency", "aggregation_method"):
                    add(name)
                add("output_type", schema={"type": "integer"})
                add("vintage_dates")
            if operation.endswith("/vintagedates"):
                add("vintage_dates")
            for name in ("realtime_start", "realtime_end", "file_type"):
                add(name)
            if any(token in operation for token in ("/series", "/tags", "/releases", "/sources", "/dates", "/updates")):
                add("limit", schema={"type": "integer"})
                add("offset", schema={"type": "integer"})
                add("order_by")
                add("sort_order")
            enriched.append(
                self._endpoint(
                    source,
                    identity=endpoint.operation,
                    name=endpoint.name,
                    operation=endpoint.operation,
                    documentation_url=endpoint.documentation_url,
                    parameters=parameters,
                    category=endpoint.category,
                )
            )
        return tuple(enriched)


class TreasuryFiscalDataSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "treasury_fiscal_data"


class WorldBankSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "world_bank"


class IMFSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "imf"


class ECBSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "ecb"


class CoinGeckoSpecParser(PublicOpenAPISpecParser, PublicFinancialDocsSpecParser):
    VENDOR_ID = "coingecko"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        try:
            return PublicOpenAPISpecParser.parse(self, source, content)
        except ValueError:
            # Preserve support for the provider's older Markdown endpoint index
            # while preferring its current public OpenAPI document.
            normalized = html.unescape(content).replace("\\_", "_")
            paths = sorted(set(PublicFinancialDocsSpecParser._MARKDOWN_ENDPOINT_RE.findall(normalized)))
            if paths:
                return tuple(
                    self._endpoint(
                        source,
                        identity=path,
                        name=path.strip("/").replace("/", " "),
                        operation=self._normalize_path(path),
                        documentation_url=source.location,
                        category="Documented API endpoint",
                    )
                    for path in paths
                )
            return PublicFinancialDocsSpecParser.parse(self, source, content)


class CoinMarketCapSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "coinmarketcap"

    _TABLE_ROUTE_RE = re.compile(
        r"(?m)^\|[^\n]*?(`?/(?:x402/)?v[1-9]/[^`|\s]+`?)[^\n]*?\|\s*(GET|POST)\s*\|",
        re.IGNORECASE,
    )
    _TABLE_ROUTE_ALT_RE = re.compile(
        r"(?m)^\|[^\n]*?\|\s*(GET|POST)\s*\|\s*`?(/(?:x402/)?v[1-9]/[^`|\s]+)`?\s*\|",
        re.IGNORECASE,
    )
    _TABLE_PARAMETER_RE = re.compile(
        r"(?m)^\|\s*`?([A-Za-z][A-Za-z0-9_.\[\]-]*)`?\s*\|\s*([^|]+)\|\s*([^|]+)\|"
    )
    _CURL_PARAMETER_RE = re.compile(r"--data-urlencode\s+[\"']([A-Za-z][A-Za-z0-9_.\[\]-]*)=")

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        endpoints = {item.operation: item for item in super().parse(source, content)}
        methods: dict[str, str] = {}
        section_parameters: dict[str, dict[str, EndpointParameterSpec]] = {}
        for section in re.split(r"(?m)(?=^## Document:)", content):
            routes: set[str] = set()
            for raw_path, method in self._TABLE_ROUTE_RE.findall(section):
                path = raw_path.strip("`")
                routes.add(path)
                methods[path] = method.upper()
            for method, raw_path in self._TABLE_ROUTE_ALT_RE.findall(section):
                path = raw_path.strip("`")
                routes.add(path)
                methods[path] = method.upper()
            for url in self._URL_RE.findall(section):
                try:
                    parsed_url = urlparse(url.rstrip(".,);]}"))
                except ValueError:
                    continue
                if (parsed_url.hostname or "").lower() == "pro-api.coinmarketcap.com":
                    routes.add(parsed_url.path)
            parameters: dict[str, EndpointParameterSpec] = {}
            for name, raw_type, description in self._TABLE_PARAMETER_RE.findall(section):
                if name.lower() in {"parameter", "endpoint", "method", "path"}:
                    continue
                lowered = raw_type.lower()
                schema: Mapping[str, Any] = {"type": "integer"} if "integer" in lowered else {"type": "boolean"} if "boolean" in lowered else {"type": "string"}
                parameters[name] = EndpointParameterSpec(name=name, description=description.strip(), schema=schema)
            for name in self._CURL_PARAMETER_RE.findall(section):
                parameters.setdefault(name, EndpointParameterSpec(name=name, schema={"type": "string"}))
            for route in routes:
                section_parameters.setdefault(route, {}).update(parameters)
        enriched: list[EndpointSpec] = []
        for operation, endpoint in endpoints.items():
            parameters = {item.name: item for item in endpoint.parameters}
            parameters.update(section_parameters.get(operation, {}))
            common_names: list[str] = []
            if operation.endswith("/map"):
                common_names += ["listing_status", "start", "limit", "sort", "symbol", "aux"]
            if operation.endswith("/info") or "/info/" in operation:
                common_names += ["id", "slug", "symbol", "address", "aux"]
            if "/listings/" in operation:
                common_names += ["start", "limit", "price_min", "price_max", "market_cap_min", "market_cap_max", "volume_24h_min", "volume_24h_max", "convert", "sort", "sort_dir", "cryptocurrency_type", "tag", "aux"]
            if "/quotes/" in operation:
                common_names += ["id", "slug", "symbol", "convert", "convert_id", "aux", "skip_invalid"]
            if "/market-pairs/" in operation or "/pairs/" in operation:
                common_names += ["id", "slug", "symbol", "address", "start", "limit", "convert", "sort", "sort_dir", "aux"]
            if any(token in operation for token in ("/historical", "/ohlcv/", "/chart/")):
                common_names += ["time_start", "time_end", "count", "interval", "convert", "convert_id", "skip_invalid"]
            if any(token in operation for token in ("/latest", "/trending/", "/search", "/token")):
                common_names += ["id", "slug", "symbol", "address", "start", "limit", "convert", "aux"]
            if any(token in operation for token in ("/airdrop", "/categor", "/multiplier", "/holders/", "/liquidity-change/", "/platform/", "/security/", "/exchange/assets", "/derivatives/list")):
                common_names += ["id", "slug", "symbol", "address", "start", "limit", "convert", "aux"]
            if any(token in operation for token in ("/k-line/", "/simple/price", "/price-conversion", "/index/")):
                common_names += ["id", "symbol", "amount", "interval", "time_start", "time_end", "convert"]
            for name in dict.fromkeys(common_names):
                schema: Mapping[str, Any] = {"type": "integer"} if name in {"start", "limit", "count", "convert_id"} else {"type": "boolean"} if name == "skip_invalid" else {"type": "string"}
                parameters.setdefault(name, EndpointParameterSpec(name=name, schema=schema))
            method = methods.get(operation, str(endpoint.extensions.get("attas.http_method") or "GET"))
            if method == "POST":
                parameters.setdefault("body", EndpointParameterSpec(name="body", location="body", required=True, schema={"type": "object"}))
            enriched.append(
                self._endpoint(
                    source,
                    identity=operation,
                    name=endpoint.name,
                    operation=operation,
                    documentation_url=endpoint.documentation_url,
                    parameters=parameters,
                    category=endpoint.category,
                    method=method,
                )
            )
        return tuple(sorted(enriched, key=lambda item: item.endpoint_id))


class _AIsaDocsSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "aisa_one"


class _AIsaOpenAPISpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "aisa_one"


class AIsaSpecParser(AbstractSpecParser):
    """Normalize only AIsa's publicly documented financial API surface."""

    VERSION = "2"

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == "aisa_one"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if not self.supports(source):
            raise ValueError(f"AIsaSpecParser does not support vendor {source.vendor_id!r}")
        if source.media_type in {"application/json", "application/yaml", "text/yaml"}:
            endpoints = _AIsaOpenAPISpecParser().parse(source, content)
            financial = tuple(
                endpoint for endpoint in endpoints if endpoint.operation.startswith("/financial/")
            )
            if not financial:
                raise ValueError("No Financial Data operations were found in the AIsa OpenAPI specification")
            return financial
        return _AIsaDocsSpecParser().parse(source, content)


class BISSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "bis"


class OECDSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "oecd"


class OpenFIGISpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "openfigi"


class IntrinioSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "intrinio"


class MorningstarSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "morningstar"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        wrapper = json.loads(content)
        documents = wrapper.get("attas_openapi_documents") if isinstance(wrapper, Mapping) else None
        if not isinstance(documents, list):
            return super().parse(source, content)
        endpoints: dict[str, EndpointSpec] = {}
        for item in documents:
            if not isinstance(item, Mapping) or not isinstance(item.get("document"), Mapping):
                continue
            parsed = super().parse(source, json.dumps(item["document"]))
            for endpoint in parsed:
                key = f"{endpoint.extensions.get('attas.http_method', 'GET')}:{endpoint.operation}"
                endpoints[key] = endpoint
        if not endpoints:
            raise ValueError("No operations were found in Morningstar's public OpenAPI specifications")
        return tuple(endpoints[key] for key in sorted(endpoints))


class CrunchbaseSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "crunchbase"


class MoodysAnalyticsSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "moodys_analytics"


class DealroomSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "dealroom"


class QuartrSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "quartr"


class VisibleSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "visible"
    _HTTP_METHODS = ("get",)


class CEICSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "ceic"


class FactSetSpecParser(AbstractSpecParser):
    """Normalize every generated FactSet Python SDK API reference page."""

    VERSION = "1"
    _ROUTE_RE = re.compile(
        r"\[\*\*(?P<identity>[^*]+)\*\*\]\([^)]*\)\s*\|\s*\*\*(?P<method>GET|POST)\*\*\s+(?P<path>/\S+)\s*\|\s*(?P<description>[^\n]*)",
        re.IGNORECASE,
    )

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == "factset"

    @staticmethod
    def _schema(type_text: str) -> Mapping[str, Any]:
        value = _html_text(re.sub(r"\[[^]]+\]\([^)]+\)", "object", type_text)).lower()
        if "[str]" in value or "list" in value or "array" in value:
            return {"type": "array", "items": {"type": "string"}}
        if "bool" in value:
            return {"type": "boolean"}
        if "int" in value:
            return {"type": "integer"}
        if "float" in value or "number" in value:
            return {"type": "number"}
        if any(item in value for item in ("request", "object", "dict")):
            return {"type": "object", "additionalProperties": True}
        return {"type": "string"}

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        endpoints: dict[str, EndpointSpec] = {}
        sections = re.split(r"(?=<!-- SOURCE FILE: )", content)
        for section in sections:
            base_match = re.search(r"All URIs are relative to \*?(https://[^*\s]+)\*?", section)
            if not base_match:
                continue
            base_url = base_match.group(1).rstrip("/*")
            headings = {
                match.group(1): match.start()
                for match in re.finditer(r"^# \*\*([^*]+)\*\*\s*$", section, flags=re.MULTILINE)
            }
            ordered_headings = sorted((position, name) for name, position in headings.items())
            for route in self._ROUTE_RE.finditer(section):
                identity = route.group("identity").strip()
                method = route.group("method").upper()
                path = route.group("path").strip()
                start = headings.get(identity, route.end())
                end = next((position for position, _ in ordered_headings if position > start), len(section))
                detail = section[start:end]
                parameters: list[EndpointParameterSpec] = []
                table_match = re.search(
                    r"### Parameters\s+Name\s*\|[^\n]+\n[-| ]+\n(?P<rows>.*?)(?:\n### |\n# |\Z)",
                    detail,
                    flags=re.DOTALL | re.IGNORECASE,
                )
                if table_match:
                    for row in table_match.group("rows").splitlines():
                        columns = [item.strip() for item in row.split("|")]
                        if len(columns) < 4:
                            continue
                        name = re.sub(r"[*` ]", "", columns[0])
                        if not name or name.lower() in PublicOpenAPISpecParser._SECRET_PARAMETERS:
                            continue
                        type_text = columns[1]
                        body_like = (
                            method == "POST"
                            and (
                                "request" in type_text.lower()
                                or name.lower() in {"body", "payload", "request"}
                                or name.lower().endswith(("_request", "_body", "_payload"))
                            )
                        )
                        location = "path" if "{" + name + "}" in path else "body" if body_like else "query"
                        notes = columns[3] if len(columns) > 3 else ""
                        parameters.append(
                            EndpointParameterSpec(
                                name=name,
                                location=location,
                                description=concise_financial_description(_html_text(columns[2]), limit=500),
                                required="optional" not in notes.lower(),
                                schema=self._schema(type_text),
                            )
                        )
                for placeholder in re.findall(r"\{([^}]+)\}", path):
                    if placeholder not in {item.name for item in parameters}:
                        parameters.append(EndpointParameterSpec(name=placeholder, location="path", required=True, schema={"type": "string"}))
                api_slug = re.sub(r"[^a-z0-9]+", "_", base_url.lower()).strip("_")[-70:]
                endpoint_id = f"factset.{api_slug}.{re.sub(r'[^a-z0-9]+', '_', identity.lower()).strip('_')}"
                return_match = re.search(r"### Return type\s+(?P<type>.*?)(?:\n### |\n# |\Z)", detail, flags=re.DOTALL | re.IGNORECASE)
                return_text = _html_text(return_match.group("type")) if return_match else ""
                response_schema = dict(self._schema(return_text)) if return_text else {"type": "object", "additionalProperties": True}
                if return_text:
                    response_schema["x-factset-model"] = return_text[:300]
                endpoints[endpoint_id] = EndpointSpec(
                    endpoint_id=endpoint_id,
                    vendor_id="factset",
                    name=identity.replace("_", " ").title(),
                    description=concise_financial_description(_html_text(route.group("description")), limit=1_000),
                    category="FactSet Enterprise SDK",
                    transport="https",
                    operation=path,
                    parameters=tuple(parameters),
                    response_schema=response_schema,
                    authentication={"scheme": "delegated_connection", "provider_scheme": "oauth2_or_api_key", "required": True, "credential_reference": "connection_id"},
                    cost={"model": "provider_terms", "amount": 0, "currency": "USD", "unit": "request", "provider_cost": "FactSet product entitlement required.", "notes": []},
                    quality={"status": "provider_generated_sdk", "notes": []},
                    documentation_url=source.location,
                    executable=provider_endpoint_is_executable("factset", path),
                    extensions={"attas.schema_provenance": "official_generated_sdk", "attas.http_method": method, "attas.base_url": base_url},
                )
        if not endpoints:
            raise ValueError("No FactSet routes were found in the official generated SDK references")
        return tuple(endpoints[key] for key in sorted(endpoints))


class SimilarwebSpecParser(EmbeddedOpenAPISpecParser):
    VENDOR_ID = "similarweb"


class TomorrowIOSpecParser(EmbeddedOpenAPISpecParser):
    VENDOR_ID = "tomorrow_io"


class WeatherbitSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "weatherbit"


class MeteomaticsSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "meteomatics"


class PlanetSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "planet"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        family = re.sub(r"[^a-z0-9]+", "_", source.source_id.lower()).strip("_")
        family = family.removeprefix("planet_official_").removesuffix("_openapi")
        return tuple(
            EndpointSpec(
                **{
                    **endpoint.__dict__,
                    "endpoint_id": f"planet.{family}.{endpoint.endpoint_id.removeprefix('planet.')}",
                }
            )
            for endpoint in super().parse(source, content)
        )


class SentinelHubSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "sentinel_hub"


class UP42SpecParser(EmbeddedOpenAPISpecParser):
    VENDOR_ID = "up42"


class NOAAWeatherSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "noaa_weather"


class NASACMRSpecParser(RedocEmbeddedOpenAPISpecParser):
    VENDOR_ID = "nasa_cmr"


class CopernicusDataSpaceSpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "copernicus_data_space"


class GoogleEarthEngineSpecParser(AbstractSpecParser):
    """Normalize Google's complete Earth Engine Discovery documents."""

    VERSION = "1"

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == "google_earth_engine"

    @staticmethod
    def _schema(value: Any, schemas: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        reference = str(value.get("$ref") or "")
        if reference and isinstance(schemas.get(reference), Mapping):
            return FinnhubSpecParser._resolve_schema(schemas[reference], {"schemas": schemas})
        return FinnhubSpecParser._resolve_schema(value, {"schemas": schemas})

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        document = json.loads(content)
        if not isinstance(document, Mapping) or document.get("kind") != "discovery#restDescription":
            raise ValueError("Earth Engine source is not a Google REST Discovery document")
        schemas = document.get("schemas") if isinstance(document.get("schemas"), Mapping) else {}
        base_url = str(document.get("rootUrl") or "https://earthengine.googleapis.com").rstrip("/")
        api_version = re.sub(r"[^a-z0-9]+", "_", str(document.get("version") or "api").lower()).strip("_")
        endpoints: list[EndpointSpec] = []

        def walk(resources: Any) -> None:
            if not isinstance(resources, Mapping):
                return
            for resource in resources.values():
                if not isinstance(resource, Mapping):
                    continue
                methods = resource.get("methods")
                if isinstance(methods, Mapping):
                    for method_spec in methods.values():
                        if not isinstance(method_spec, Mapping):
                            continue
                        http_method = str(method_spec.get("httpMethod") or "GET").upper()
                        if http_method not in {"GET", "POST"}:
                            continue
                        raw_path = str(method_spec.get("path") or "")
                        path = "/" + re.sub(r"\{\+?([A-Za-z0-9_.]+)(?:=[^}]*)?\}", r"{\1}", raw_path).lstrip("/")
                        parameters: list[EndpointParameterSpec] = []
                        for name, raw in (method_spec.get("parameters") or {}).items():
                            if not isinstance(raw, Mapping):
                                continue
                            parameters.append(
                                EndpointParameterSpec(
                                    name=str(name),
                                    location=str(raw.get("location") or "query"),
                                    description=concise_financial_description(str(raw.get("description") or ""), limit=500),
                                    required=bool(raw.get("required")),
                                    schema=self._schema(raw, schemas),
                                    default=raw.get("default"),
                                )
                            )
                        request = method_spec.get("request")
                        if isinstance(request, Mapping):
                            parameters.append(EndpointParameterSpec(name="body", location="body", required=True, schema=self._schema(request, schemas)))
                        identity = str(method_spec.get("id") or f"{http_method}_{path}")
                        slug = re.sub(r"[^a-z0-9]+", "_", identity.lower()).strip("_")
                        endpoints.append(
                            EndpointSpec(
                                endpoint_id=f"google_earth_engine.{api_version}.{slug}", vendor_id="google_earth_engine",
                                name=str(method_spec.get("description") or identity).split(".", 1)[0],
                                description=concise_financial_description(str(method_spec.get("description") or identity), limit=1_000),
                                category="Google Earth Engine REST", transport="https", operation=path,
                                parameters=tuple(parameters), response_schema=self._schema(method_spec.get("response"), schemas),
                                authentication={"scheme": "delegated_connection", "provider_scheme": "oauth2", "required": True, "credential_reference": "connection_id"},
                                cost={"model": "provider_terms", "amount": 0, "currency": "USD", "unit": "request", "provider_cost": "Google Cloud and Earth Engine terms apply.", "notes": []},
                                quality={"status": "provider_documented", "notes": []}, documentation_url=source.location,
                                executable=provider_endpoint_is_executable("google_earth_engine", path),
                                extensions={"attas.schema_provenance": "google_discovery", "attas.http_method": http_method, "attas.base_url": base_url},
                            )
                        )
                walk(resource.get("resources"))

        walk(document.get("resources"))
        if not endpoints:
            raise ValueError("No Earth Engine GET or POST methods were found")
        return tuple(endpoints)


class OpenWeatherSpecParser(PublicFinancialDocsSpecParser):
    VENDOR_ID = "openweather"


class Sustainable1SpecParser(PublicOpenAPISpecParser):
    """Read-only XpressAPI datasets with stable IDs across service versions."""
    VENDOR_ID = "sustainable1"
    VERSION = "1"

    def parse(self, source, content):
        from dataclasses import replace
        from urllib.parse import urlsplit
        document = self._load_document(content)
        service = urlsplit(source.location).path.strip('/').split('/')[0]
        allowed = {"esgscores", "physicalrisk", "trucostcear", "trucostenvironmental", "trucostparisalignment"}
        if urlsplit(source.location).hostname != "xpressapi.marketplace.spglobal.com" or service not in allowed:
            raise ValueError("Use the official Sustainable1 XpressAPI dataset specification.")
        document['servers'] = [{'url': 'https://xpressapi.marketplace.spglobal.com/'+service}]
        document['paths'] = {path: {method: {**operation, 'operationId': service+'_'+method+'_'+path} for method, operation in item.items() if method in ('get','post') and isinstance(operation, Mapping)} for path, item in document.get('paths', {}).items() if path.startswith('/api/') and isinstance(item, Mapping)}
        # Both GET and POST in these dataset APIs are retrieval operations.
        # Authentication/token endpoints are outside the dataset catalog.
        return tuple(replace(e, authentication={**e.authentication, 'provider_scheme':'bearer'}, extensions={**e.extensions, 'attas.service':service}) for e in super().parse(source,json.dumps(document)))


class _LSEGOpenAPISpecParser(PublicOpenAPISpecParser):
    VENDOR_ID = "lseg"


class LSEGSpecParser(PublicFinancialDocsSpecParser):
    """Normalize public routes and entitled LSEG Swagger/OpenAPI exports."""

    VERSION = "2"

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == "lseg"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if not self.supports(source):
            raise ValueError(f"LSEGSpecParser does not support vendor {source.vendor_id!r}")
        if source.media_type in {"application/json", "application/yaml", "text/yaml"}:
            endpoints = tuple(
                endpoint
                for endpoint in _LSEGOpenAPISpecParser().parse(source, content)
                if endpoint.extensions.get("attas.http_method") in {"GET", "POST"}
            )
            if not endpoints:
                raise ValueError("The LSEG OpenAPI export has no read or analytics operations supported by data_fetch")
            return endpoints
        return super().parse(source, content)


class BloombergSpecParser(AbstractSpecParser):
    """Normalize public BLPAPI operations whose full schema is discovered at runtime."""

    VERSION = "1"

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") == "bloomberg"

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        del content
        if not self.supports(source):
            raise ValueError(f"BloombergSpecParser does not support vendor {source.vendor_id!r}")
        endpoints: list[EndpointSpec] = []
        for declared in source.options.get("declared_endpoints") or ():
            if not isinstance(declared, Mapping):
                continue
            operation = str(declared.get("operation") or "").strip()
            service_name = str(declared.get("service_name") or "//blp/refdata").strip()
            request_type = str(declared.get("request_type") or "").strip()
            if not operation or not service_name.startswith("//blp/") or not request_type:
                continue
            parameters: list[EndpointParameterSpec] = []
            for raw_parameter in declared.get("parameters") or ():
                if not isinstance(raw_parameter, Mapping):
                    continue
                name = str(raw_parameter.get("name") or "").strip()
                if not name:
                    continue
                parameters.append(
                    EndpointParameterSpec(
                        name=name,
                        location="blpapi_request",
                        description=str(raw_parameter.get("description") or ""),
                        required=bool(raw_parameter.get("required", False)),
                        schema=dict(raw_parameter.get("schema") or {"type": "string"}),
                        default=raw_parameter.get("default"),
                    )
                )
            endpoints.append(
                EndpointSpec(
                    endpoint_id=(
                        f"bloomberg.{re.sub(r'[^a-z0-9]+', '_', request_type.lower()).strip('_')}"
                        if service_name == "//blp/refdata"
                        else f"bloomberg.{service_name.rsplit('/', 1)[-1]}.{re.sub(r'[^a-z0-9]+', '_', request_type.lower()).strip('_')}"
                    ),
                    vendor_id="bloomberg",
                    name=str(declared.get("name") or request_type),
                    description=str(declared.get("description") or source.description),
                    category=str(declared.get("category") or "Bloomberg BLPAPI"),
                    transport="blpapi",
                    operation=operation,
                    parameters=tuple(parameters),
                    response_schema={"type": "object", "additionalProperties": True},
                    authentication={
                        "scheme": "delegated_connection",
                        "provider_scheme": "bloomberg_licensed_session",
                        "required": True,
                        "credential_reference": "connection_id",
                    },
                    cost={
                        "model": "provider_terms",
                        "amount": 0,
                        "currency": "USD",
                        "unit": "request",
                        "provider_cost": "Access depends on Bloomberg product licensing, service authorization, and data entitlements.",
                        "notes": [],
                    },
                    quality={
                        "status": "runtime_schema",
                        "notes": [
                            "BLPAPI request and response schemas are validated against the authorized service at runtime."
                        ],
                    },
                    documentation_url=str(declared.get("documentation_url") or source.location),
                    executable=True,
                    extensions={
                        "attas.schema_provenance": "bloomberg_runtime_service",
                        "attas.specification_only": False,
                        "bloomberg.service_name": service_name,
                        "bloomberg.request_type": request_type,
                    },
                )
            )
        if not endpoints:
            raise ValueError("No executable Bloomberg BLPAPI operations were declared")
        return tuple(endpoints)


class YFinanceSpecParser(AbstractSpecParser):
    """Build a catalog from the installed, documented public yfinance API."""

    VERSION = "4"
    _EXECUTABLE_CLASSES = {
        "Ticker", "Tickers", "Market", "Search", "Lookup", "Sector", "Industry", "Calendars"
    }
    _EXECUTABLE_FUNCTIONS = {"download", "screen"}

    @staticmethod
    def history_parameter_docs() -> dict[str, dict[str, Any]]:
        """yfinance's own documentation of history() parameters, verbatim, with any listed valid values.

        Ticker.history defers to PriceHistory.history, whose docstring carries the documented limits
        (for example "Intraday data cannot extend last 60 days"). An older yfinance without it gives {}.
        """
        try:
            from yfinance.scrapers.history import PriceHistory
        except Exception:
            return {}
        docs: dict[str, dict[str, Any]] = {}
        name = ""
        for line in (inspect.getdoc(PriceHistory.history) or "").splitlines():
            started = re.match(r"^\s{4}(\w+) : ", line)
            if started:
                name = started.group(1)
                docs[name] = {"text": []}
            elif name and line.startswith("      "):
                docs[name]["text"].append(line.strip().lstrip("|").strip())
            elif line.strip() and not line.startswith(" "):
                name = ""
        for entry in docs.values():
            entry["text"] = " ".join(part for part in entry["text"] if part)
            listed = re.search(r"Valid \w+: ([\w,]+)", entry["text"])
            if listed:
                entry["enum"] = listed.group(1).split(",")
        return docs

    @staticmethod
    def _semantic_profile(qualified_name: str) -> tuple[str, Mapping[str, Any]]:
        lowered = qualified_name.lower()
        if lowered in {"download", "ticker.history", "tickers.history"}:
            return (
                "Historical market prices with open, high, low, close, adjusted prices, and volume; "
                "supports daily, intraday, weekly, and monthly intervals for provider-covered securities.",
                {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "Date": {"type": "string",
                                             "description": "Bar date for daily, weekly and monthly intervals."},
                                    "Datetime": {"type": "string",
                                                 "description": "Bar start time for intraday intervals, in the "
                                                                "listing's time zone; such bars carry no Date."},
                                    "Open": {"type": "number"},
                                    "High": {"type": "number"},
                                    "Low": {"type": "number"},
                                    "Close": {"type": "number"},
                                    "Adj Close": {"type": "number"},
                                    "Volume": {"type": "number"},
                                },
                            },
                        }
                    },
                },
            )
        if lowered.endswith(".info") or lowered.endswith(".get_info"):
            return (
                "Security profile, quote, valuation, market, and company metadata when available from the provider.",
                {"type": "object", "additionalProperties": True},
            )
        return "", {"type": "object", "additionalProperties": True}

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") in {"yfinance", "yahoo_finance"}

    @staticmethod
    def _endpoint(
        source: VendorSpecSource,
        *,
        qualified_name: str,
        value: Any,
        category: str,
        executable: bool,
        synthetic: tuple[EndpointParameterSpec, ...] = (),
    ) -> EndpointSpec:
        semantic_description, response_schema = YFinanceSpecParser._semantic_profile(qualified_name)
        reflected_description = inspect.getdoc(value) or f"Documented yfinance operation {qualified_name}."
        description = concise_financial_description(
            " ".join(item for item in (semantic_description, reflected_description) if item),
            limit=1_000,
        )
        operation = f"yfinance.{qualified_name}"
        return EndpointSpec(
            endpoint_id=operation.lower(),
            vendor_id="yfinance",
            name=qualified_name,
            description=description,
            category=category,
            transport="python_library",
            operation=operation,
            parameters=_signature_parameters(value, synthetic=synthetic),
            response_schema=response_schema,
            authentication={"scheme": "public_provider", "required": False},
            cost={
                "model": "provider_terms",
                "amount": 0,
                "currency": "USD",
                "unit": "request",
                "provider_cost": "No direct library fee; Yahoo Finance terms and limits apply.",
                "notes": [],
            },
            quality={"status": "provider_dependent", "notes": ["Coverage and freshness vary by symbol and operation."]},
            documentation_url=source.location,
            executable=executable,
            extensions={"attas.vendor_version": source.vendor_version},
        )

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        del content
        if not self.supports(source):
            raise ValueError(f"YFinanceSpecParser does not support vendor {source.vendor_id!r}")
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - dependency is required by Attas
            raise RuntimeError("yfinance is required to build the YFinance endpoint catalog") from exc

        public_names = list(getattr(yf, "__all__", []) or [])
        endpoints: dict[str, EndpointSpec] = {}
        for name in public_names:
            if name.startswith("_") or not hasattr(yf, name):
                continue
            value = getattr(yf, name)
            if inspect.isclass(value):
                if name not in self._EXECUTABLE_CLASSES:
                    continue
                for member_name, member in inspect.getmembers(value):
                    if member_name.startswith("_"):
                        continue
                    raw_member = inspect.getattr_static(value, member_name, None)
                    is_property = isinstance(raw_member, property)
                    if not (is_property or inspect.isroutine(member)):
                        continue
                    if not is_property:
                        module = str(getattr(member, "__module__", ""))
                        if module and not module.startswith("yfinance"):
                            continue
                    constructor_names = {
                        "Ticker": "symbol",
                        "Tickers": "tickers",
                        "Market": "market",
                        "Search": "query",
                        "Lookup": "query",
                        "Sector": "key",
                        "Industry": "key",
                    }
                    synthetic_name = constructor_names.get(name, "")
                    synthetic_items = []
                    if synthetic_name:
                        synthetic_items.append(
                            EndpointParameterSpec(
                                name=synthetic_name,
                                required=True,
                                schema={"type": "string"},
                                description=f"Identifier used to construct yfinance.{name}.",
                            )
                        )
                    if name == "Calendars":
                        synthetic_items.extend(_signature_parameters(value))
                    if member_name == "history" and name in self._EXECUTABLE_CLASSES:
                        # yfinance's own words where it documents the parameter; ours only where it does not.
                        documented = self.history_parameter_docs()

                        def history_parameter(parameter: str, fallback: str, schema: dict[str, Any], **extra: Any):
                            doc = documented.get(parameter) or {}
                            if doc.get("enum"):
                                schema = {**schema, "enum": list(doc["enum"])}
                            return EndpointParameterSpec(name=parameter, description=doc.get("text") or fallback,
                                                         schema=schema, **extra)

                        synthetic_items.extend(
                            (
                                history_parameter("period", "History window such as 5d, 1mo, 1y, ytd, or max.",
                                                  {"type": "string"}, default="1mo"),
                                history_parameter("interval", "Bar interval such as 1d, 1wk, 1mo, or an intraday interval.",
                                                  {"type": "string"}, default="1d"),
                                history_parameter("start", "Optional inclusive ISO start date.", {"type": "string"}),
                                history_parameter("end", "Optional exclusive ISO end date.", {"type": "string"}),
                                history_parameter("auto_adjust", "Automatically adjust OHLC values for splits and dividends.",
                                                  {"type": "boolean"}, default=True),
                            )
                        )
                    synthetic = tuple(synthetic_items)
                    callable_value = raw_member.fget if is_property and raw_member and raw_member.fget else member
                    endpoint = self._endpoint(
                        source,
                        qualified_name=f"{name}.{member_name}",
                        value=callable_value,
                        category=name,
                        executable=True,
                        synthetic=synthetic,
                    )
                    endpoints[endpoint.endpoint_id] = endpoint
            elif callable(value):
                if name not in self._EXECUTABLE_FUNCTIONS:
                    continue
                endpoint = self._endpoint(
                    source,
                    qualified_name=name,
                    value=value,
                    category="function",
                    executable=True,
                )
                endpoints[endpoint.endpoint_id] = endpoint
        return tuple(endpoints.values())


class AlphaVantageSpecParser(AbstractSpecParser):
    """Parse all function identifiers documented on the Alpha Vantage API page."""

    VERSION = "3"
    _HEADING_RE = re.compile(r"<h([2-6])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
    _FUNCTION_RE = re.compile(r"function\s*=\s*([A-Z][A-Z0-9_]+)", re.IGNORECASE)
    _URL_RE = re.compile(r"https?://(?:www\.)?alphavantage\.co/query\?[^\s\"'<>]+", re.IGNORECASE)
    _PARAMETER_RE = re.compile(r"(?:Required|Optional)\s*:\s*([A-Za-z][A-Za-z0-9_]*)", re.IGNORECASE)

    def supports(self, source: VendorSpecSource) -> bool:
        return source.vendor_id.strip().lower().replace("-", "_") in {"alpha_vantage", "alphavantage"}

    @staticmethod
    def _clean_title(title: str, function_name: str) -> str:
        cleaned = re.sub(r"\b(?:Trending|Premium|Utility)\b", " ", title, flags=re.IGNORECASE)
        return " ".join(cleaned.split()).strip(" -") or function_name

    @staticmethod
    def _cost_profile(title: str, text: str) -> tuple[bool, bool]:
        combined = f"{title} {text}".lower()
        mixed_tier = any(
            marker in combined
            for marker in (
                "available to both free and premium",
                "compact outputsize is available to both free and premium",
                "free and premium api keys",
            )
        )
        premium = not mixed_tier and (
            "premium" in title.lower()
            or bool(re.search(r"\b(?:this is a premium endpoint|premium api function|premium endpoint)\b", combined))
        )
        return premium, mixed_tier

    @staticmethod
    def _parameter_specs(block: str, function_name: str) -> tuple[EndpointParameterSpec, ...]:
        unescaped = html.unescape(block)
        text = _html_text(unescaped)
        names: list[str] = []
        required_names = {
            match.group(1)
            for match in re.finditer(r"Required\s*:\s*([A-Za-z][A-Za-z0-9_]*)", text, re.IGNORECASE)
        }
        for match in AlphaVantageSpecParser._PARAMETER_RE.finditer(text):
            name = match.group(1)
            if name not in names:
                names.append(name)
        for url_match in AlphaVantageSpecParser._URL_RE.finditer(unescaped):
            url = html.unescape(url_match.group(0))
            for name in parse_qs(urlparse(url).query, keep_blank_values=True):
                if name not in names:
                    names.append(name)
        for name in ("function",):
            if name not in names:
                names.append(name)
        specs: list[EndpointParameterSpec] = []
        for name in names:
            lower = name.lower()
            if lower in {"apikey", "api_key", "api-key", "token"}:
                continue
            schema: dict[str, Any] = {"type": "string"}
            default: Any = None
            if lower == "function":
                schema["enum"] = [function_name]
                default = function_name
            specs.append(
                EndpointParameterSpec(
                    name=name,
                    required=lower in {item.lower() for item in required_names} or lower == "function",
                    schema=schema,
                    default=default,
                )
            )
        return tuple(specs)

    def parse(self, source: VendorSpecSource, content: str) -> Tuple[EndpointSpec, ...]:
        if not self.supports(source):
            raise ValueError(f"AlphaVantageSpecParser does not support vendor {source.vendor_id!r}")
        headings = list(self._HEADING_RE.finditer(content))
        endpoints: dict[str, EndpointSpec] = {}
        current_category = "Alpha Vantage API"
        for index, heading in enumerate(headings):
            level = int(heading.group(1))
            title = _html_text(heading.group(2))
            if level <= 3 and title:
                current_category = title
                continue
            if level != 4:
                continue
            block_end = len(content)
            for following_heading in headings[index + 1:]:
                if int(following_heading.group(1)) <= 4:
                    block_end = following_heading.start()
                    break
            block = content[heading.end():block_end]
            functions = list(dict.fromkeys(value.upper() for value in self._FUNCTION_RE.findall(html.unescape(block))))
            for function_name in functions:
                text = _html_text(block)
                premium, mixed_tier = self._cost_profile(title, text)
                endpoint_id = f"alpha_vantage.{function_name.lower()}"
                endpoints.setdefault(
                    endpoint_id,
                    EndpointSpec(
                        endpoint_id=endpoint_id,
                        vendor_id="alpha_vantage",
                        name=self._clean_title(title, function_name),
                        description=concise_financial_description(
                            text or f"Alpha Vantage {function_name} endpoint.",
                            limit=1_000,
                        ),
                        category=current_category,
                        transport="https",
                        operation=function_name,
                        parameters=self._parameter_specs(block, function_name),
                        response_schema={"type": "object", "additionalProperties": True},
                        authentication={
                            "scheme": "delegated_connection",
                            "provider_scheme": "api_key",
                            "required": True,
                            "credential_reference": "connection_id",
                            "parameter": "apikey",
                        },
                        cost={
                            "model": "premium_subscription" if premium else "tiered" if mixed_tier else "free_or_subscription",
                            "amount": 0,
                            "currency": "USD",
                            "unit": "request",
                            "provider_cost": (
                                "Premium subscription required."
                                if premium
                                else "Free and premium access vary by parameter or output depth."
                                if mixed_tier
                                else "Free tier or subscription, subject to provider limits."
                            ),
                            "notes": ["Entitlement depends on request parameters."] if mixed_tier else [],
                        },
                        quality={"status": "provider_documented", "notes": []},
                        documentation_url=source.location,
                        executable=True,
                        extensions={
                            "attas.premium": premium,
                            "attas.mixed_tier": mixed_tier,
                            "attas.vendor_version": source.vendor_version,
                        },
                    ),
                )

        all_functions = list(dict.fromkeys(value.upper() for value in self._FUNCTION_RE.findall(html.unescape(content))))
        for function_name in all_functions:
            endpoint_id = f"alpha_vantage.{function_name.lower()}"
            endpoints.setdefault(
                endpoint_id,
                EndpointSpec(
                    endpoint_id=endpoint_id,
                    vendor_id="alpha_vantage",
                    name=function_name,
                    description=f"Alpha Vantage {function_name} endpoint documented by the vendor.",
                    category="Alpha Vantage API",
                    transport="https",
                    operation=function_name,
                    parameters=(
                        EndpointParameterSpec(name="function", required=True, schema={"type": "string", "enum": [function_name]}, default=function_name),
                    ),
                    response_schema={"type": "object", "additionalProperties": True},
                    authentication={
                        "scheme": "delegated_connection",
                        "provider_scheme": "api_key",
                        "required": True,
                        "credential_reference": "connection_id",
                        "parameter": "apikey",
                    },
                    cost={
                        "model": "provider_terms",
                        "amount": 0,
                        "currency": "USD",
                        "unit": "request",
                        "provider_cost": "See Alpha Vantage plan and endpoint entitlement.",
                        "notes": [],
                    },
                    quality={"status": "provider_documented", "notes": []},
                    documentation_url=source.location,
                    executable=True,
                    extensions={"attas.vendor_version": source.vendor_version},
                ),
            )
        if not endpoints:
            raise ValueError("No Alpha Vantage function identifiers were found in the documentation")
        return tuple(endpoints.values())
