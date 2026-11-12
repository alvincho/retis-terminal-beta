"""Resolve agent payment/publication identity from Plaza's registered owner."""
from fastapi import HTTPException


def agent_owner(plaza, agent_id):
    if not isinstance(agent_id, str) or not agent_id or len(agent_id) > 200:
        raise ValueError("A registered agent identity is required.")
    practice = plaza._get_plaza_practice()
    if practice is None:
        raise HTTPException(503, "Plaza agent registry is unavailable.")
    search = getattr(getattr(practice, "state", None), "search_entries", None)
    if callable(search):
        entries = search(agent_id=agent_id)
        entry = next((row for row in entries if str(row.get("agent_id") or row.get("card", {}).get("agent_id") or "") == agent_id), None)
        if entry is None:
            raise HTTPException(404, "Registered agent unavailable.")
    else:
        entry = plaza._resolve_plaza_pulser_entry(practice=practice, pulser_id=agent_id)
    card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
    if str(entry.get("agent_id") or card.get("agent_id") or "") != agent_id:
        raise HTTPException(409, "Registered agent identity differs from the requested publisher.")
    owner = plaza._resolve_entry_owner_user(entry)
    if not isinstance(owner, dict) or not owner.get("id"):
        raise HTTPException(409, "Registered agent has no resolvable owner account.")
    if not plaza._ui_user_status_is_active(owner):
        raise HTTPException(403, "Registered agent owner account is disabled.")
    return owner


def agent_payee(plaza, agent_id, consumer_id):
    owner = agent_owner(plaza, agent_id)
    return {"agentId": agent_id, "provider_user_id": str(owner["id"]),
            "provider_username": str(owner.get("username") or ""),
            "basis": "registered-agent-owner", "isOwner": str(owner["id"]) == consumer_id}
