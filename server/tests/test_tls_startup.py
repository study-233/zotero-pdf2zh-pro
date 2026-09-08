from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TLSStartupTests(unittest.TestCase):
    def test_server_import_enables_verified_system_tls_in_a_fresh_process(self):
        # Exercise the generated console script's import in isolation: another
        # test or a Homebrew wrapper must not supply the SSL initialization.
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["PDF2ZH_DATA_DIR"] = directory
            result = subprocess.run(
                [sys.executable, "-c", """
import ssl
import server
import httpx
import truststore

assert ssl.SSLContext is truststore.SSLContext
ctx = httpx.create_ssl_context(trust_env=False)
assert isinstance(ctx, truststore.SSLContext)
assert ctx.verify_mode == ssl.CERT_REQUIRED
assert ctx.check_hostname is True
"""],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
