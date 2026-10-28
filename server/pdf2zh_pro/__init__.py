"""Product-owned translation core. Importing it does not initialize an engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("zotero-pdf2zh-pro")
except PackageNotFoundError:
    __version__ = "development"
