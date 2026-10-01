# SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
# SPDX-License-Identifier: MIT
"""Capability document/CLI regressions; no browser or GPU is launched."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import unquote
from urllib.request import urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from flow_gpu_probe import CAPABILITY_DOCUMENT
from serve import create_server


class FlowProbeDocument(unittest.TestCase):
    def test_capability_document_contains_one_valid_self_contained_favicon(self):
        class Tags(HTMLParser):
            def __init__(self):
                super().__init__(); self.tags = []

            def handle_starttag(self, tag, attrs):
                self.tags.append((tag, dict(attrs)))

        parser = Tags(); parser.feed(CAPABILITY_DOCUMENT); parser.close()
        icons = [attrs for tag, attrs in parser.tags if tag == 'link' and attrs.get('rel') == 'icon']
        self.assertEqual(len(icons), 1)
        self.assertTrue(icons[0]['href'].startswith('data:image/svg+xml,'))
        icon = ET.fromstring(unquote(icons[0]['href'].split(',', 1)[1]))
        self.assertEqual(icon.tag, '{http://www.w3.org/2000/svg}svg')
        self.assertEqual((icon.get('width'), icon.get('height')), ('1', '1'))
        self.assertEqual(len(icon), 0)
        self.assertFalse(any(tag in ('script', 'iframe', 'img', 'object', 'embed') for tag, _ in parser.tags))
        self.assertFalse(any('src' in attrs or (tag != 'link' and 'href' in attrs) for tag, attrs in parser.tags))

    def test_original_loopback_server_serves_document_and_original_security_headers(self):
        # Actual local HTTP only; this does not predict a browser favicon request.
        with tempfile.TemporaryDirectory(prefix='physx-flow-document-test-') as directory:
            root = Path(directory)
            (root / 'index.html').write_text(CAPABILITY_DOCUMENT, encoding='utf-8')
            server = create_server(root=root)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/index.html', timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), 'text/html')
                    self.assertEqual(response.read(), (root / 'index.html').read_bytes())
                    for name, value in {'Cross-Origin-Opener-Policy': 'same-origin',
                            'Cross-Origin-Embedder-Policy': 'require-corp',
                            'Cross-Origin-Resource-Policy': 'same-origin',
                            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'}.items():
                        self.assertEqual(response.headers[name], value)
                self.assertEqual({path.name for path in root.iterdir()}, {'index.html'})
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
        self.assertFalse(root.exists())

    def test_probe_cli_help_and_invalid_arguments_stop_before_browser_import(self):
        # Actual Python CLI invocations, not a replacement browser or GPU.
        cases = [(['--help'], 0, '--browser-engine'),
                 (['--hardware', '--software-vulkan'], 2, 'not allowed with argument'),
                 (['--browser-engine', 'unknown-browser'], 2, 'invalid choice')]
        with tempfile.TemporaryDirectory(prefix='physx-flow-document-cli-') as directory:
            for arguments, expected, text in cases:
                with self.subTest(arguments=arguments):
                    result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'tools/flow_gpu_probe.py'), *arguments],
                        cwd=directory, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                    self.assertIn(text, result.stdout + result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
