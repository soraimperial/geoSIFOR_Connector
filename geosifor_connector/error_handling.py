"""
error_handling.py

A small decorator for UI action handlers (the methods Qt calls directly
from a button click, menu action, etc). Without this, an unexpected
exception in one of those handlers has nowhere good to go -- PyQt prints
a traceback to QGIS's Python console (which most users never open) and
the action just silently does nothing, leaving the user with no idea
what happened or that anything went wrong at all.

This wraps a handler so that any exception not already handled more
specifically inside it is: logged (to the same QGIS Log Messages panel
used for warnings elsewhere in the plugin), and shown to the user as a
plain, non-technical message box, rather than disappearing.

This is deliberately a last resort, not a substitute for handling
expected, specific failures (bad input, a network error, malformed JSON)
close to where they happen with their own clear messages -- those should
still be caught and explained specifically. @safe_slot exists to catch
the unexpected ones so the user gets *something* useful instead of
nothing.
"""

import functools
import inspect
import logging

from qgis.PyQt.QtWidgets import QMessageBox

logger = logging.getLogger("geosifor_connector")


def safe_slot(func):
    """
    Decorator for a QWidget method (so `self` is the widget, used as the
    parent for the error dialog).

    Qt signals like `clicked` call a connected slot with an extra
    positional argument (e.g. a `checked: bool`), which a plain Python
    function wrapper forwards on to the wrapped method unless something
    accounts for it. Connecting directly to a bound method that doesn't
    declare that parameter works because Qt's own connection machinery
    is lenient about it -- but once a handler is wrapped in an ordinary
    function, that leniency is gone, and the call fails with a
    "takes N positional arguments but N+1 were given" TypeError.

    To keep methods written with whatever signature suits them (most take
    only `self`, a couple take an explicit argument from a non-Qt caller),
    this inspects the real parameter count once, at decoration time, and
    only forwards as many of Qt's positional arguments as the method
    actually declares -- silently dropping the rest, the same leniency a
    direct, undecorated connection would have had.
    """
    real_params = inspect.signature(func).parameters
    # Subtract 1 for `self`, which is always passed separately below.
    max_positional = max(len(real_params) - 1, 0)

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args[:max_positional], **kwargs)
        except Exception as e:  # noqa: BLE001 -- this is the deliberate last-resort catch-all
            logger.exception("Unexpected error in %s", func.__qualname__)
            QMessageBox.critical(
                self,
                "Something went wrong",
                f"An unexpected error occurred and the action could not be completed.\n\n"
                f"({type(e).__name__}: {e})\n\n"
                f"Details have been written to QGIS's Log Messages panel (Plugins tab)."
            )
            return None

    return wrapper
