"""
containers.py

Thin wrapper around GeoSiforPanel (the actual content widget, in
dock_widget.py) presenting it as a real QDialog: genuine OS-level
titlebar with minimize, maximize, and close, resizing, behaving like
QGIS's own Data Source Manager. Non-modal, so QGIS's status bar (CRS,
scale, log) stays fully usable while it's open.

A QDockWidget-based version existed earlier for snapping into the QGIS
panel layout, but was dropped: QDockWidget's floating/docked behavior
varies awkwardly depending on where it's dragged, and didn't hold up
well in practice. The dialog is simpler and covers the real use case.
"""

from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout
from qgis.PyQt.QtCore import Qt

from .dock_widget import GeoSiforPanel
from .plugin_paths import plugin_icon


class GeoSiforDialog(QDialog):
    """A real top-level window, like Data Source Manager."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("GeoSIFOR Connector")
        self.setWindowIcon(plugin_icon())

        # Real minimize/maximize/close, resizing — same flags QGIS itself
        # uses for non-modal tool windows such as Data Source Manager.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        self.panel = GeoSiforPanel(iface, parent=self)
        layout.addWidget(self.panel)

        self.resize(620, 720)

    def refresh(self):
        self.panel._refresh_list()
