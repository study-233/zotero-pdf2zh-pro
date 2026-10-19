"""Compatibility fixes for the PyMuPDF 1.25.x runtime we ship."""

from functools import wraps
from importlib.metadata import version
import warnings

# The pinned wheels predate SWIG's Py_LIMITED_API type-name fix:
# https://github.com/pymupdf/PyMuPDF/issues/3931
# These are binding metadata warnings, emitted on import and interpreter exit.
# Keep every other deprecation and all resource/PDF warnings visible.
if version("pymupdf") in {"1.25.1", "1.25.2"}:
    warnings.filterwarnings(
        "ignore",
        message=r"^builtin type (?:SwigPyPacked|SwigPyObject|swigvarlink) has no __module__ attribute$",
        category=DeprecationWarning,
    )

import pymupdf


def install_compatibility_fixes() -> None:
    # Both JM_get_fontextension and the Python annotation fallback pass printf
    # arguments to message(), whose upstream signature only accepts one string.
    # Keep warnings visible; do not hide PDF parsing errors or change font data.
    if pymupdf.VersionBind not in {"1.25.1", "1.25.2"}:
        return
    original = pymupdf.message
    if getattr(original, "_babeldoc_printf_compatible", False):
        return

    @wraps(original)
    def message(text="", *args):
        return original(text % args if args else text)

    message._babeldoc_printf_compatible = True
    pymupdf.message = message
