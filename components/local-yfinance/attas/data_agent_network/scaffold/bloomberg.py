"""Executable Bloomberg BLPAPI adapter with runtime service-schema discovery."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Mapping

from .models import EndpointExecutionResult


BLOOMBERG_SERVICE_NAME = "//blp/refdata"
BLOOMBERG_SERVICE_OPERATIONS = {
    "//blp/refdata": frozenset({
        "ReferenceDataRequest",
        "HistoricalDataRequest",
        "IntradayBarRequest",
        "IntradayTickRequest",
        "PortfolioDataRequest",
        "BeqsRequest",
        "ServiceSchema",
    }),
    "//blp/apiflds": frozenset({
        "FieldInfoRequest",
        "FieldSearchRequest",
        "CategorizedFieldSearchRequest",
        "ServiceSchema",
    }),
    "//blp/instruments": frozenset({"instrumentListRequest", "curveListRequest", "govListRequest", "ServiceSchema"}),
    "//blp/mktdata": frozenset({"Subscription", "ServiceSchema"}),
    "//blp/mktbar": frozenset({"Subscription", "ServiceSchema"}),
}
BLOOMBERG_OPERATIONS = frozenset(
    operation for operations in BLOOMBERG_SERVICE_OPERATIONS.values() for operation in operations
)


def bloomberg_endpoint_is_executable(operation: str, extensions: Mapping[str, Any] | None = None) -> bool:
    """Return whether a catalog operation maps to the fixed Bloomberg service allowlist."""
    metadata = extensions if isinstance(extensions, Mapping) else {}
    service_name = str(metadata.get("bloomberg.service_name") or "")
    request_type = str(metadata.get("bloomberg.request_type") or "")
    return (
        service_name in BLOOMBERG_SERVICE_OPERATIONS
        and request_type in BLOOMBERG_SERVICE_OPERATIONS[service_name]
        and str(operation or "") == f"{service_name}:{request_type}"
    )


class BloombergBlpapiClient:
    """Small synchronous BLPAPI client created only when a live request is made."""

    def __init__(
        self,
        *,
        server_host: str = "localhost",
        server_port: int = 8194,
        authentication_options: str = "",
        timeout_ms: int = 30_000,
        blpapi_module: Any = None,
    ) -> None:
        self.server_host = str(server_host or "localhost").strip() or "localhost"
        self.server_port = int(server_port)
        self.authentication_options = str(authentication_options or "").strip()
        self.timeout_ms = max(1_000, min(int(timeout_ms), 300_000))
        self._blpapi = blpapi_module

    def _module(self) -> Any:
        if self._blpapi is None:
            try:
                import blpapi
            except ImportError as exc:
                raise RuntimeError(
                    "The Bloomberg BLPAPI Python SDK is not installed in this Data Source runtime."
                ) from exc
            self._blpapi = blpapi
        return self._blpapi

    def _open_session(self, service_name: str) -> tuple[Any, Any, Any]:
        if service_name not in BLOOMBERG_SERVICE_OPERATIONS:
            raise ValueError("Bloomberg service is not allowlisted.")
        blpapi = self._module()
        options = blpapi.SessionOptions()
        options.setServerHost(self.server_host)
        options.setServerPort(self.server_port)
        if self.authentication_options:
            options.setAuthenticationOptions(self.authentication_options)
        session = blpapi.Session(options)
        if not session.start():
            raise RuntimeError("Bloomberg BLPAPI session could not be started.")
        if not session.openService(service_name):
            session.stop()
            raise RuntimeError("Bloomberg reference-data service could not be opened or authorized.")
        return blpapi, session, session.getService(service_name)

    @staticmethod
    def _populate_request(request: Any, parameters: Mapping[str, Any]) -> None:
        if hasattr(request, "fromJson"):
            request.fromJson(json.dumps(dict(parameters), default=str))
            return
        for name, value in parameters.items():
            if value is None:
                continue
            if isinstance(value, list):
                if value and all(isinstance(item, Mapping) for item in value):
                    array = request.getElement(name)
                    for item in value:
                        entry = array.appendElement()
                        for child_name, child_value in item.items():
                            entry.setElement(str(child_name), child_value)
                else:
                    for item in value:
                        request.append(name, item)
            elif isinstance(value, Mapping):
                element = request.getElement(name)
                for child_name, child_value in value.items():
                    element.setElement(str(child_name), child_value)
            else:
                request.set(name, value)

    @staticmethod
    def _message_payload(message: Any) -> Dict[str, Any]:
        if hasattr(message, "toPy"):
            raw = message.toPy()
        elif hasattr(message, "toJson"):
            raw = json.loads(message.toJson())
        else:
            raw = {"content": message.toString()}
        normalized = json.loads(json.dumps(raw, default=str))
        payload = normalized if isinstance(normalized, Mapping) else {"value": normalized}
        return {
            "message_type": str(message.messageType()) if hasattr(message, "messageType") else "",
            "data": dict(payload),
        }

    def request(
        self,
        service_name: str,
        request_type: str,
        parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if service_name not in BLOOMBERG_SERVICE_OPERATIONS or request_type not in BLOOMBERG_SERVICE_OPERATIONS[service_name] - {"ServiceSchema"}:
            raise ValueError("Bloomberg service or operation is not allowlisted.")
        blpapi, session, service = self._open_session(service_name)
        try:
            request = service.createRequest(request_type)
            self._populate_request(request, parameters)
            session.sendRequest(request)
            messages: list[Dict[str, Any]] = []
            while True:
                event = session.nextEvent(self.timeout_ms)
                event_type = event.eventType()
                if event_type == blpapi.Event.TIMEOUT:
                    raise TimeoutError("Bloomberg BLPAPI request timed out.")
                for message in event:
                    payload = self._message_payload(message)
                    response_error = payload["data"].get("responseError")
                    if isinstance(response_error, Mapping):
                        category = str(response_error.get("category") or "")
                        message_text = str(response_error.get("message") or "Bloomberg rejected the request.")
                        raise RuntimeError(f"Bloomberg {category}: {message_text}")
                    messages.append(payload)
                if event_type == blpapi.Event.RESPONSE:
                    break
            return {"service": service_name, "operation": request_type, "messages": messages}
        finally:
            session.stop()

    def subscribe(
        self,
        service_name: str,
        securities: list[str],
        fields: list[str],
        options: list[str] | None = None,
        *,
        max_events: int = 1,
    ) -> Mapping[str, Any]:
        """Collect a bounded number of live subscription data events."""
        if service_name not in {"//blp/mktdata", "//blp/mktbar"}:
            raise ValueError("Bloomberg subscription service is not allowlisted.")
        if not securities:
            raise ValueError("At least one Bloomberg subscription security is required.")
        event_limit = max(1, min(int(max_events), 1_000))
        blpapi, session, _service = self._open_session(service_name)
        try:
            subscriptions = blpapi.SubscriptionList()
            for security in securities:
                subscriptions.add(str(security), [str(field) for field in fields], [str(item) for item in (options or [])])
            session.subscribe(subscriptions)
            messages: list[Dict[str, Any]] = []
            data_events = 0
            while data_events < event_limit:
                event = session.nextEvent(self.timeout_ms)
                event_type = event.eventType()
                if event_type == blpapi.Event.TIMEOUT:
                    raise TimeoutError("Bloomberg BLPAPI subscription timed out.")
                for message in event:
                    messages.append(self._message_payload(message))
                if event_type == blpapi.Event.SUBSCRIPTION_DATA:
                    data_events += 1
            return {
                "service": service_name,
                "operation": "Subscription",
                "data_events": data_events,
                "messages": messages,
            }
        finally:
            session.stop()

    def describe_service(self, service_name: str) -> Mapping[str, Any]:
        if service_name not in BLOOMBERG_SERVICE_OPERATIONS:
            raise ValueError("Bloomberg service is not allowlisted.")
        _blpapi, session, service = self._open_session(service_name)
        try:
            operations: list[Dict[str, Any]] = []
            for operation in service.operations():
                request_definition = operation.requestDefinition()
                responses = [
                    definition.toString()
                    for definition in operation.responseDefinitions()
                ]
                operations.append(
                    {
                        "name": str(operation.name()),
                        "description": str(operation.description()),
                        "request_schema": request_definition.toString() if request_definition is not None else "",
                        "response_schemas": responses,
                    }
                )
            return {
                "service": service_name,
                "description": str(service.description()),
                "schema": service.toString(),
                "operations": operations,
            }
        finally:
            session.stop()


def bloomberg_client_from_config(
    config: Mapping[str, Any] | None = None,
    *,
    blpapi_module: Any = None,
) -> BloombergBlpapiClient:
    """Create a client from non-secret server settings and server-owned environment config."""
    settings = config if isinstance(config, Mapping) else {}
    host = str(os.getenv("BLPAPI_HOST") or settings.get("server_host") or "localhost")
    raw_port = os.getenv("BLPAPI_PORT") or settings.get("server_port") or 8194
    raw_timeout = os.getenv("BLPAPI_TIMEOUT_MS") or settings.get("timeout_ms") or 30_000
    return BloombergBlpapiClient(
        server_host=host,
        server_port=int(raw_port),
        authentication_options=str(
            os.getenv("BLPAPI_AUTHENTICATION_OPTIONS")
            or settings.get("authentication_options")
            or ""
        ),
        timeout_ms=int(raw_timeout),
        blpapi_module=blpapi_module,
    )


def execute_bloomberg_endpoint(
    endpoint: Mapping[str, Any],
    parameters: Mapping[str, Any],
    *,
    client: Any,
) -> EndpointExecutionResult:
    """Execute one fixed BLPAPI operation using the authorized runtime service schema."""
    endpoint_id = str(endpoint.get("endpoint_id") or "")
    extensions = endpoint.get("extensions") if isinstance(endpoint.get("extensions"), Mapping) else {}
    operation = str(endpoint.get("operation") or "")
    if not bloomberg_endpoint_is_executable(operation, extensions):
        return EndpointExecutionResult(
            status="not_executable",
            endpoint_id=endpoint_id,
            error="This catalog entry is not an allowlisted Bloomberg BLPAPI operation.",
        )
    secret_names = {"password", "token", "secret", "authorization", "credential", "api_key", "apikey"}
    if any(str(name).lower() in secret_names for name in parameters):
        return EndpointExecutionResult(
            status="failed",
            endpoint_id=endpoint_id,
            error="Raw Bloomberg credentials are not accepted as request parameters.",
        )
    specs = {
        str(item.get("name") or ""): item
        for item in endpoint.get("parameters") or ()
        if isinstance(item, Mapping) and str(item.get("name") or "")
    }
    unknown = sorted(set(parameters) - set(specs))
    if unknown:
        return EndpointExecutionResult(
            status="failed",
            endpoint_id=endpoint_id,
            error=f"Unsupported Bloomberg request parameter(s): {', '.join(unknown)}",
        )
    request_parameters = dict(parameters)
    for name, spec in specs.items():
        if name not in request_parameters and spec.get("default") is not None:
            request_parameters[name] = spec.get("default")
    missing = [
        name
        for name, spec in specs.items()
        if spec.get("required") and (request_parameters.get(name) is None or request_parameters.get(name) == "")
    ]
    if missing:
        return EndpointExecutionResult(
            status="failed",
            endpoint_id=endpoint_id,
            error=f"Missing required Bloomberg request parameter(s): {', '.join(missing)}",
        )
    service_name = str(extensions["bloomberg.service_name"])
    request_type = str(extensions["bloomberg.request_type"])
    try:
        if request_type == "ServiceSchema":
            raw = client.describe_service(service_name)
        elif request_type == "Subscription":
            raw = client.subscribe(
                service_name,
                list(request_parameters.get("securities") or ()),
                list(request_parameters.get("fields") or ()),
                list(request_parameters.get("options") or ()),
                max_events=int(request_parameters.get("max_events") or 1),
            )
        else:
            raw = client.request(service_name, request_type, request_parameters)
        normalized = json.loads(json.dumps(raw, default=str))
        data = normalized if isinstance(normalized, Mapping) else {"items": normalized}
        return EndpointExecutionResult(
            status="completed",
            endpoint_id=endpoint_id,
            data=dict(data),
            data_schema=endpoint.get("response_schema") if isinstance(endpoint.get("response_schema"), Mapping) else {},
            cost=endpoint.get("cost") if isinstance(endpoint.get("cost"), Mapping) else {},
        )
    except Exception as exc:
        message = str(exc).lower()
        status = "authentication_required" if any(
            marker in message
            for marker in ("not authorized", "authorization", "permission", "entitlement", "could not be opened")
        ) else "failed"
        error = (
            "Bloomberg rejected the licensed session or data entitlement."
            if status == "authentication_required"
            else "The Bloomberg BLPAPI request could not be completed."
        )
        return EndpointExecutionResult(status=status, endpoint_id=endpoint_id, error=error)


__all__ = [
    "BLOOMBERG_OPERATIONS",
    "BLOOMBERG_SERVICE_OPERATIONS",
    "BLOOMBERG_SERVICE_NAME",
    "BloombergBlpapiClient",
    "bloomberg_client_from_config",
    "bloomberg_endpoint_is_executable",
    "execute_bloomberg_endpoint",
]
