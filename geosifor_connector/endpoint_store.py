"""
endpoint_store.py

Persists the user's own list of saved service endpoints using
QgsSettings, so it survives QGIS restarts and lives per-profile like any
other QGIS connection.

Endpoints are stored as plain JSON (a list of dicts) since that's what
QgsSettings can hold, but every function here works with real Endpoint
objects (see models.py) -- the dict <-> Endpoint conversion happens only
at the load/save boundary in this module, so nothing else in the plugin
needs to know the storage format is JSON.

There is no discovery here by design: GeoSIFOR's catalogue is a
JavaScript viewer with no crawlable root, so endpoints are collected by
hand, once, from the viewer itself. This module exists purely to make
that one-time cost permanent instead of repeated.

Folders are mostly just a value endpoints share, derived dynamically from
whichever folder names are currently in use -- but a folder can also be
created standalone with nothing in it yet (e.g. via right-click on empty
space), in which case its name is remembered separately until something
is filed into it or it's deleted.

A "profile" is a named, saved set of endpoint ids that should be checked
at once (e.g. "Operational Decision", "Fuels"), stored separately since
several profiles can overlap on the same endpoints.
"""

import json
import logging
from qgis.core import QgsSettings

from .models import Endpoint, ServiceType

logger = logging.getLogger("geosifor_connector")

SETTINGS_GROUP = "geosifor_connector"
SETTINGS_KEY = "endpoints"
PROFILES_KEY = "profiles"
EMPTY_FOLDERS_KEY = "empty_folders"

# Kept for any external code that imported this directly before the
# ServiceType enum existed; new code should use ServiceType.values().
VALID_SERVICE_TYPES = ServiceType.values()


def _settings() -> QgsSettings:
    return QgsSettings()


def load_endpoints() -> list[Endpoint]:
    """Return the list of saved Endpoint objects, oldest-added first."""
    s = _settings()
    raw = s.value(f"{SETTINGS_GROUP}/{SETTINGS_KEY}", "")
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Saved endpoint list was not valid JSON; treating as empty.")
        return []

    if not isinstance(data, list):
        logger.warning("Saved endpoint list was not a JSON array; treating as empty.")
        return []

    endpoints = []
    for entry in data:
        if not isinstance(entry, dict):
            logger.warning("Skipping a malformed saved endpoint entry: %r", entry)
            continue
        try:
            endpoints.append(Endpoint.from_dict(entry))
        except ValueError as e:
            logger.warning("Skipping a saved endpoint with an invalid service type: %s", e)

    return endpoints


def save_endpoints(endpoints: list[Endpoint]) -> None:
    """Overwrite the full saved list. Callers should load, mutate, save."""
    s = _settings()
    payload = json.dumps([e.to_dict() for e in endpoints])
    s.setValue(f"{SETTINGS_GROUP}/{SETTINGS_KEY}", payload)


def add_endpoint(label: str, url: str, service, public: bool, authcfg: str = "", folder: str = "") -> list[Endpoint]:
    """
    `service` may be a ServiceType or a plain string (e.g. straight from
    a dropdown widget's currentText()) -- ServiceType.coerce() accepts
    both and raises a clear error for anything unrecognized.
    """
    service = ServiceType.coerce(service)
    if not label.strip() or not url.strip():
        raise ValueError("label and url are required")

    endpoints = load_endpoints()
    endpoints.append(Endpoint(
        label=label.strip(),
        url=url.strip(),
        service=service,
        public=bool(public),
        authcfg=authcfg or "",
        folder=folder.strip(),
    ))
    save_endpoints(endpoints)
    return endpoints


def remove_endpoint(index: int) -> list[Endpoint]:
    endpoints = load_endpoints()
    if 0 <= index < len(endpoints):
        removed_id = endpoints[index].id
        endpoints.pop(index)
        save_endpoints(endpoints)
        _remove_id_from_all_profiles(removed_id)
    return endpoints


def update_endpoint(index: int, **fields) -> list[Endpoint]:
    """Update one or more fields of an existing endpoint by its list index,
    e.g. update_endpoint(2, favorite=True)."""
    endpoints = load_endpoints()
    if 0 <= index < len(endpoints):
        for key, value in fields.items():
            setattr(endpoints[index], key, value)
        save_endpoints(endpoints)
    return endpoints


def set_favorite(index: int, favorite: bool) -> list[Endpoint]:
    return update_endpoint(index, favorite=bool(favorite))


def set_folder(index: int, folder: str) -> list[Endpoint]:
    return update_endpoint(index, folder=(folder or "").strip())


def set_label(index: int, label: str) -> list[Endpoint]:
    return update_endpoint(index, label=(label or "").strip())


def set_folder_by_id(endpoint_id: str, folder: str) -> list[Endpoint]:
    """Same as set_folder, but addressed by stable id rather than list
    index -- convenient for callers (like drag-and-drop) that only have
    the id on hand."""
    endpoints = load_endpoints()
    for idx, e in enumerate(endpoints):
        if e.id == endpoint_id:
            return update_endpoint(idx, folder=(folder or "").strip())
    return endpoints


def _load_empty_folders() -> list[str]:
    s = _settings()
    raw = s.value(f"{SETTINGS_GROUP}/{EMPTY_FOLDERS_KEY}", "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        logger.warning("Saved empty-folders list was not valid JSON; treating as empty.")
    return []


def _save_empty_folders(names: list[str]) -> None:
    s = _settings()
    s.setValue(f"{SETTINGS_GROUP}/{EMPTY_FOLDERS_KEY}", json.dumps(names))


def create_folder(folder_name: str) -> list[str]:
    """
    Create a folder with no endpoints in it yet -- e.g. via right-click on
    empty space in the tree, rather than only ever being able to create
    one as a side effect of filing an endpoint into it. Stored separately
    from endpoints; once something is filed into it, list_folders() finds
    it from the endpoints themselves too, but it stays remembered here
    even if it empties back out again later, since a deliberately created
    folder shouldn't vanish just because it's temporarily unused.
    """
    folder_name = folder_name.strip()
    if not folder_name:
        raise ValueError("Folder name is required")

    empty_folders = _load_empty_folders()
    if folder_name not in empty_folders:
        empty_folders.append(folder_name)
        _save_empty_folders(empty_folders)
    return list_folders()


def list_folders() -> list[str]:
    """Distinct folder names currently in use or explicitly created,
    sorted, excluding the empty/unfiled value."""
    endpoints = load_endpoints()
    folders = {e.folder.strip() for e in endpoints}
    folders.discard("")
    folders.update(_load_empty_folders())
    return sorted(folders)


def delete_folder(folder_name: str) -> list[Endpoint]:
    """
    Remove a folder by unfiling every endpoint currently in it (sets their
    folder to ""), rather than touching the endpoints themselves. Since
    folders are mostly just a shared label, "deleting" one is just
    clearing that label everywhere it's used -- nothing is lost. Also
    drops it from the explicitly-created-empty-folders list, if present.
    """
    endpoints = load_endpoints()
    changed = False
    for e in endpoints:
        if e.folder == folder_name:
            e.folder = ""
            changed = True
    if changed:
        save_endpoints(endpoints)

    empty_folders = _load_empty_folders()
    if folder_name in empty_folders:
        empty_folders.remove(folder_name)
        _save_empty_folders(empty_folders)

    return endpoints


def rename_folder(old_name: str, new_name: str) -> list[Endpoint]:
    """Rename a folder across all endpoints and the empty-folder registry."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name:
        raise ValueError("Current folder name is required")
    if not new_name:
        raise ValueError("New folder name is required")
    if old_name == new_name:
        return load_endpoints()

    endpoints = load_endpoints()
    changed = False
    for e in endpoints:
        if e.folder == old_name:
            e.folder = new_name
            changed = True
    if changed:
        save_endpoints(endpoints)

    empty_folders = _load_empty_folders()
    if old_name in empty_folders:
        empty_folders.remove(old_name)
        if new_name not in empty_folders:
            empty_folders.append(new_name)
        _save_empty_folders(empty_folders)

    return endpoints


def reorder_endpoint(moved_id: str, target_id: str, before: bool) -> list[Endpoint]:
    """
    Move the endpoint with id == moved_id to sit immediately before (or
    after) the endpoint with id == target_id in the underlying list. Used
    for drag-and-drop reordering within a folder (or among unfiled
    entries), where on-screen order is simply filtered list order.

    Folder membership is left untouched here -- this only changes
    position. Moving between folders is a separate operation
    (set_folder), since a single drag-drop in the UI may need to do both.
    """
    endpoints = load_endpoints()

    moved_index = next((i for i, e in enumerate(endpoints) if e.id == moved_id), None)
    if moved_index is None:
        return endpoints

    moved_entry = endpoints.pop(moved_index)

    target_index = next((i for i, e in enumerate(endpoints) if e.id == target_id), None)
    if target_index is None:
        # Target vanished (shouldn't normally happen) -- put it back where it was.
        endpoints.insert(moved_index, moved_entry)
        return endpoints

    insert_at = target_index if before else target_index + 1
    endpoints.insert(insert_at, moved_entry)
    save_endpoints(endpoints)
    return endpoints


def export_to_json_string() -> str:
    """Used by the 'Export list' button so the list survives a profile reset."""
    return json.dumps([e.to_dict() for e in load_endpoints()], indent=2, ensure_ascii=False)


def import_from_json_string(raw: str, replace: bool = False) -> list[Endpoint]:
    """
    Used by the 'Import list' button.
    If replace=True, overwrites the current list entirely.
    If replace=False, appends, skipping exact duplicate (label, url) pairs.

    Raises ValueError with a clear message if the JSON isn't a list of
    endpoint-shaped objects, rather than trusting imported data blindly.
    """
    try:
        incoming_raw = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"That file isn't valid JSON ({e}).")

    if not isinstance(incoming_raw, list):
        raise ValueError("Imported JSON must be a list of endpoint objects.")

    incoming = []
    for i, entry in enumerate(incoming_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry #{i + 1} in the imported file isn't an object.")
        if not entry.get("label") or not entry.get("url"):
            raise ValueError(f"Entry #{i + 1} in the imported file is missing a label or url.")
        try:
            incoming.append(Endpoint.from_dict(entry))
        except ValueError as e:
            raise ValueError(f"Entry #{i + 1} in the imported file: {e}")

    if replace:
        save_endpoints(incoming)
        return incoming

    existing = load_endpoints()
    existing_keys = {(e.label, e.url) for e in existing}
    for entry in incoming:
        key = (entry.label, entry.url)
        if key not in existing_keys:
            existing.append(entry)
            existing_keys.add(key)
    save_endpoints(existing)
    return existing


def get_default_authcfg() -> str:
    """The single shared Basic Auth config id, if one has been set up."""
    s = _settings()
    return s.value(f"{SETTINGS_GROUP}/default_authcfg", "")


def set_default_authcfg(authcfg_id: str) -> None:
    s = _settings()
    s.setValue(f"{SETTINGS_GROUP}/default_authcfg", authcfg_id or "")


# ---------------------------------------------------------------------
# Profiles: named, saved sets of endpoint ids to check at once
# ---------------------------------------------------------------------

def load_profiles() -> dict[str, list[str]]:
    """Return {profile_name: [endpoint_id, ...]}."""
    s = _settings()
    raw = s.value(f"{SETTINGS_GROUP}/{PROFILES_KEY}", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        logger.warning("Saved profiles were not valid JSON; treating as empty.")
    return {}


def save_profiles(profiles: dict[str, list[str]]) -> None:
    s = _settings()
    s.setValue(f"{SETTINGS_GROUP}/{PROFILES_KEY}", json.dumps(profiles))


def save_profile(name: str, endpoint_ids) -> dict[str, list[str]]:
    """Create or overwrite a profile with the given name."""
    name = name.strip()
    if not name:
        raise ValueError("Profile name is required")
    profiles = load_profiles()
    profiles[name] = list(endpoint_ids)
    save_profiles(profiles)
    return profiles


def delete_profile(name: str) -> dict[str, list[str]]:
    profiles = load_profiles()
    profiles.pop(name, None)
    save_profiles(profiles)
    return profiles


def _remove_id_from_all_profiles(endpoint_id: str) -> None:
    """Called when an endpoint is removed, so stale ids don't linger in profiles."""
    profiles = load_profiles()
    changed = False
    for name, ids in profiles.items():
        if endpoint_id in ids:
            profiles[name] = [i for i in ids if i != endpoint_id]
            changed = True
    if changed:
        save_profiles(profiles)
