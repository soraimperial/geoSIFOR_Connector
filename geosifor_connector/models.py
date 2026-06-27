"""
models.py

Typed data shapes used throughout the plugin, instead of passing raw
dicts and magic strings between modules.

- ServiceType: an enum of the supported connection types, instead of
  comparing plain strings like "WFS" or "REST" everywhere.
- Endpoint: a dataclass for one saved connection, instead of a dict with
  string keys. QgsSettings only stores plain text, so Endpoint still
  converts to/from a plain dict at the storage boundary (to_dict/from_dict)
  -- the rest of the plugin works with real Endpoint objects.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum


class ServiceType(str, Enum):
    """
    The connection types this plugin knows how to load. Subclassing
    `str` as well as `Enum` means a ServiceType still compares equal to
    its plain string value (ServiceType.WFS == "WFS" is True) and still
    serializes to plain text in JSON without any extra conversion code --
    important since these are stored as plain text in QgsSettings and in
    exported/imported JSON files that may have been created by an older
    version of the plugin that only ever knew plain strings.
    """
    WFS = "WFS"
    WMS = "WMS"
    WMTS = "WMTS"
    REST = "REST"
    GEOJSON = "GeoJSON/Data API"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """Plain string values, in declaration order -- used to populate
        the service-type dropdown in the add-endpoint form."""
        return tuple(member.value for member in cls)

    @classmethod
    def coerce(cls, value) -> "ServiceType":
        """
        Turns a plain string (e.g. loaded from old JSON) into a
        ServiceType, raising a clear error for anything unrecognized
        rather than letting an invalid value silently flow through the
        rest of the plugin.
        """
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError:
            raise ValueError(
                f"Unknown service type '{value}'. Expected one of: {', '.join(cls.values())}"
            )


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Endpoint:
    """
    One saved GeoSIFOR (or other) service connection.

    `id` is a stable identifier assigned once at creation and never
    reused -- favourites and profiles reference endpoints by id rather
    than by list position, so those references survive reordering or
    other endpoints being added/removed.
    """
    label: str
    url: str
    service: ServiceType
    public: bool = False
    authcfg: str = ""
    folder: str = ""
    favorite: bool = False
    id: str = field(default_factory=_new_id)

    def to_dict(self) -> dict:
        """Plain-dict form for JSON storage (QgsSettings, export/import).
        ServiceType serializes as its plain string value automatically
        since it subclasses str."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Endpoint":
        """
        Builds an Endpoint from a plain dict, tolerating entries saved by
        older versions of the plugin that predate one or more fields
        (id/folder/favorite) -- those just fall back to their defaults
        rather than raising.
        """
        return cls(
            label=data.get("label", ""),
            url=data.get("url", ""),
            service=ServiceType.coerce(data.get("service")),
            public=bool(data.get("public", False)),
            authcfg=data.get("authcfg", ""),
            folder=data.get("folder", ""),
            favorite=bool(data.get("favorite", False)),
            id=data.get("id") or _new_id(),
        )
