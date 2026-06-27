"""
plugin_paths.py

A couple of tiny path helpers shared by containers.py and
geosifor_connector.py, so the plugin's icon is resolved in exactly one
place instead of being duplicated.
"""

from pathlib import Path

from qgis.PyQt.QtGui import QIcon

PLUGIN_DIR = Path(__file__).parent
ICON_PATH = PLUGIN_DIR / "icon.png"


def plugin_icon() -> QIcon:
    """The GeoSIFOR/SGIFR icon, or a blank QIcon if it's missing for some
    reason (e.g. a stripped-down install) -- callers don't need to check
    for that themselves."""
    return QIcon(str(ICON_PATH)) if ICON_PATH.exists() else QIcon()
