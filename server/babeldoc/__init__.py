__version__ = "0.5.24"

# Package import also runs in spawned PDF workers, before any PDF operations.
from babeldoc.pymupdf_compat import install_compatibility_fixes

install_compatibility_fixes()
