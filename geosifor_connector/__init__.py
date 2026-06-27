def classFactory(iface):
    _setup_logging()
    from .geosifor_connector import GeoSiforConnectorPlugin
    return GeoSiforConnectorPlugin(iface)


def _setup_logging():
    """
    Routes this plugin's Python logger to QGIS's own Log Messages panel
    (Plugins tab) instead of going nowhere. Without this, logger.warning()
    etc. calls elsewhere in the plugin produce no visible output anywhere
    a user would actually look -- this is the same panel that was used to
    diagnose the WFS GMLAS schema issue earlier, so routing here keeps
    diagnosis in one familiar place rather than introducing a second log
    destination.
    """
    import logging
    from qgis.core import QgsMessageLog, Qgis

    logger = logging.getLogger("geosifor_connector")
    if logger.handlers:
        return  # already set up (e.g. plugin reloaded without restarting QGIS)

    logger.setLevel(logging.DEBUG)

    class QgisLogHandler(logging.Handler):
        _LEVEL_MAP = {
            logging.DEBUG: Qgis.MessageLevel.Info,
            logging.INFO: Qgis.MessageLevel.Info,
            logging.WARNING: Qgis.MessageLevel.Warning,
            logging.ERROR: Qgis.MessageLevel.Critical,
            logging.CRITICAL: Qgis.MessageLevel.Critical,
        }

        def emit(self, record):
            level = self._LEVEL_MAP.get(record.levelno, Qgis.MessageLevel.Info)
            QgsMessageLog.logMessage(self.format(record), "GeoSIFOR Connector", level)

    handler = QgisLogHandler()
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
