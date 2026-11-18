"""Install provider operations in their existing DataSource, never in a client.

Activation is explicit. The source keeps its identity, registration, canonical
routes and lifespan. Credentials and stream leases use a source-owned vault.
"""
from pathlib import Path

from fastapi import HTTPException

from .routes import create_app, install_private_headers
from .service import ProviderService
from .vault import ProviderVault


def install_provider_runtime(source, configuration=None, *, connectors=None):
    config = configuration or source._config_data.get("provider_runtime") or {}
    if config.get("enabled") is not True:
        return None
    if set(config) - {"enabled", "state_dir", "policy", "users", "probe", "native_workspace"}:
        raise ValueError("Unsupported provider runtime configuration.")
    state = Path(config.get("state_dir", "")).expanduser()
    if not config.get("state_dir") or not state.is_absolute():
        raise ValueError("An explicit source-owned runtime directory is required.")
    checkout = Path(__file__).resolve().parents[3]
    if state.resolve().is_relative_to(checkout):
        raise ValueError("Provider runtime state must remain outside the source checkout.")
    users = config.get("users")
    if not isinstance(users, dict) or not users:
        raise ValueError("Explicit verified-user operation permissions are required.")
    actions = {"source.read", "connections.read", "connections.write", "source.execute", "source.probe", "source.stream"}
    if any(not isinstance(user, str) or not user or not isinstance(allowed, list)
           or any(action not in actions for action in allowed) for user, allowed in users.items()):
        raise ValueError("Provider permissions must list supported actions for each verified user.")
    if not isinstance(config.get("policy"), dict):
        raise ValueError("An explicit provider execution/retention policy is required.")
    service = ProviderService(source, ProviderVault(state, source.vendor_id), config["policy"], probe=config.get("probe"))

    async def authenticate(request, action):
        from prompits.core.call_grants import PREFIX, stream_user
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(401, "Sign in to this DataSource.")
        if token.startswith(PREFIX) and action == "connections.write":
            raise HTTPException(403, "Credential changes require direct source user authentication.")
        verified = await source._verify_plaza_user_token(caller_user_token=token, preferred_plaza_url=source.plaza_url)
        owner = verified.get("user_id")
        # Consult the server-owned mapping each time; revocation does not need
        # a client refresh or a service restart. Request fields supply no ACL.
        if not owner or action not in config["users"].get(owner, []):
            raise HTTPException(403, "This user is not authorized for the source operation.")
        if action == "source.stream" and token.startswith(PREFIX):
            current = await stream_user(source, token)
            if current.get("id") != owner:
                raise HTTPException(403, "Source stream authority changed.")
        return {"id": owner, "window": (verified.get("user") or {}).get("terminalWindow", "")}

    owned_app = create_app(service, authenticate, connectors=connectors)
    paths = {getattr(route, "path", None) for route in source.app.router.routes}
    if any(getattr(route, "path", "").startswith("/api/source/v1/") for route in source.app.router.routes):
        raise ValueError("Provider runtime is already installed in this source.")
    source.app.router.routes.extend(route for route in owned_app.router.routes
                                    if getattr(route, "path", None) not in paths)
    source.app.exception_handlers.update(owned_app.exception_handlers)
    install_private_headers(source.app)
    source.app.add_event_handler("shutdown", owned_app.state.provider_streams.close)
    source.app.state.provider_service = service
    source.app.state.provider_streams = owned_app.state.provider_streams
    source.agent_card.setdefault("meta", {})["provider_runtime"] = {
        "version": "1.0.0", "execution_owner": source.vendor_id,
        "interface": "/api/source/v1/interface", "connections": "/api/source/v1/connections",
        "snapshots": "/api/source/v1/snapshots", "credential_owner": "source",
        "terminal_execution": False,
    }
    from .native_workspace import install_native_workspace
    install_native_workspace(source, service, config.get('native_workspace') or {}, config['users'])
    return service
