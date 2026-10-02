#!/usr/bin/env python3
"""Preview dist/site at the same project subpath used by GitHub Pages."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = '/zotero-pdf2zh-pro/'


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        if self.path == '/':
            self.send_response(302)
            self.send_header('Location', BASE)
            self.end_headers()
            return None
        if not self.path.startswith(BASE):
            self.send_error(404)
            return None
        return super().send_head()

    def translate_path(self, path):
        return super().translate_path('/' + path.removeprefix(BASE))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8895)
    args = parser.parse_args()
    handler = partial(Handler, directory=str(ROOT / 'dist/site'))
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler)
    print(f'Preview: http://127.0.0.1:{args.port}{BASE}', flush=True)
    server.serve_forever()
