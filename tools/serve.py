#!/usr/bin/env python3
"""Loopback-only development server. No uploads, no write API, no production use."""
from __future__ import annotations
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
ROOT=Path(__file__).resolve().parents[1]
class Handler(SimpleHTTPRequestHandler):
    extensions_map={**SimpleHTTPRequestHandler.extensions_map,'.mjs':'text/javascript','.wasm':'application/wasm'}
    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy','same-origin')
        self.send_header('Cross-Origin-Embedder-Policy','require-corp')
        self.send_header('Cross-Origin-Resource-Policy','same-origin')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        super().end_headers()
    def translate_path(self,path):
        # Restrict file access even if a symlink in the kit points outside it.
        resolved=Path(super().translate_path(path)).resolve()
        root=Path(self.directory).resolve()
        if not resolved.is_relative_to(root): return str(root/'__forbidden__')
        relative=resolved.relative_to(root)
        if any(part.startswith('.') for part in relative.parts) or (relative.parts and relative.parts[0]=='work'):
            return str(root/'__forbidden__')
        return str(resolved)
    def list_directory(self,path):
        self.send_error(403,'Directory listings disabled');return None
    def do_GET(self):
        if urlsplit(self.path).path=='/':
            self.send_response(302);self.send_header('Location','/web/index.html');self.end_headers();return
        super().do_GET()
def create_server(port=0,root=ROOT):
    return ThreadingHTTPServer(('127.0.0.1',port),partial(Handler,directory=str(root)))
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8765);a=p.parse_args()
    if not 1<=a.port<=65535:p.error('port must be between 1 and 65535')
    with create_server(a.port) as server:
        print(f'Open http://127.0.0.1:{server.server_port}/web/index.html',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
if __name__=='__main__':main()
