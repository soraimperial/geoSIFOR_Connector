# GeoSIFOR Connector — what's new in this version

This consolidates everything changed since the last working baseline It's meant as a single reference for what to actually test, not a turn-by-turn history. Because I keep forgetting what I actually did and what I should still look at.

## Most recent: background loading, code-quality pass, multi-layer UX

- **Network calls no longer block QGIS's main thread.** Loading a layer
  and checking whether a service exposes multiple layers both now run on
  a background `QgsTask`. Previously, a slow or unresponsive server (a
  weak field connection, a government server having a bad day) would
  freeze QGIS's entire interface for the duration of the request, which
  could look indistinguishable from a crash. Verified with a simulated
  task-execution harness covering: single-option resolution, multi-option
  prompting and retry-with-choice, and a network-failure case — all
  produce the same correct results as before, just without blocking.
- **"Add a layer from this service…"**: right-click a single endpoint to
  pull another layer out of a multi-layer service immediately, without
  ticking its checkbox or running the full batch "Add selected to map"
  flow.
- **Layer naming**: when a layer is loaded by picking from more than one
  option, the chosen layer's name is appended to the resulting QGIS
  layer's name (e.g. "GeoSIFOR RGG — Sede"), so loading several layers
  from the same endpoint no longer produces identically-named,
  indistinguishable entries in the Layers panel.
- **"Multiple layers" indicator** in the tree, so a multi-layer service
  is visible before you click into it, not only once the layer-picker
  dialog appears. Checked once per endpoint per session (not on every
  refresh) and only for endpoints with an available credential, to limit
  how often this triggers a network call.
- A significant internal refactor: `Endpoint`/`ServiceType` (dataclass +
  enum) replaced raw dicts and magic strings throughout; a structured
  `LoadResult` replaced a bare `(layer, error, choices)` tuple; logging
  now routes to QGIS's own Log Messages panel; every button/menu handler
  is wrapped in a decorator that shows a plain error message instead of
  silently failing on an unexpected bug. Verified with standalone tests
  against the storage layer (add/favourite/folder/reorder/export/import,
  old-format migration, profile-cleanup-on-removal) and the decorator
  itself (confirmed it correctly drops the extra argument Qt's `clicked`
  signal passes, which broke every button on first deployment and was
  fixed immediately after).
- Two real bugs found and fixed from actual GeoSIFOR testing: a display
  bug showing "ServiceType.WMS" instead of "WMS" in the tree (an
  Enum/str formatting quirk), and a WMTS CRS bug where a layer's CRS
  inherited from its parent `<Layer>` element in WMS 1.1.1 capabilities
  wasn't being collected, causing a fallback to a CRS the server didn't
  actually support.

## Window & layout

- **Opens as a real window**, not a docked panel: minimize, maximize,
  close, freely resizable — the same as QGIS's own Data Source Manager.
  The earlier dockable-panel mode was removed entirely; it behaved
  inconsistently depending on where it was dragged.
- **Collapsible sections** (the same expand/collapse widget QGIS uses in
  its own panels, e.g. Layer Styling), so the endpoint tree — the actual
  working area — gets most of the vertical space instead of competing
  with setup UI that's only used occasionally:
  - **Shared credential** — collapsed by default once something's
    configured; expanded automatically the very first time, when nothing
    is set up yet, so it isn't missed.
  - **Profiles** — collapsed by default. Useful for some workflows, not
    universally used.
  - **Add new endpoint** — collapsed by default.
  - Each remembers whether you left it expanded or collapsed, across
    sessions.
- **Checkbox rendering fixed** — explicit styling on the tree's checkbox
  indicators so they always render as distinct, bordered boxes; they
  previously could merge into one solid block depending on the active
  QGIS theme.

## Organising endpoints

- **Folders**, entirely optional:
  - Set a folder when adding an endpoint, right-click an endpoint and
    "Move to folder…", or right-click empty space below the list and
    "New folder…" to create one with nothing in it yet.
  - "(no folder)" is always available wherever a folder is chosen, and
    is the default — using folders never becomes mandatory just because
    some endpoints have one.
  - Unfiled endpoints sit directly at the top level of the tree, not
    trapped inside a forced catch-all group.
  - Right-click a folder's row (or several selected folders) → "Delete
    folder" unfiles its endpoints; it never deletes the endpoints
    themselves.
- **Load as group**: ticking a folder's own checkbox ticks every endpoint
  inside it in one motion.
- **Drag and drop**: drag an endpoint onto another to reorder it relative
  to that one (refiling it too, if the target's in a different folder);
  drag onto a folder's row to file directly into that folder.
- **Search/filter bar**, sitting directly above the endpoint tree, matching
  label, URL, service type, and folder name as you type.
- **Favourites**: right-click one or more selected endpoints → "Add to
  Favorites" (or double-click a single one). Favourited endpoints get a
  ★ and also appear grouped under a "Favorites" section at the top of the
  tree, in addition to wherever else they're filed.
- **Profiles** — Save / Load / New… / Delete:
  - "New…" creates a named profile from whatever's currently checked.
  - "Save" updates the *currently selected* profile with the current
    checked set (no rename, just an update) — this replaced an earlier
    version that could only ever create new profiles, never update one.
  - "Load" re-checks exactly the endpoints saved in the selected profile.
  - Profiles store endpoint ids, not list positions, so reordering or
    adding other endpoints never breaks a saved profile.

## Removing things

- **No more standalone "Remove selected" button.** Endpoints are now
  removed by right-clicking one or more selected rows → "Remove" — the
  same right-click pattern used for folders, favourites, and move-to-folder,
  rather than a separate button with its own selection logic.
- Right-click now correctly acts on the **entire current multi-selection**,
  not just the single row under the cursor — applies to favouriting,
  moving to a folder, and removing, for both endpoints and folders.

## Service loading fixes

These three only matter once an endpoint actually gets loaded — covered
here because each one was a real bug found through testing, not a
guess that's still unverified:

- **REST (ArcGIS FeatureServer)**: a bare service URL (no trailing layer
  index) isn't directly loadable. The plugin now queries the service's
  own JSON metadata (`<url>?f=json`) to find the real sub-layer, and uses
  it automatically when there's only one — the normal case for GeoSIFOR.
- **WFS**: QGIS's WFS provider needs an explicit `typename`, which a bare
  URL doesn't supply. The plugin now queries the service's own
  GetCapabilities XML to find it.
- **WMS**: needed two separate fixes, both found through real endpoint
  testing:
  - QGIS's WMS provider needs an explicit `layers=` parameter — again
    queried from GetCapabilities rather than assumed.
  - Some GeoSIFOR WMS services additionally reject an empty `styles=`
    value; they need a real one, which can be namespace-prefixed even
    when the layer's own name isn't (e.g. layer `biosfera` needing style
    `BDG:biosfera`). The required style name is now read from each
    layer's own GetCapabilities entry alongside its name.
- **GeoJSON / Data API** endpoints (the plain `.../v1/dados/...` URLs)
  needed a different fix entirely: OGR's remote-file reading doesn't
  understand QGIS's `authcfg=` convention the way WFS/WMS/ArcGIS
  providers do. Credentials are now read back out of the stored
  authentication config and inlined directly into the URL
  (`https://user:pass@host/...`), matching the convention GDAL/OGR
  actually expects for HTTP Basic Auth.
- In all three discovery cases (REST/WFS/WMS), if a service genuinely
  exposes more than one option, you're prompted to pick rather than the
  plugin guessing.

## Fixed bugs

- A crash when right-clicking and choosing "Move to folder…" on certain
  selections (`QAction` has no `parentWidget()` — leftover from an
  earlier, incorrect submenu-detection check).
- Service-type dropdown no longer offers "FeatureServer" as a separate,
  confusing option from "REST" — GeoSIFOR's REST services are ArcGIS
  Server underneath, and both pointed at the same loading logic anyway.
- Added a "GeoJSON / Data API" service type for the plain `.../v1/dados/...`
  endpoints, which are a different shape entirely from WFS/WMS/REST.

## Known untested / lower-confidence areas

Worth specifically exercising rather than assuming these are solid:

- **Folder-checkbox propagation** ("load as group") relies on Qt's
  tristate parent-checkbox behaviour. If ticking a folder doesn't tick
  every endpoint inside it, that's worth reporting.
- **Drag-and-drop** intercepts Qt's drop event directly (rather than
  trusting the tree widget's built-in move) specifically so storage
  always matches what's visually on screen. The three distinct paths —
  drag onto another endpoint, drag onto a folder's row, reorder within
  the same folder — haven't all been independently confirmed.


## Notes / things to verify against real GeoSIFOR services

Tested about only 20 services (AGIF, ICNF, DGT, GNR and AML). So there might be hidden issues with other services.
The biggest issue has been WMS, which kept breaking with every little change. I am not confident that CRS issues might be resolved at this point.

- REST services in GeoSIFOR are ArcGIS Server endpoints. A bare service
  URL (".../FeatureServer" or ".../MapServer", no trailing layer index)
  isn't directly loadable, so the plugin queries the service's own JSON
  metadata ("<url>?f=json") to find the actual sub-layer, the same way it
  always has.
- **WFS** queries GetCapabilities for just the feature type name, then
  builds an explicit connection string
  (`srsname='EPSG:3763' typename='...' url='...' version='auto'`).
  `QgsProviderRegistry.instance().querySublayers()` (the same API the
  native "Add WFS Layer" dialog calls) was tried here too, but the QGIS
  log panel showed it triggers GDAL's stricter GMLAS schema validator,
  which failed on a duplicate element/type declaration in GeoSIFOR's own
  GML schema — a real problem on the server's side, not something fixable
  by changing how the connection string is built. The lighter-weight
  typename-only read avoids that validation path entirely.
- **WMS and WMTS share the same builder.** Both are built as a flat,
  URL-encoded query string, matched byte-for-byte against real working
  source strings copied from layers loaded through QGIS's native "Add
  WMS/WMTS Layer" dialog (the same dialog handles both). Two earlier
  attempts at WMS (hand-parsed GetCapabilities XML, then
  `querySublayers()`) both produced URIs the server rejected before this
  flat format was matched exactly and confirmed working.
- The **CRS** used is read from each layer's own declared options in
  GetCapabilities, then picked by preferring, in order: (1) the QGIS
  **project's own current CRS**, if the layer offers it — this is the
  actually-correct reason to prefer a particular CRS, since it avoids
  on-the-fly reprojection and matches the experience of someone working
  entirely in one CRS (e.g. always in EPSG:3763) seeing layers "just
  match" through QGIS's native dialog; (2) `EPSG:4326`, the WMS
  specification's own default, if the project CRS isn't among the
  layer's options; (3) whichever CRS is listed first, as a last resort.
- **CRS inheritance fix**: at least one real WMTS layer (Ortos2018, WMS
  1.1.1) was found failing with `unsupported srs: EPSG:4326` even though
  its real `Available in CRS` list (per QGIS's own Layer Properties) was
  only `EPSG:3763`/`EPSG:3857` — 4326 was never declared. The cause: WMS
  1.1.1 lets a layer inherit CRS/SRS declared on its *parent* `<Layer>`
  element rather than always repeating its own full list, and the CRS
  extraction here originally only read a leaf layer's direct children,
  missing inherited declarations entirely. For a layer that declares no
  CRS of its own, that produced an empty list, which silently fell back
  to a default the server didn't actually support. Fixed by walking the
  full ancestor `<Layer>` chain and collecting CRS/SRS from all of it,
  verified against both this case and the original COSc2025 (WMS 1.3.0,
  CRS declared directly on the leaf) to confirm neither regressed.
- In the discovery-based paths (REST, WFS), if there's exactly one
  option — the normal case for GeoSIFOR, where each API is scoped to a
  single audited layer — it's used automatically with no prompt. If
  there's genuinely more than one, you'll be asked to pick.
- "GeoJSON / Data API" endpoints (the plain `.../v1/dados/...` URLs) are
  loaded directly via QGIS's OGR/GeoJSON driver — no capabilities
  handshake needed, since these return a ready-made FeatureCollection.
- Basic Auth is attached via `authcfg=<id>` on the data source URI for
  every provider used here, the same pattern QGIS's native dialogs use.
- The endpoint list, profiles, and chosen credential all live in
  `QgsSettings`, which is per QGIS *profile*. If you use multiple
  profiles, none of this is shared between them unless you use
  Export/Import (which currently covers endpoints only, not profiles).
- The "load folder as group" behaviour relies on Qt's tristate parent
  checkbox propagation. This has not been verified against a live QGIS
  session — if ticking a folder doesn't tick its children, that's the
  first thing to report back.
- Drag-and-drop reordering/refiling is the least-verified part of this
  build — it intercepts Qt's drop event directly rather than relying on
  the tree widget's built-in move behaviour, specifically so storage
  always stays in sync with what's on screen. If a drag looks like it
  didn't visually complete, or storage doesn't end up matching what you
  dragged where, that's worth reporting with specifics (dragged what,
  onto what).

## Code structure (for anyone reading or forking the source)

- `models.py` — `ServiceType` (an enum, not magic strings) and `Endpoint`
  (a dataclass, not a raw dict). Both convert to/from plain dicts only at
  the storage boundary in `endpoint_store.py`; everywhere else in the
  plugin works with real typed objects.
- `endpoint_store.py` — all persistence (`QgsSettings`, JSON underneath).
  Validates imported JSON explicitly rather than trusting it.
- `layer_loader.py` — turns one `Endpoint` into a real QGIS layer.
  Returns a `LoadResult` (a small dataclass with `.layer`, `.error`,
  `.pending_choices`) instead of a bare tuple.
- `dock_widget.py` — the UI. Keeps a read-only snapshot of saved
  endpoints in `self.endpoints`, refreshed every time `_refresh_list()`
  runs; nothing mutates that snapshot directly, every change goes
  through `endpoint_store` first. The "load selected" flow is split into
  small steps (`_checked_endpoints`, `_split_by_credential`,
  `_load_one_endpoint`, `_show_load_summary`) rather than one long
  function.
- `containers.py` / `geosifor_connector.py` — the QDialog wrapper and the
  QGIS plugin entry point.
- `error_handling.py` — a `@safe_slot` decorator applied to every
  button/menu-triggered method, so an unexpected bug shows a plain
  message instead of silently doing nothing.
- `plugin_paths.py` — the one place the plugin's icon path is resolved.
- Logging goes through Python's standard `logging` module under the
  `"geosifor_connector"` logger, routed to QGIS's own Log Messages panel
  (Plugins tab) by `__init__.py` at load time.

### Background loading (QgsTask)

GetCapabilities/metadata discovery (WFS typenames, WMS/WMTS layer names
and CRS, ArcGIS sub-layer JSON) now runs on a background `QgsTask`
instead of blocking the main/UI thread, so a slow or unresponsive
service — a weak field connection, a government server having a bad day
— shows up as a wait, not as QGIS appearing to hang or crash. This
covers:

- Loading a layer (`load_endpoint_async` in `layer_loader.py`), including
  the layer-choice prompt for multi-layer services.
- The "Multiple layers" indicator in the tree (`has_multiple_layers_async`).

The actual network fetch (`_blocking_get`, still built on
`QgsBlockingNetworkRequest`) is unchanged — what changed is that it now
runs *inside* the background task rather than directly on the main
thread, which is the actual mechanism that keeps the interface
responsive. `QgsTask` objects are tracked in `dock_widget.py`'s
`self._active_tasks` while in flight and released once their callback
fires, since `QgsTask` requires the caller to keep its own reference or
risk the task being garbage-collected mid-flight (a documented footgun
in other PyQGIS plugins).

What this does **not** cover: `QgsVectorLayer`'s own internal fetch for
GeoJSON/Data API endpoints and the final tile/feature streaming for any
already-resolved WFS/WMS/REST layer. Those happen inside QGIS's/GDAL's
own providers after this plugin hands off a finished URI, using
whatever async/streaming behaviour those providers already have
natively — the same as loading any other remote layer in QGIS, not
something specific to this plugin.

### Deliberately not done (and why)

- **Storage stays as one JSON blob in `QgsSettings`, not SQLite.** Fine
  for the realistic size of a personal endpoint list; revisit only if
  this ever needs to scale to hundreds of entries with frequent
  concurrent writes, neither of which describes the actual use case here.