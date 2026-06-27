"""
dock_widget.py

GeoSiforPanel is the actual UI content as a plain QWidget with no opinion
about how it's presented on screen (containers.py wraps it in a QDialog).

Layout, top to bottom:
  - Shared credential picker (QgsAuthConfigSelect), collapsible, set up
    once and rarely revisited.
  - Profiles (save/load/update a named, checked set of endpoints),
    collapsible and collapsed by default -- useful for some workflows,
    not universally used, so it doesn't cost a permanently visible row.
  - Search/filter bar, directly above the endpoint tree.
  - A tree of saved endpoints grouped by folder (plus a "Favorites"
    group for starred ones). Unfiled endpoints sit directly at the top
    level of the tree, not inside any group -- folders are optional,
    never forced. Checking a folder's own checkbox checks every endpoint
    under it ("load as group").
  - "Add selected to map" loads every checked endpoint in one click.
  - "Add new endpoint" form, collapsible, with an optional folder field
    -- "(no folder)" is always available there regardless of what's used
    elsewhere.
  - Export / import row.

self.endpoints is a read-only snapshot of endpoint_store.load_endpoints(),
refreshed every time _refresh_list() runs. Any action that changes data
(add/remove/favorite/move/reorder) writes straight to endpoint_store and
then calls _refresh_list() to bring self.endpoints back in sync --
self.endpoints itself is never mutated in place, so there's no risk of
the in-memory snapshot drifting from what's actually saved.
"""

import logging

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLineEdit, QComboBox, QCheckBox, QLabel, QMessageBox,
    QFileDialog, QInputDialog, QMenu, QAbstractItemView
)
from qgis.PyQt.QtCore import Qt

from qgis.gui import QgsAuthConfigSelect, QgsCollapsibleGroupBox

from . import endpoint_store
from . import layer_loader
from .models import Endpoint, ServiceType
from .error_handling import safe_slot

logger = logging.getLogger("geosifor_connector")

FAVORITES_GROUP = "★ Favorites"
NO_FOLDER_LABEL = "(no folder)"

ENDPOINT_ID_ROLE = Qt.ItemDataRole.UserRole
IS_FOLDER_ROLE = Qt.ItemDataRole.UserRole + 1


class EndpointTree(QTreeWidget):
    """
    QTreeWidget's built-in InternalMove drag-drop only rearranges the
    on-screen tree -- it has no idea about endpoint_store, so left alone
    a drag would visually succeed and then silently revert on the next
    refresh. This subclass intercepts the actual drop, applies the
    equivalent change to endpoint_store, and asks the panel to do a full
    _refresh_list() afterward, so the tree's visual state is always
    rebuilt from storage.
    """

    def __init__(self, on_drop_completed, parent=None):
        super().__init__(parent)
        self._on_drop_completed = on_drop_completed
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        dragged_item = self.currentItem()
        if dragged_item is None or dragged_item.data(0, IS_FOLDER_ROLE):
            event.ignore()
            return

        target_item = self.itemAt(event.position().toPoint()) if hasattr(event, "position") else self.itemAt(event.pos())
        moved_id = dragged_item.data(0, ENDPOINT_ID_ROLE)

        if target_item is None:
            # Dropped on empty space below everything -> treat as unfiled.
            endpoint_store.set_folder_by_id(moved_id, "")
            event.accept()
            self._on_drop_completed()
            return

        if target_item.data(0, IS_FOLDER_ROLE):
            # Dropped directly onto a folder/group row -> file into that
            # folder (Favorites is a view, not a real folder -- skip it).
            target_name = target_item.text(0)
            if target_name != FAVORITES_GROUP:
                endpoint_store.set_folder_by_id(moved_id, target_name)
            event.accept()
            self._on_drop_completed()
            return

        # Dropped onto another endpoint row: adopt that row's folder
        # (handles cross-folder moves) and reorder relative to it.
        target_id = target_item.data(0, ENDPOINT_ID_ROLE)
        target_parent = target_item.parent()
        target_folder = "" if target_parent is None else target_parent.text(0)
        if target_folder == FAVORITES_GROUP:
            target_folder = None  # don't refile into the Favorites view

        if target_folder is not None and moved_id != target_id:
            endpoint_store.set_folder_by_id(moved_id, target_folder)

        indicator = self.dropIndicatorPosition()
        before = indicator in (
            QAbstractItemView.DropIndicatorPosition.AboveItem,
            QAbstractItemView.DropIndicatorPosition.OnItem,
        )
        if moved_id != target_id:
            endpoint_store.reorder_endpoint(moved_id, target_id, before=before)

        event.accept()
        self._on_drop_completed()


class GeoSiforPanel(QWidget):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._updating_tree = False  # guards against feedback loops while bulk-checking
        self.endpoints: list[Endpoint] = []  # read-only snapshot, see module docstring

        # Caches whether an endpoint's service turned out to expose more
        # than one layer, keyed by endpoint id. Checking this requires a
        # GetCapabilities-style network call, so it's deliberately not
        # done eagerly for every endpoint on every _refresh_list() (which
        # runs constantly, e.g. after every favourite toggle) -- instead
        # it's checked once per endpoint, the first time it's rendered,
        # and the result is kept for the rest of this session. A value of
        # None means "not checked yet"; True/False is a real answer.
        self._layer_count_cache: dict[str, bool | None] = {}

        # Background fetches (GetCapabilities/metadata discovery, via
        # layer_loader's *_async functions) run on a QgsTask so a slow or
        # unresponsive server doesn't freeze the whole QGIS interface.
        # QgsTask requires the caller to keep its own reference until the
        # task's callback fires, or it can be garbage-collected mid-flight
        # -- this list is exactly that: active tasks are appended when
        # started and removed once their callback runs, so this is always
        # the live set of "still in flight" background fetches, not a
        # growing leak.
        self._active_tasks: list = []

        self._build_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # --- Credential picker ---
        # Collapsible, since it's set up once and rarely revisited. Starts
        # expanded only the first time, when nothing is configured yet.
        cred_box = QgsCollapsibleGroupBox("Shared credential")
        cred_box.setObjectName("GeoSiforCredentialBox")
        cred_layout = QVBoxLayout(cred_box)
        cred_layout.setSpacing(2)
        cred_layout.setContentsMargins(6, 4, 6, 6)

        self.auth_select = QgsAuthConfigSelect()
        current_authcfg = endpoint_store.get_default_authcfg()
        if current_authcfg:
            self.auth_select.setConfigId(current_authcfg)
        self.auth_select.selectedConfigIdChanged.connect(self._on_authcfg_changed)
        self.auth_select.setToolTip(
            "Pick an existing QGIS authentication (e.g. the one used for your "
            "WMS connection), or use + to create a new one."
        )
        cred_layout.addWidget(self.auth_select)
        cred_box.setCollapsed(bool(current_authcfg))
        layout.addWidget(cred_box)

        # --- Profiles ---
        # Collapsible and collapsed by default -- not universally used.
        profile_box = QgsCollapsibleGroupBox("Profiles")
        profile_box.setObjectName("GeoSiforProfilesBox")
        profile_box.setCollapsed(True)
        profile_layout = QHBoxLayout(profile_box)
        profile_layout.setSpacing(4)
        profile_layout.setContentsMargins(6, 4, 6, 6)

        self.profile_combo = QComboBox()
        profile_layout.addWidget(self.profile_combo, stretch=1)

        save_profile_btn = QPushButton("Save")
        save_profile_btn.setStyleSheet("padding: 2px 8px;")
        save_profile_btn.setToolTip("Update the selected profile with the endpoints currently checked.")
        save_profile_btn.clicked.connect(self._on_save_profile)
        profile_layout.addWidget(save_profile_btn)

        load_profile_btn = QPushButton("Load")
        load_profile_btn.setStyleSheet("padding: 2px 8px;")
        load_profile_btn.setToolTip("Check exactly the endpoints saved in the selected profile.")
        load_profile_btn.clicked.connect(self._on_load_profile)
        profile_layout.addWidget(load_profile_btn)

        new_profile_btn = QPushButton("New…")
        new_profile_btn.setStyleSheet("padding: 2px 8px;")
        new_profile_btn.setToolTip("Create a new profile from the endpoints currently checked.")
        new_profile_btn.clicked.connect(self._on_new_profile)
        profile_layout.addWidget(new_profile_btn)

        delete_profile_btn = QPushButton("Delete")
        delete_profile_btn.setStyleSheet("padding: 2px 8px;")
        delete_profile_btn.clicked.connect(self._on_delete_profile)
        profile_layout.addWidget(delete_profile_btn)

        layout.addWidget(profile_box)
        self._refresh_profiles()

        # --- Search bar, directly above the tree it filters ---
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter endpoints…")
        self.search_input.textChanged.connect(self._refresh_list)
        layout.addWidget(self.search_input)

        # --- Endpoint tree (folders optional; unfiled entries sit at top level) ---
        layout.addWidget(QLabel("Saved endpoints:  (drag to reorder or move between folders)"))
        self.tree = EndpointTree(on_drop_completed=self._refresh_list)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.header().setStretchLastSection(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        # Some QGIS themes render the native checkbox indicator with no
        # visible border between adjacent rows, making a column of
        # checkboxes look like one merged rectangle. Style it explicitly
        # so each checkbox is always a distinct, bordered box.
        self.tree.setStyleSheet("""
            QTreeView::indicator {
                width: 14px;
                height: 14px;
                border: 1px solid palette(mid);
                border-radius: 2px;
                background: palette(base);
                margin: 2px;
            }
            QTreeView::indicator:checked {
                background: palette(highlight);
                border: 1px solid palette(highlight);
            }
            QTreeView::indicator:indeterminate {
                background: palette(midlight);
                border: 1px solid palette(mid);
            }
        """)

        layout.addWidget(self.tree, stretch=1)

        load_btn = QPushButton("Add selected to map")
        layout.addWidget(load_btn)
        load_btn.clicked.connect(self._on_load_selected)

        # --- Add new endpoint form ---
        # Collapsible (same widget QGIS uses for its own panels, e.g.
        # Layer Styling) and collapsed by default -- used occasionally,
        # not the primary working area.
        add_box = QgsCollapsibleGroupBox("Add new endpoint")
        add_box.setObjectName("GeoSiforAddEndpointBox")
        add_box.setCollapsed(True)
        grid = QGridLayout(add_box)
        grid.setSpacing(6)

        self.label_input = QLineEdit()
        grid.addWidget(QLabel("Label:"), 0, 0)
        grid.addWidget(self.label_input, 0, 1, 1, 3)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste the URL copied from the GeoSIFOR viewer")
        grid.addWidget(QLabel("URL:"), 1, 0)
        grid.addWidget(self.url_input, 1, 1, 1, 3)

        # Service type + folder side by side -- neither needs full width.
        self.service_input = QComboBox()
        self.service_input.addItems(ServiceType.values())
        grid.addWidget(QLabel("Service type:"), 2, 0)
        grid.addWidget(self.service_input, 2, 1)

        # "(no folder)" is always present and is the default -- folders
        # are opt-in, never forced, regardless of how many exist.
        self.folder_input = QComboBox()
        self.folder_input.setEditable(True)
        self.folder_input.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        grid.addWidget(QLabel("Folder:"), 2, 2)
        grid.addWidget(self.folder_input, 2, 3)
        self._refresh_folder_combo()

        # Public checkbox + Add button side by side on the last row.
        self.public_checkbox = QCheckBox("Public (no credential needed)")
        grid.addWidget(self.public_checkbox, 3, 0, 1, 2)

        add_btn = QPushButton("Add to list")
        add_btn.clicked.connect(self._on_add_endpoint)
        grid.addWidget(add_btn, 3, 2, 1, 2)

        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        layout.addWidget(add_box)

        # --- Import / export row (secondary actions, kept small) ---
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(4)

        export_btn = QPushButton("Export…")
        import_btn = QPushButton("Import…")

        for btn in (export_btn, import_btn):
            btn.setStyleSheet("padding: 2px 8px;")

        export_btn.clicked.connect(self._on_export)
        import_btn.clicked.connect(self._on_import)

        bottom_row.addStretch()
        bottom_row.addWidget(export_btn)
        bottom_row.addWidget(import_btn)

        layout.addLayout(bottom_row)

    # ------------------------------------------------------------------
    # Refresh helpers
    # ------------------------------------------------------------------

    @safe_slot
    def _on_authcfg_changed(self, authcfg_id: str):
        endpoint_store.set_default_authcfg(authcfg_id)
        self._refresh_list()

    def _track_task(self, task):
        """Keeps a background QgsTask referenced while it's in flight
        (see self._active_tasks in __init__ for why this is necessary),
        removing it once the task manager reports it finished."""
        if task is None:
            return  # the call resolved synchronously, no task was started
        self._active_tasks.append(task)
        task.taskCompleted.connect(lambda: self._untrack_task(task))
        task.taskTerminated.connect(lambda: self._untrack_task(task))

    def _untrack_task(self, task):
        if task in self._active_tasks:
            self._active_tasks.remove(task)

    def _refresh_profiles(self):
        self.profile_combo.clear()
        names = sorted(endpoint_store.load_profiles().keys())
        if names:
            self.profile_combo.addItems(names)
            self.profile_combo.setEnabled(True)
        else:
            self.profile_combo.addItem("(no profiles saved yet)")
            self.profile_combo.setEnabled(False)

    def _refresh_folder_combo(self):
        """"(no folder)" is always the first, always-available option,
        regardless of which real folders currently exist."""
        current_text = self.folder_input.lineEdit().text()
        self.folder_input.blockSignals(True)
        self.folder_input.clear()
        self.folder_input.addItem(NO_FOLDER_LABEL)
        self.folder_input.addItems(endpoint_store.list_folders())
        self.folder_input.blockSignals(False)
        self.folder_input.lineEdit().setText(current_text)

    def _checked_ids(self) -> set[str]:
        """Snapshot of which endpoint ids are currently checked, so a
        rebuild of the tree (e.g. from search/folder changes) can restore
        the user's selection instead of losing it."""
        checked = set()

        def visit(item):
            if not item.data(0, IS_FOLDER_ROLE) and item.checkState(0) == Qt.CheckState.Checked:
                checked.add(item.data(0, ENDPOINT_ID_ROLE))
            for i in range(item.childCount()):
                visit(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
        return checked

    def _refresh_list(self, preserve_checked: set[str] = None):
        """
        The single place that re-reads endpoint_store and rebuilds both
        self.endpoints (the in-memory snapshot every other method reads
        from) and the tree widget. Any action that changes data should
        write to endpoint_store and then call this, rather than trying to
        keep self.endpoints in sync by hand.
        """
        self._updating_tree = True
        try:
            previously_checked = preserve_checked if preserve_checked is not None else self._checked_ids()

            self.tree.clear()
            authcfg = endpoint_store.get_default_authcfg()
            has_cred = bool(authcfg)

            self.endpoints = endpoint_store.load_endpoints()
            endpoints = self.endpoints

            search = self.search_input.text().strip().lower()
            if search:
                endpoints = [
                    e for e in endpoints
                    if search in e.label.lower() or search in e.url.lower()
                    or search in e.folder.lower() or search in e.service.lower()
                ]

            self._refresh_folder_combo()

            groups = {}

            def get_group(name):
                if name not in groups:
                    group_item = QTreeWidgetItem([name])
                    group_item.setFlags(
                        group_item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate
                    )
                    group_item.setCheckState(0, Qt.CheckState.Unchecked)
                    group_item.setData(0, IS_FOLDER_ROLE, True)
                    self.tree.addTopLevelItem(group_item)
                    groups[name] = group_item
                return groups[name]

            # Favourites shown first, as a group, if any exist.
            favorites = [e for e in endpoints if e.favorite]
            if favorites:
                fav_group = get_group(FAVORITES_GROUP)
                for endpoint in favorites:
                    self._add_endpoint_item(fav_group, endpoint, has_cred, previously_checked)

            # Real (named) folders next, shown even when empty so a
            # standalone-created folder stays visible until something is
            # filed into it or it's deleted.
            for folder_name in endpoint_store.list_folders():
                in_folder = [e for e in endpoints if e.folder == folder_name]
                folder_group = get_group(folder_name)
                for endpoint in in_folder:
                    self._add_endpoint_item(folder_group, endpoint, has_cred, previously_checked)

            # Unfiled endpoints sit directly at the tree's top level --
            # using folders is opt-in, never a forced catch-all group.
            unfiled = [e for e in endpoints if not e.folder]
            for endpoint in unfiled:
                self._add_endpoint_item(self.tree, endpoint, has_cred, previously_checked, top_level=True)

            self.tree.expandAll()
        finally:
            self._updating_tree = False

    def _add_endpoint_item(self, parent, endpoint: Endpoint, has_cred: bool, previously_checked: set, top_level: bool = False):
        item = QTreeWidgetItem([""])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(0, ENDPOINT_ID_ROLE, endpoint.id)
        item.setData(0, IS_FOLDER_ROLE, False)
        item.setCheckState(0, Qt.CheckState.Checked if endpoint.id in previously_checked else Qt.CheckState.Unchecked)
        item.setToolTip(0, endpoint.url)

        if top_level:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)

        self._set_endpoint_item_text(item, endpoint, has_cred)
        return item

    def _set_endpoint_item_text(self, item, endpoint: Endpoint, has_cred: bool):
        """
        Sets an endpoint row's label text using whatever's already known
        (instant -- no flicker for an endpoint whose multi-layer status
        was already checked earlier this session). If that status isn't
        known yet, kicks off the async check in the background and
        re-sets this same item's text once the result arrives, rather
        than blocking tree construction on it.
        """
        access = "public" if endpoint.public else ("restricted" if has_cred else "restricted, no credential")
        star = "★ " if endpoint.favorite else ""

        cached = self._layer_count_cache.get(endpoint.id)
        suffix = " · Multiple layers" if cached else ""
        item.setText(0, f"{star}{endpoint.label}   [{endpoint.service.value} · {access}{suffix}]")

        if cached is not None:
            return  # already known, nothing to check

        authcfg = endpoint_store.get_default_authcfg()
        if not endpoint.public and not authcfg:
            return  # restricted with no credential -- nothing to check yet

        def on_checked(is_multiple, err):
            if err:
                # Don't cache a failed check -- a transient network error
                # shouldn't permanently hide the note; just try again
                # next time this item is rendered.
                logger.debug("Layer-count check failed for '%s': %s", endpoint.label, err)
                return
            self._layer_count_cache[endpoint.id] = is_multiple
            # The item may have been replaced by a tree rebuild that
            # happened while this check was in flight (e.g. the user
            # toggled a favourite) -- re-setting text on a deleted Qt
            # object would raise, so this is deliberately tolerant of
            # that rather than trying to track item identity across
            # rebuilds.
            try:
                self._set_endpoint_item_text(item, endpoint, has_cred)
            except RuntimeError:
                pass

        task = layer_loader.has_multiple_layers_async(endpoint, authcfg, on_checked)
        self._track_task(task)

    @safe_slot
    def _on_item_double_clicked(self, item, column):
        if item.data(0, IS_FOLDER_ROLE):
            return
        self._toggle_favorite_for_item(item)

    def _toggle_favorite_for_item(self, item):
        endpoint_id = item.data(0, ENDPOINT_ID_ROLE)
        for idx, e in enumerate(self.endpoints):
            if e.id == endpoint_id:
                endpoint_store.set_favorite(idx, not e.favorite)
                break
        self._refresh_list()

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    @safe_slot
    def _on_tree_context_menu(self, pos):
        item = self.tree.itemAt(pos)

        if item is None:
            self._show_empty_space_context_menu(pos)
            return

        # Right-click acts on the whole current selection if the clicked
        # row is already part of it; otherwise it replaces the selection
        # with just the clicked row (matching most file managers).
        if item not in self.tree.selectedItems():
            self.tree.setCurrentItem(item)

        selected = self.tree.selectedItems()
        folders_selected = [i for i in selected if i.data(0, IS_FOLDER_ROLE)]
        endpoints_selected = [i for i in selected if not i.data(0, IS_FOLDER_ROLE)]

        if folders_selected and not endpoints_selected:
            self._show_folder_context_menu(folders_selected, pos)
        elif endpoints_selected and not folders_selected:
            self._show_endpoint_context_menu(endpoints_selected, pos)
        # A mix of folder + endpoint rows selected together gets no menu --
        # "delete folder" and "move to folder" don't share a sensible
        # combined action, so the user is left to select one kind at a time.

    def _show_empty_space_context_menu(self, pos):
        menu = QMenu(self)
        select_all_action = menu.addAction("Select all")
        unselect_all_action = menu.addAction("Unselect all")
        menu.addSeparator()
        new_folder_action = menu.addAction("New folder…")

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))

        if chosen == select_all_action:
            self._set_all_checked(True)
        elif chosen == unselect_all_action:
            self._set_all_checked(False)
        elif chosen == new_folder_action:
            name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
            if ok and name.strip():
                try:
                    endpoint_store.create_folder(name.strip())
                except ValueError as e:
                    QMessageBox.warning(self, "Invalid folder name", str(e))
                    return
                self._refresh_list()

    def _set_all_checked(self, checked: bool):
        """Checks or unchecks every row in the tree, leaf endpoints and
        folder/group rows alike."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked

        def visit(item):
            item.setCheckState(0, state)
            for i in range(item.childCount()):
                visit(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _show_folder_context_menu(self, folder_items, pos):
        folder_names = [i.text(0) for i in folder_items if i.text(0) != FAVORITES_GROUP]
        if not folder_names:
            return  # only Favorites was selected -- nothing to do

        menu = QMenu(self)
        rename_action = None
        if len(folder_names) == 1:
            rename_action = menu.addAction("Rename folder")
        label = "Delete folder" if len(folder_names) == 1 else f"Delete {len(folder_names)} folders"
        delete_action = menu.addAction(f"{label} (endpoints stay, just unfiled)")

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if rename_action is not None and chosen == rename_action:
            old_name = folder_names[0]
            new_name, ok = QInputDialog.getText(self, "Rename folder", "Folder name:", text=old_name)
            if ok and new_name.strip():
                try:
                    endpoint_store.rename_folder(old_name, new_name.strip())
                except ValueError as e:
                    QMessageBox.warning(self, "Invalid folder name", str(e))
                    return
                self._refresh_list()
            return

        if chosen != delete_action:
            return

        count = sum(1 for e in self.endpoints if e.folder in folder_names)
        names_preview = ", ".join(folder_names)
        confirm = QMessageBox.question(
            self, "Delete folder" if len(folder_names) == 1 else "Delete folders",
            f"Delete {names_preview}?\n\n"
            f"{count} endpoint(s) inside will become unfiled, not removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            for folder_name in folder_names:
                endpoint_store.delete_folder(folder_name)
            self._refresh_list()

    def _show_endpoint_context_menu(self, endpoint_items, pos):
        ids = {i.data(0, ENDPOINT_ID_ROLE) for i in endpoint_items}
        selected = [e for e in self.endpoints if e.id in ids]
        if not selected:
            return

        multi = len(selected) > 1

        menu = QMenu(self)

        # Only offered for a single selection -- "add a layer" doesn't
        # make sense for several endpoints at once, and this is the
        # direct way to pull another layer out of a multi-layer service
        # without needing to tick its checkbox and run the full batch
        # "Add selected to map" flow just to get the choice prompt again.
        add_layer_action = None
        if not multi:
            add_layer_action = menu.addAction("Add a layer from this service…")
            menu.addSeparator()

        if multi:
            fav_action = menu.addAction(f"Add {len(selected)} to Favorites")
            unfav_action = menu.addAction(f"Remove {len(selected)} from Favorites")
        else:
            fav_label = "Remove from Favorites" if selected[0].favorite else "Add to Favorites"
            fav_action = menu.addAction(fav_label)
            unfav_action = None

        folder_menu = menu.addMenu("Move to folder…")
        no_folder_action = folder_menu.addAction(NO_FOLDER_LABEL)
        existing_folders = endpoint_store.list_folders()
        if existing_folders:
            folder_menu.addSeparator()
            for folder_name in existing_folders:
                folder_menu.addAction(folder_name)
        folder_menu.addSeparator()
        new_folder_action = folder_menu.addAction("New folder…")

        menu.addSeparator()
        if not multi:
            rename_action = menu.addAction("Rename")
        remove_label = "Remove" if not multi else f"Remove {len(selected)} endpoints"
        remove_action = menu.addAction(remove_label)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return

        # Re-fetch indices fresh each time rather than reusing stale ones,
        # since an earlier action in this same call could already have
        # changed list positions.
        def current_indices():
            current = endpoint_store.load_endpoints()
            return [idx for idx, e in enumerate(current) if e.id in ids]

        if add_layer_action is not None and chosen == add_layer_action:
            self._add_layer_from_endpoint(selected[0])
        elif chosen == fav_action:
            if multi:
                for idx in current_indices():
                    endpoint_store.set_favorite(idx, True)
            else:
                # Toggle relative to current state, not a hardcoded value --
                # the single-selection label flips between Add/Remove
                # depending on current state, so the action must too.
                new_state = not selected[0].favorite
                for idx in current_indices():
                    endpoint_store.set_favorite(idx, new_state)
            self._refresh_list()
        elif unfav_action is not None and chosen == unfav_action:
            for idx in current_indices():
                endpoint_store.set_favorite(idx, False)
            self._refresh_list()
        elif chosen == no_folder_action:
            for idx in current_indices():
                endpoint_store.set_folder(idx, "")
            self._refresh_list()
        elif chosen == new_folder_action:
            name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
            if ok and name.strip():
                for idx in current_indices():
                    endpoint_store.set_folder(idx, name.strip())
                self._refresh_list()
        elif not multi and chosen == rename_action:
            current = selected[0]
            new_label, ok = QInputDialog.getText(self, "Rename endpoint", "Label:", text=current.label)
            if ok and new_label.strip():
                for idx in current_indices():
                    endpoint_store.set_label(idx, new_label.strip())
                self._refresh_list()
        elif chosen == remove_action:
            labels = [e.label for e in selected]
            if multi:
                confirm = QMessageBox.question(
                    self, "Remove endpoints",
                    f"Remove {len(selected)} endpoints?\n\n" + "\n".join(f"- {l}" for l in labels),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
            for idx in sorted(current_indices(), reverse=True):
                endpoint_store.remove_endpoint(idx)
            self._refresh_list()
        elif chosen in folder_menu.actions():
            for idx in current_indices():
                endpoint_store.set_folder(idx, chosen.text())
            self._refresh_list()

    # ------------------------------------------------------------------
    # Add endpoint form
    # ------------------------------------------------------------------

    @safe_slot
    def _on_add_endpoint(self):
        label = self.label_input.text().strip()
        url = self.url_input.text().strip()
        service = self.service_input.currentText()
        public = self.public_checkbox.isChecked()

        folder_text = self.folder_input.lineEdit().text().strip()
        folder = "" if folder_text == NO_FOLDER_LABEL else folder_text

        if not label or not url:
            QMessageBox.warning(self, "Missing info", "Label and URL are both required.")
            return

        try:
            endpoint_store.add_endpoint(label, url, service, public, folder=folder)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid endpoint", str(e))
            return

        self.label_input.clear()
        self.url_input.clear()
        self.public_checkbox.setChecked(False)
        self.folder_input.lineEdit().setText(NO_FOLDER_LABEL)
        self._refresh_list()

    # ------------------------------------------------------------------
    # Loading endpoints onto the map
    #
    # Split into small steps (selection -> credential check -> per-item
    # load -> summary) instead of one long function, so each step can be
    # read (and tested) on its own.
    # ------------------------------------------------------------------

    def _checked_endpoints(self) -> list[Endpoint]:
        by_id = {e.id: e for e in self.endpoints}
        checked = []

        def visit(item):
            if not item.data(0, IS_FOLDER_ROLE) and item.checkState(0) == Qt.CheckState.Checked:
                endpoint = by_id.get(item.data(0, ENDPOINT_ID_ROLE))
                if endpoint:
                    checked.append(endpoint)
            for i in range(item.childCount()):
                visit(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))
        return checked

    def _split_by_credential(self, checked: list[Endpoint], authcfg: str):
        """Splits checked endpoints into (loadable, blocked), where
        blocked = restricted endpoints with no credential available."""
        blocked = [e for e in checked if not e.public and not authcfg]
        blocked_ids = {e.id for e in blocked}
        loadable = [e for e in checked if e.id not in blocked_ids]
        return loadable, blocked

    def _load_one_endpoint_async(self, endpoint: Endpoint, authcfg: str, on_done):
        """
        Loads a single endpoint, prompting for a sub-layer/typename/CRS
        choice and retrying if the service offers more than one option --
        without blocking the calling thread while the discovery
        (GetCapabilities/metadata) step runs.

        on_done(succeeded: bool, label: str, error_or_None, was_cancelled: bool)
        is called exactly once, always on the main thread.

        Any unexpected exception from layer_loader is caught here rather
        than left to propagate -- this is used both for single-endpoint
        loads and inside a sequential loop over several checked
        endpoints, so one endpoint hitting an unexpected error shouldn't
        abort loading the rest or crash the plugin; it should just be
        reported as that one endpoint's failure.
        """
        label = endpoint.label or endpoint.url

        def handle_result(result):
            if result.needs_choice:
                chosen = self._prompt_choice(label, result.pending_choices)
                if chosen is None:
                    on_done(False, label, None, True)  # user cancelled the prompt
                    return
                chosen_value, chosen_display_name = chosen
                try:
                    task = layer_loader.load_endpoint_async(
                        endpoint, authcfg, handle_result,
                        choice=chosen_value, choice_display_name=chosen_display_name,
                    )
                    self._track_task(task)
                except Exception as e:  # noqa: BLE001 -- last resort, see docstring
                    logger.exception("Unexpected error loading endpoint '%s'", label)
                    on_done(False, label, f"Unexpected error ({type(e).__name__}: {e})", False)
                return

            if result.ok:
                on_done(True, label, None, False)
            else:
                on_done(False, label, result.error or "Unknown error", False)

        try:
            task = layer_loader.load_endpoint_async(endpoint, authcfg, handle_result)
            self._track_task(task)
        except Exception as e:  # noqa: BLE001 -- last resort so one bad endpoint doesn't abort the batch
            logger.exception("Unexpected error loading endpoint '%s'", label)
            on_done(False, label, f"Unexpected error ({type(e).__name__}: {e})", False)

    def _add_layer_from_endpoint(self, endpoint: Endpoint):
        """
        Right-click action: loads one more layer from a single endpoint
        immediately, prompting for which layer if the service offers more
        than one -- without needing to tick its checkbox and run it
        through the full "Add selected to map" batch flow. Reuses
        _load_one_endpoint_async directly so the prompting/error-reporting
        logic isn't duplicated.
        """
        authcfg = endpoint_store.get_default_authcfg()
        if not endpoint.public and not authcfg:
            QMessageBox.information(
                self, "No credential set",
                f"'{endpoint.label}' is restricted and no credential is selected above."
            )
            return

        def on_done(ok, label, error, was_cancelled):
            if ok:
                self.iface.messageBar().pushSuccess("GeoSIFOR Connector", f"Loaded layer from '{label}'.")
            elif was_cancelled:
                pass  # user cancelled the layer-choice prompt; nothing to report
            else:
                logger.warning("Failed to load a layer from '%s': %s", label, error)
                QMessageBox.warning(self, "Could not load layer", error or "Unknown error")

        self._load_one_endpoint_async(endpoint, authcfg, on_done)

    def _show_load_summary(self, succeeded: list[str], failed: list, blocked: list[Endpoint], cancelled: list[str], total_loadable: int):
        skipped_note = f"\n\nSkipped (no credential): {', '.join(e.label for e in blocked)}" if blocked else ""
        cancelled_note = f"\n\nCancelled (no choice made): {', '.join(cancelled)}" if cancelled else ""

        if failed or blocked or cancelled:
            details = "\n".join(f"- {label}: {err}" for label, err in failed)
            QMessageBox.warning(
                self, "Some endpoints did not load",
                f"{len(succeeded)} of {total_loadable} loaded successfully.\n\nFailures:\n{details}{skipped_note}{cancelled_note}"
            )
        else:
            self.iface.messageBar().pushSuccess(
                "GeoSIFOR Connector", f"Loaded {len(succeeded)} layer(s) successfully."
            )

    @safe_slot
    def _on_load_selected(self):
        checked = self._checked_endpoints()
        if not checked:
            QMessageBox.information(self, "Nothing selected", "Tick at least one endpoint (or a whole folder) to load.")
            return

        authcfg = endpoint_store.get_default_authcfg()
        loadable, blocked = self._split_by_credential(checked, authcfg)

        if blocked and not loadable:
            names = ", ".join(e.label for e in blocked)
            QMessageBox.information(
                self, "No credential set",
                f"Skipped: {names}\n\nThese are restricted and no credential is selected above."
            )
            return

        succeeded, failed, cancelled = [], [], []

        # Endpoints are loaded one at a time (each waits for the previous
        # one's callback before starting) rather than all at once, mainly
        # so the layer-choice prompt, if one comes up, is shown one
        # dialog at a time instead of several popping up together.
        remaining = list(loadable)

        def load_next():
            if not remaining:
                self._show_load_summary(succeeded, failed, blocked, cancelled, len(loadable))
                return

            endpoint = remaining.pop(0)

            def on_done(ok, label, error, was_cancelled):
                if ok:
                    succeeded.append(label)
                elif was_cancelled:
                    cancelled.append(label)
                else:
                    logger.warning("Failed to load '%s': %s", label, error)
                    failed.append((label, error))
                load_next()

            self._load_one_endpoint_async(endpoint, authcfg, on_done)

        load_next()

    def _prompt_choice(self, label: str, choices: list):
        """
        choices is a list of (value, display_label) tuples. Only
        display_label is ever shown. Returns (value, display_label) for
        whichever the user picked, or None if they cancelled.
        """
        items = [display for _, display in choices]
        chosen, ok = QInputDialog.getItem(
            self, f"Choose layer for '{label}'",
            "This service has more than one layer. Pick one:",
            items, editable=False
        )
        if not ok or not chosen:
            return None

        chosen_index = items.index(chosen)
        return choices[chosen_index]  # (value, display_label)

    # ------------------------------------------------------------------
    # Export / import
    # ------------------------------------------------------------------

    @safe_slot
    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export endpoint list", "geosifor_endpoints.json", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(endpoint_store.export_to_json_string())
        QMessageBox.information(self, "Exported", f"Endpoint list saved to:\n{path}")

    @safe_slot
    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import endpoint list", "", "JSON (*.json)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()

        choice = QMessageBox.question(
            self, "Import mode",
            "Replace the current list entirely?\n\n"
            "Yes = replace, No = merge (skip duplicates)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return

        try:
            endpoint_store.import_from_json_string(raw, replace=(choice == QMessageBox.StandardButton.Yes))
        except ValueError as e:
            logger.warning("Import failed: %s", e)
            QMessageBox.critical(self, "Import failed", str(e))
            return

        self._refresh_list()

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    @safe_slot
    def _on_save_profile(self):
        """Updates the currently-selected profile in place with whatever
        is checked right now. Does not prompt for a name -- that's what
        'New…' is for."""
        profiles = endpoint_store.load_profiles()
        name = self.profile_combo.currentText()
        if name not in profiles:
            QMessageBox.information(
                self, "No profile selected",
                "Select a profile to update, or use 'New…' to create one."
            )
            return

        checked = self._checked_endpoints()
        if not checked:
            QMessageBox.information(self, "Nothing checked", "Tick the endpoints you want in this profile first.")
            return

        endpoint_store.save_profile(name, [e.id for e in checked])
        self.iface.messageBar().pushSuccess(
            "GeoSIFOR Connector", f"Profile '{name}' updated with {len(checked)} endpoint(s)."
        )

    @safe_slot
    def _on_new_profile(self):
        checked = self._checked_endpoints()
        if not checked:
            QMessageBox.information(self, "Nothing checked", "Tick the endpoints you want in this profile first.")
            return

        existing_names = sorted(endpoint_store.load_profiles().keys())
        name, ok = QInputDialog.getText(
            self, "New profile",
            "Profile name (e.g. 'Operational Decision', 'Fuels'):"
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if name in existing_names:
            confirm = QMessageBox.question(
                self, "Overwrite profile?",
                f"A profile named '{name}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        endpoint_store.save_profile(name, [e.id for e in checked])
        self._refresh_profiles()
        index = self.profile_combo.findText(name)
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        QMessageBox.information(self, "Created", f"Profile '{name}' created with {len(checked)} endpoint(s).")

    @safe_slot
    def _on_load_profile(self):
        profiles = endpoint_store.load_profiles()
        name = self.profile_combo.currentText()
        if name not in profiles:
            return
        self._refresh_list(preserve_checked=set(profiles[name]))

    @safe_slot
    def _on_delete_profile(self):
        profiles = endpoint_store.load_profiles()
        name = self.profile_combo.currentText()
        if name not in profiles:
            return

        confirm = QMessageBox.question(
            self, "Delete profile",
            f"Delete the profile '{name}'? This won't remove any endpoints, just the saved selection.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        endpoint_store.delete_profile(name)
        self._refresh_profiles()
