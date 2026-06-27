"""
geosifor_connector.py

Main plugin class. QGIS calls initGui() on load and unload() when the
plugin is disabled/removed.

Opens as a real QDialog (minimize/maximize/close, resizable), the same
way QGIS's own Data Source Manager behaves.
"""

import logging

from qgis.PyQt.QtWidgets import QAction

from .containers import GeoSiforDialog
from .plugin_paths import plugin_icon

logger = logging.getLogger("geosifor_connector")


class GeoSiforConnectorPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.dialog: GeoSiforDialog | None = None
        self.action: QAction | None = None

    def initGui(self):
        self.action = QAction(plugin_icon(), "GeoSIFOR Connector", self.iface.mainWindow())
        self.action.triggered.connect(self._open_dialog)

        # addPluginToWebMenu always nests under an auto-created submenu
        # named by its first argument, which for a single action shows up
        # as the redundant "Web > GeoSIFOR Connector > GeoSIFOR Connector".
        # Adding straight to iface.webMenu() puts it directly under Web.
        self.iface.webMenu().addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def _open_dialog(self):
        if self.dialog is None:
            self.dialog = GeoSiforDialog(self.iface, parent=self.iface.mainWindow())
        else:
            self.dialog.refresh()

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def unload(self):
        self.iface.webMenu().removeAction(self.action)
        self.iface.removeToolBarIcon(self.action)

        if self.dialog is not None:
            self.dialog.close()
            self.dialog = None
