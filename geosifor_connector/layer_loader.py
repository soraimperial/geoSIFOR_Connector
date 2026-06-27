"""
layer_loader.py

Turns a saved Endpoint (see models.py) into a real QGIS layer on the map,
attaching the shared Basic Auth config automatically for any endpoint not
marked public.

"""

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, Any, Callable
from urllib.parse import quote

from qgis.core import (
    QgsVectorLayer, QgsRasterLayer, QgsProject, QgsBlockingNetworkRequest,
    QgsApplication, QgsTask,
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

logger = logging.getLogger("geosifor_connector")


@dataclass
class LoadResult:
    """
    What load_endpoint() returns, instead of a bare (layer, error, choices)
    tuple. Exactly one of `layer` or `error` is set on a final result;
    `pending_choices` is set instead of either when the caller needs to
    prompt the user and retry (see load_endpoint's docstring).
    """
    layer: Optional[Any] = None
    error: Optional[str] = None
    pending_choices: Optional[list] = field(default=None)

    @property
    def ok(self) -> bool:
        return self.layer is not None

    @property
    def needs_choice(self) -> bool:
        return bool(self.pending_choices)


def _blocking_get(query_url, authcfg=""):
    """
    Performs one GET via QgsBlockingNetworkRequest, so the stored authcfg
    is honored the same way it would be for an actual layer load. Returns
    (raw_text_or_None, error_message_or_None).

    """
    request = QNetworkRequest(QUrl(query_url))
    blocking = QgsBlockingNetworkRequest()
    if authcfg:
        blocking.setAuthCfg(authcfg)

    err = blocking.get(request)
    if err != QgsBlockingNetworkRequest.ErrorCode.NoError:
        return None, f"Could not query {query_url}: {blocking.errorMessage()}"

    reply = blocking.reply()
    raw = bytes(reply.content()).decode("utf-8", errors="replace")
    return raw, None


def fetch_async(query_url: str, authcfg: str, callback: Callable[[Optional[str], Optional[str]], None]) -> QgsTask:
    """
    Runs _blocking_get() on a background QgsTask instead of the main/UI
    thread, so a slow or unresponsive server (a weak field connection, a
    government server having a bad day) doesn't freeze QGIS's whole
    interface while waiting -- the previous behaviour, since every
    GetCapabilities/metadata fetch in this plugin used to run directly on
    the main thread via QgsBlockingNetworkRequest.
    """

    def run(task: QgsTask):
        # Runs on the worker thread -- must not touch any Qt widgets.
        return _blocking_get(query_url, authcfg)

    def finished(exception, result):
        # Runs back on the main thread.
        if exception is not None:
            logger.warning("Background fetch failed for %s: %s", query_url, exception)
            callback(None, str(exception))
            return
        raw, err = result
        callback(raw, err)

    task = QgsTask.fromFunction(f"GeoSIFOR Connector: fetching {query_url}", run, on_finished=finished)
    QgsApplication.taskManager().addTask(task)
    return task


# ---------------------------------------------------------------------
# WMS / WMTS layer-name discovery (minimal: name only, no style/format
# guessing -- those are hardcoded to match the verified working example)
# ---------------------------------------------------------------------

def _local_tag(elem):
    """Strip the XML namespace prefix from an element tag, e.g.
    '{http://www.opengis.net/wfs}FeatureType' -> 'FeatureType'."""
    return elem.tag.rsplit("}", 1)[-1]


def _parse_wfs_typenames(raw: str, query_url: str):
    """Pure parsing step, unchanged from before -- only the fetch that
    supplies `raw` is now asynchronous; this logic itself didn't need to
    change. Returns (list_of_(typename, title)_tuples, error_or_None)."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return None, f"GetCapabilities response at {query_url} was not valid XML ({e})."

    typenames = []
    for feature_type_elem in root.iter():
        if _local_tag(feature_type_elem) != "FeatureType":
            continue

        name, title = None, None
        for child in feature_type_elem:
            tag = _local_tag(child)
            if tag == "Name":
                name = (child.text or "").strip()
            elif tag == "Title":
                title = (child.text or "").strip()

        if name:
            typenames.append((name, title or name))

    return typenames, None


def fetch_wfs_typenames_async(url, authcfg, callback: Callable[[Optional[list], Optional[str]], None]) -> QgsTask:
    """
    Async equivalent of the old fetch_wfs_typenames(): queries a WFS
    endpoint's GetCapabilities document for just the feature type
    name(s) on a background QgsTask, then calls
    callback(list_of_(typename, title)_tuples_or_None, error_or_None)
    back on the main thread. See fetch_async()'s docstring for why this
    runs in the background and the caller's responsibility to keep the
    returned task referenced.
    """
    separator = "&" if "?" in url else "?"
    query_url = f"{url}{separator}SERVICE=WFS&REQUEST=GetCapabilities"

    def on_fetched(raw, err):
        if err:
            callback(None, err)
            return
        callback(*_parse_wfs_typenames(raw, query_url))

    return fetch_async(query_url, authcfg, on_fetched)


def _build_wfs_uri(url, typename, public, authcfg):
    """Explicit, minimal WFS connection string for QGIS's WFS provider."""
    parts = [
        "srsname='EPSG:3763'",
        f"typename='{typename}'",
        f"url='{url}'",
        "version='auto'",
    ]
    if not public and authcfg:
        parts.append(f"authcfg={authcfg}")
    return " ".join(parts)


def _parse_wms_layer_names(raw: str, query_url: str):
    """
    Pure parsing step, unchanged from before (including the ancestor-CRS
    walk that fixed the WMTS inheritance bug) -- only the fetch that
    supplies `raw` is now asynchronous.

    Returns (list_of_(layer_name, title, crs_list)_tuples, error_or_None).
    crs_list is every <CRS>/<SRS> the layer declares (including inherited
    from ancestor <Layer> elements), in GetCapabilities's own order.
    """
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return None, f"GetCapabilities response at {query_url} was not valid XML ({e})."

    # Build a parent map once, since ElementTree gives no built-in way to
    # walk upward from a child -- needed below to collect CRS/SRS declared
    # on ancestor <Layer> elements, not just the leaf layer itself.
    parent_of = {child: parent for parent in root.iter() for child in parent}

    def ancestor_layers(elem):
        """Yields elem itself, then each ancestor <Layer> up to the root,
        since WMS 1.1.1 lets a layer inherit CRS/SRS declared on its
        parent <Layer> instead of always repeating its own full list."""
        current = elem
        while current is not None:
            if _local_tag(current) == "Layer":
                yield current
            current = parent_of.get(current)

    layers = []
    for layer_elem in root.iter():
        if _local_tag(layer_elem) != "Layer":
            continue
        if any(_local_tag(c) == "Layer" for c in layer_elem):
            continue  # container/group layer, not a leaf

        name, title = None, None
        for child in layer_elem:
            tag = _local_tag(child)
            if tag == "Name":
                name = (child.text or "").strip()
            elif tag == "Title":
                title = (child.text or "").strip()

      
        crs_list = []
        for layer_in_chain in ancestor_layers(layer_elem):
            for child in layer_in_chain:
                if _local_tag(child) in ("CRS", "SRS"):
                    value = (child.text or "").strip()
                    if value and value not in crs_list:
                        crs_list.append(value)

        if name:
            layers.append((name, title or name, crs_list))

    return layers, None


def fetch_wms_layer_names_async(url, authcfg, callback: Callable[[Optional[list], Optional[str]], None]) -> QgsTask:
    """
    Async equivalent of the old fetch_wms_layer_names(): queries a
    WMS/WMTS endpoint's GetCapabilities document on a background QgsTask,
    then calls callback(list_of_(layer_name, title, crs_list)_tuples_or_None,
    error_or_None) back on the main thread.
    """
    separator = "&" if "?" in url else "?"
    query_url = f"{url}{separator}SERVICE=WMS&REQUEST=GetCapabilities"

    def on_fetched(raw, err):
        if err:
            callback(None, err)
            return
        callback(*_parse_wms_layer_names(raw, query_url))

    return fetch_async(query_url, authcfg, on_fetched)


def _pick_crs(crs_list, project=None):
    """
    Picks a CRS from a layer's declared options, preferring (in order):

      1. The QGIS project's own current CRS, if the layer declares it.
         This is the actually-correct reason to prefer a specific CRS:
         loading a layer in whatever CRS the rest of the project already
         uses means no reprojection is needed and everything lines up
         without QGIS having to do on-the-fly transformation. A user
         working entirely in EPSG:3763, for example, wants layers loaded
         in 3763 whenever a service offers it, not a fixed "WMS=4326"
         assumption.
      2. EPSG:4326, the WMS specification's own default CRS, if the
         project CRS isn't among the layer's options.
      3. Whichever CRS the layer lists first, as a last resort.

    Caveat: this is a heuristic, not a guarantee. At least one real
    GeoSIFOR layer's working source used EPSG:4326 even though EPSG:3763
    was also among its declared options, so project-CRS matching is the
    right general principle but not confirmed to explain every case. If a
    layer fails to load on a CRS mismatch, comparing its declared CRS
    list against a known-working QGIS-generated source string for that
    same layer is the most reliable way to check this heuristic.
    """
    if not crs_list:
        return "EPSG:4326"

    if project is None:
        from qgis.core import QgsProject
        project = QgsProject.instance()

    try:
        project_crs = project.crs().authid()  # e.g. "EPSG:3763"
    except Exception:  # noqa: BLE001 -- project CRS lookup is best-effort
        project_crs = ""

    if project_crs and project_crs in crs_list:
        return project_crs
    if "EPSG:4326" in crs_list:
        return "EPSG:4326"
    return crs_list[0]


def _build_wms_query_string_uri(url, layer_name, public, authcfg, crs="EPSG:4326"):
 
    params = [
        ("contextualWMSLegend", "0"),
        ("crs", crs),
        ("dpiMode", "7"),
        ("featureCount", "10"),
        ("format", "image/png"),
        ("layers", layer_name),
    ]

    parts = []
    for key, value in params:
        parts.append(f"{key}={quote(value, safe='')}")
    parts.append("styles")  # bare, matching QGIS's own native format
    parts.append("tilePixelRatio=0")

    if not public and authcfg:
        parts.append(f"authcfg={authcfg}")

    parts.append(f"url={quote(url, safe='')}")
    return "&".join(parts)


# ---------------------------------------------------------------------
# REST (ArcGIS FeatureServer/MapServer) sub-layer discovery
# ---------------------------------------------------------------------

def is_arcgis_service_root(url):
    """
    True if the URL is a bare FeatureServer/MapServer root with no
    trailing numeric sub-layer index, e.g. ".../FeatureServer" rather
    than ".../FeatureServer/0".
    """
    lowered = url.rstrip("/").lower()
    return lowered.endswith("featureserver") or lowered.endswith("mapserver")


def _parse_arcgis_sublayers(raw: str, url: str):
    """Pure parsing step, unchanged from before. Returns
    (list_of_(index, name)_tuples, error_or_None)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"Service metadata at {url} was not valid JSON."

    layers = data.get("layers", [])
    if not layers:
        # Some services nest sub-layers differently, or this might already
        # be a layer-level endpoint that doesn't need drilling into.
        return [], None

    return [(layer.get("id"), layer.get("name", f"Layer {layer.get('id')}")) for layer in layers], None


def fetch_arcgis_sublayers_async(url, authcfg, callback: Callable[[Optional[list], Optional[str]], None]) -> QgsTask:
    """
    Async equivalent of the old fetch_arcgis_sublayers(): queries an
    ArcGIS Server FeatureServer/MapServer root's own JSON metadata on a
    background QgsTask, then calls
    callback(list_of_(index, name)_tuples_or_None, error_or_None) back on
    the main thread.
    """
    query_url = url.rstrip("/") + "?f=json"

    def on_fetched(raw, err):
        if err:
            callback(None, err)
            return
        callback(*_parse_arcgis_sublayers(raw, url))

    return fetch_async(query_url, authcfg, on_fetched)


# ---------------------------------------------------------------------
# URI builders
# ---------------------------------------------------------------------

def _build_arcgis_uri(url, public, authcfg):
    parts = [f"url='{url}'"]
    if not public and authcfg:
        parts.append(f"authcfg={authcfg}")
    return " ".join(parts)


def _build_geojson_uri(url, public, authcfg):
    """
    OGR's remote-file reading (vsicurl under the hood) doesn't understand
    QGIS's "authcfg=" data source convention the way WFS/WMS/ArcGIS
    providers do — that's specific to QGIS's own network-aware providers.
    For a plain HTTP(S) GeoJSON URL read via the OGR provider, credentials
    have to be inlined directly into the URL as "https://user:pass@host/...",
    which is the standard HTTP Basic Auth-in-URL convention GDAL/OGR
    understands. We read the stored username/password back out of the
    QGIS Authentication Manager (rather than just passing the authcfg id
    along) specifically for this one provider.
    """
    if public or not authcfg:
        return url

    username, password, err = _get_basic_auth_credentials(authcfg)
    if err or not username:
        # Fall back to the bare URL; load_endpoint will report the
        # resulting failure clearly rather than silently sending an
        # unauthenticated request to a restricted endpoint.
        return url

    scheme, _, rest = url.partition("://")
    return f"{scheme}://{username}:{password}@{rest}"


def _get_basic_auth_credentials(authcfg):
    """
    Look up the stored Basic Auth username/password for a given authcfg
    id via QGIS's Authentication Manager.

    Returns (username, password, error_message_or_None).
    """
    from qgis.core import QgsApplication, QgsAuthMethodConfig

    auth_manager = QgsApplication.authManager()
    cfg = QgsAuthMethodConfig()
    ok = auth_manager.loadAuthenticationConfig(authcfg, cfg, full=True)
    if not ok:
        return None, None, f"Could not load stored credential '{authcfg}'."

    username = cfg.config("username", "")
    password = cfg.config("password", "")
    if not username:
        return None, None, f"Stored credential '{authcfg}' has no username (is it Basic Auth?)."

    return username, password, None


# ---------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------

def _label_with_suffix(base_label: str, suffix: str | None) -> str:
    """
    Appends a chosen sub-layer's display name to the endpoint's label for
    the resulting QGIS layer's name, e.g. "GeoSIFOR RGG — Sede" instead of
    just "GeoSIFOR RGG" -- only when a real choice was made among several
    options, so a single-option service's layer name stays exactly the
    plain endpoint label with nothing appended.
    """
    if not suffix or suffix == base_label:
        return base_label
    return f"{base_label} — {suffix}"


def load_endpoint_async(
    endpoint, authcfg,
    on_done: Callable[[LoadResult], None],
    project=None, choice=None, choice_display_name=None,
) -> Optional[QgsTask]:
 
    project = project or QgsProject.instance()
    service = endpoint.service
    service_label = service.value  # plain "WMS", not Enum's "ServiceType.WMS", for display/log text
    url = endpoint.url
    label = endpoint.label or url
    public = endpoint.public
    use_authcfg = "" if public else authcfg

    layer_label = _label_with_suffix(label, choice_display_name)

    logger.debug("Loading endpoint '%s' (%s) from %s", label, service_label, url)

    def finish_with_layer(layer):
        """Shared tail: validate, add to project, report success/failure.
        Unchanged from the old load_endpoint()'s ending."""
        if layer is None or not layer.isValid():
            logger.warning("Layer '%s' failed to load (provider reported invalid).", label)
            reason = (
                f"Layer '{label}' failed to load. If this is a restricted endpoint, "
                f"check the stored credential above; otherwise check the URL is "
                f"still correct."
            )
            on_done(LoadResult(error=reason))
            return
        project.addMapLayer(layer)
        logger.info("Loaded layer '%s' (%s).", label, service_label)
        on_done(LoadResult(layer=layer))

    if service == "WFS":
        typename = choice
        if typename is not None:
            uri = _build_wfs_uri(url, typename, public, use_authcfg)
            finish_with_layer(QgsVectorLayer(uri, layer_label, "WFS"))
            return None

        def on_typenames(choices, err):
            if err:
                logger.warning("WFS GetCapabilities failed for '%s': %s", label, err)
                on_done(LoadResult(error=err))
                return
            if not choices:
                on_done(LoadResult(error=(
                    f"Could not find any feature types for '{label}'. "
                    f"The URL or credential may be incorrect."
                )))
                return
            if len(choices) == 1:
                uri = _build_wfs_uri(url, choices[0][0], public, use_authcfg)
                finish_with_layer(QgsVectorLayer(uri, layer_label, "WFS"))
            else:
                on_done(LoadResult(pending_choices=choices))  # caller should prompt and retry

        return fetch_wfs_typenames_async(url, use_authcfg, on_typenames)

    elif service in ("WMS", "WMTS"):
        # GeoSIFOR's WMTS endpoints need no WMTS-specific parameters at
        # all -- a real working WMTS source string showed it's the exact
        # same flat WMS query-string format as WMS. The only thing that
        # varies is CRS, and that varies *per layer*, not per service
        # type -- so both branches read the layer's own declared CRS
        # list from GetCapabilities and pick from that.
        if choice is not None:
            layer_name, crs = choice
            uri = _build_wms_query_string_uri(url, layer_name, public, use_authcfg, crs=crs)
            finish_with_layer(QgsRasterLayer(uri, layer_label, "wms"))
            return None

        def on_wms_layers(choices, err):
            if err:
                logger.warning("%s GetCapabilities failed for '%s': %s", service_label, label, err)
                on_done(LoadResult(error=err))
                return
            if not choices:
                on_done(LoadResult(error=(
                    f"Could not find any layers for '{label}'. "
                    f"The URL or credential may be incorrect."
                )))
                return
            if len(choices) == 1:
                name, _title, crs_list = choices[0]
                layer_name, crs = name, _pick_crs(crs_list, project)
                uri = _build_wms_query_string_uri(url, layer_name, public, use_authcfg, crs=crs)
                finish_with_layer(QgsRasterLayer(uri, layer_label, "wms"))
            else:
                # Present (name, crs_list) -> friendly title as the choice;
                # the actual chosen CRS is resolved with _pick_crs() once
                # the user picks a layer, same as the single-choice path.
                prompt_choices = [
                    ((name, _pick_crs(crs_list, project)), title)
                    for name, title, crs_list in choices
                ]
                on_done(LoadResult(pending_choices=prompt_choices))

        return fetch_wms_layer_names_async(url, use_authcfg, on_wms_layers)

    elif service == "REST":
        # GeoSIFOR's "REST" services are ArcGIS Server endpoints, almost
        # always FeatureServer (vector) once you reach an actual
        # sub-layer; MapServer (raster /export) is the fallback only if
        # the vector provider can't load it.
        def build_and_finish(effective_url):
            uri = _build_arcgis_uri(effective_url, public, use_authcfg)
            layer = QgsVectorLayer(uri, layer_label, "arcgisfeatureserver")
            if not layer.isValid():
                layer = QgsRasterLayer(uri, layer_label, "arcgismapserver")
            finish_with_layer(layer)

        if not is_arcgis_service_root(url):
            build_and_finish(url)
            return None

        if choice is not None:
            build_and_finish(url.rstrip("/") + f"/{choice}")
            return None

        def on_sublayers(choices, err):
            if err:
                logger.warning("ArcGIS metadata fetch failed for '%s': %s", label, err)
                on_done(LoadResult(error=err))
                return
            if choices and len(choices) == 1:
                build_and_finish(url.rstrip("/") + f"/{choices[0][0]}")
            elif choices:
                on_done(LoadResult(pending_choices=choices))
            else:
                build_and_finish(url)

        return fetch_arcgis_sublayers_async(url, use_authcfg, on_sublayers)

    elif service == "GeoJSON/Data API":
        uri = _build_geojson_uri(url, public, use_authcfg)
        finish_with_layer(QgsVectorLayer(uri, label, "ogr"))
        return None

    else:
        logger.error("Unknown service type '%s' for endpoint '%s'", service_label, label)
        on_done(LoadResult(error=f"Unknown service type '{service_label}' for endpoint '{label}'."))
        return None


def has_multiple_layers_async(
    endpoint, authcfg,
    on_done: Callable[[Optional[bool], Optional[str]], None],
) -> Optional[QgsTask]:
   
    service = endpoint.service
    url = endpoint.url
    public = endpoint.public
    use_authcfg = "" if public else authcfg

    def from_choices(choices, err):
        if err:
            on_done(None, err)
            return
        on_done((len(choices) > 1 if choices else False), None)

    if service == "WFS":
        return fetch_wfs_typenames_async(url, use_authcfg, from_choices)
    elif service in ("WMS", "WMTS"):
        return fetch_wms_layer_names_async(url, use_authcfg, from_choices)
    elif service == "REST":
        if not is_arcgis_service_root(url):
            on_done(False, None)  # already points at one specific sub-layer
            return None
        return fetch_arcgis_sublayers_async(url, use_authcfg, from_choices)
    else:
        on_done(False, None)  # GeoJSON/Data API and anything else: always exactly one
        return None



