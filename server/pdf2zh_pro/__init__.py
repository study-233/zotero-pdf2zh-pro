"""Product-owned translation core. Importing it does not initialize an engine."""

from importlib.metadata import PackageNotFoundError, version
import warnings

# macOS PyMuPDF 2.0 wheels still use SWIG 4.3.1. Promoting its type-metadata
# warnings to exceptions can crash the native Layout import (upstream #5190).
# Keep unrelated deprecations and all resource/PDF warnings unchanged. This
# also covers spawned export workers, which do not import BabelDOC.
warnings.filterwarnings(
    "ignore",
    message=r"^builtin type (?:SwigPyPacked|SwigPyObject|swigvarlink) has no __module__ attribute$",
    category=DeprecationWarning,
)

try:
    __version__ = version("zotero-pdf2zh-pro")
except PackageNotFoundError:
    __version__ = "development"
