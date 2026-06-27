# GeoSIFOR Connector — what's new in this version

This consolidates everything changed since the last working baseline
(the original Dialog-vs-Dock decision onward). It's meant as a single
reference for what to actually test, not a turn-by-turn history.

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
- **Select all / Unselect all**, alongside "New folder…" on right-click
  of empty space below the list.
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
- **Add new endpoint form is now a compact grid** instead of one full-width
  field per row: Service type and Folder share a row; the Public checkbox
  and "Add to list" button share a row. Only Label and URL get full width,
  since those are the only fields that actually need it.
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
