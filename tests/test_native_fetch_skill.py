from __future__ import annotations

import asyncio
import gzip
import http.server
import socket
import threading
import unittest
from unittest.mock import patch

from botboy.skills.native import fetch as fetch_skill
from botboy.skills.native.base import NativeSkillContext
from botboy.skills.native.fetch import FetchError, FetchPolicy, address_block_reason, fetch_url, readable, vet_url

PUBLIC = FetchPolicy()
LAB = FetchPolicy(allow_private_networks=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return None

    def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/page":
            body = (b"<html><head><title>Demo &amp; Test</title><script>var secret = 1;</script>"
                    b"<style>p{}</style></head><body><h1>Hello</h1><p>BotBoy fetched <a href='/next'>this</a>.</p>"
                    b"<ul><li>one</li><li>two</li></ul></body></html>")
            self._send(200, body)
        elif self.path == "/host":
            self._send(200, self.headers.get("Host", "").encode(), "text/plain")
        elif self.path == "/json":
            self._send(200, b'{"b": 1, "a": [1, 2]}', "application/json")
        elif self.path == "/redirect":
            self._send(302, b"", Location="/page")
        elif self.path == "/to-metadata":
            self._send(302, b"", Location="http://169.254.169.254/latest/meta-data/")
        elif self.path == "/loop":
            self._send(302, b"", Location="/loop")
        elif self.path == "/binary":
            self._send(200, b"\x00\x01", "application/octet-stream")
        elif self.path == "/brotli":
            self._send(200, b"xx", "text/plain", Content_Encoding="br")
        elif self.path == "/bomb":
            self._send(200, gzip.compress(b"A" * (40 * 1024 * 1024)), "text/plain", Content_Encoding="gzip")
        elif self.path == "/missing":
            self._send(404, b"<html><body>not here</body></html>")
        else:
            self._send(200, b"ok", "text/plain")


class AddressPolicyTests(unittest.TestCase):
    def test_blocked_ranges(self) -> None:
        blocked = ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "100.64.0.1", "169.254.169.254",
                   "0.0.0.0", "224.0.0.1", "255.255.255.255", "240.0.0.1", "198.18.0.1", "::1", "fe80::1",
                   "fd00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "2002:7f00:1::", "64:ff9b::7f00:1",
                   "64:ff9b::a9fe:a9fe", "ff02::1", "::"]
        for address in blocked:
            with self.subTest(address=address):
                self.assertIsNotNone(address_block_reason(address, PUBLIC))
        for address in ("93.184.216.34", "8.8.8.8", "2606:4700::1111"):
            with self.subTest(address=address):
                self.assertIsNone(address_block_reason(address, PUBLIC))
        # NAT64 sits inside ::/8, which Python classifies as reserved; the
        # skill stays conservative and refuses it even for a public IPv4.
        self.assertIsNotNone(address_block_reason("64:ff9b::808:808", PUBLIC))

    def test_lab_opt_in_keeps_metadata_and_multicast_blocked(self) -> None:
        self.assertIsNone(address_block_reason("192.168.1.10", LAB))
        self.assertIsNone(address_block_reason("127.0.0.1", LAB))
        for address in ("169.254.169.254", "fe80::1", "224.0.0.1", "0.0.0.0", "::ffff:169.254.169.254"):
            with self.subTest(address=address):
                self.assertIsNotNone(address_block_reason(address, LAB))

    def test_url_vetting(self) -> None:
        public = lambda host, port: ["93.184.216.34"]  # noqa: E731
        self.assertEqual(vet_url("https://Example.org/a?b=1", PUBLIC, public).path, "/a?b=1")
        for url in ("file:///etc/passwd", "ftp://example.org/", "gopher://x/", "http://user:pw@example.org/",
                    "http://example.org:22/", "http://", "http://exa mple.org/", "http://bad_host.example/",
                    "javascript:alert(1)", "http://example.org:99999/"):
            with self.subTest(url=url), self.assertRaises(FetchError):
                vet_url(url, PUBLIC, public)

    def test_any_private_answer_blocks_the_host(self) -> None:
        mixed = lambda host, port: ["93.184.216.34", "10.0.0.5"]  # noqa: E731
        with self.assertRaisesRegex(FetchError, "10.0.0.5"):
            vet_url("http://mixed.example/", PUBLIC, mixed)

    def test_dns_failure_and_idna(self) -> None:
        def failing(host, port):
            raise socket.gaierror("no such host")

        with self.assertRaisesRegex(FetchError, "DNS resolution failed"):
            vet_url("http://nowhere.example/", PUBLIC, failing)
        seen = []
        vet_url("http://bücher.example/", PUBLIC, lambda host, port: seen.append(host) or ["93.184.216.34"])
        self.assertEqual(seen, ["xn--bcher-kva.example"])


class FetchServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def policy(self, **overrides) -> FetchPolicy:
        values = {"allow_private_networks": True, "allowed_ports": frozenset({self.port})}
        values.update(overrides)
        return FetchPolicy(**values)

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def test_default_policy_blocks_loopback_server(self) -> None:
        with self.assertRaisesRegex(FetchError, "not a public address"):
            fetch_url(self.url("/page"), FetchPolicy(allowed_ports=frozenset({self.port})))

    def test_html_to_readable_text_with_redirect(self) -> None:
        response = fetch_url(self.url("/redirect"), self.policy())
        self.assertEqual(response.status, 200)
        self.assertEqual(response.redirects, [self.url("/redirect")])
        title, text, links = readable(response)
        self.assertEqual(title, "Demo & Test")
        self.assertIn("# Hello", text)
        self.assertIn("- one", text)
        self.assertNotIn("secret", text)
        self.assertEqual(links, [{"href": self.url("/next"), "text": "this"}])

    def test_connection_is_pinned_to_vetted_address(self) -> None:
        calls = []
        connects = []
        original = socket.create_connection

        def resolver(host, port):
            calls.append(host)
            return ["127.0.0.1"]

        def recording(address, *args, **kwargs):
            connects.append(address[0])
            return original(address, *args, **kwargs)

        with patch("botboy.skills.native.fetch.socket.create_connection", side_effect=recording):
            response = fetch_url(self.url("/host", host="pinned.example"), self.policy(), resolver)
        self.assertEqual(response.body.decode(), f"pinned.example:{self.port}")
        self.assertEqual(calls, ["pinned.example"])
        self.assertEqual(connects, ["127.0.0.1"])

    def test_redirect_into_metadata_range_is_blocked(self) -> None:
        with self.assertRaisesRegex(FetchError, "169.254.169.254"):
            fetch_url(self.url("/to-metadata"), self.policy(allowed_ports=frozenset({self.port, 80})))

    def test_redirect_loops_are_capped(self) -> None:
        with self.assertRaisesRegex(FetchError, "redirects"):
            fetch_url(self.url("/loop"), self.policy())

    def test_content_type_and_encoding_restrictions(self) -> None:
        with self.assertRaisesRegex(FetchError, "non-text"):
            fetch_url(self.url("/binary"), self.policy())
        with self.assertRaisesRegex(FetchError, "content encoding"):
            fetch_url(self.url("/brotli"), self.policy())

    def test_decompression_bomb_is_bounded(self) -> None:
        response = fetch_url(self.url("/bomb"), self.policy(max_bytes=64 * 1024))
        self.assertEqual(len(response.body), 64 * 1024)
        self.assertTrue(response.truncated)

    def test_json_is_pretty_printed(self) -> None:
        _, text, _ = readable(fetch_url(self.url("/json"), self.policy()))
        self.assertIn('"a": [\n    1,', text)

    def test_skill_end_to_end(self) -> None:
        ctx = NativeSkillContext(fetch_allow_private_networks=True)
        lab_policy = lambda ctx, max_bytes: self.policy(max_bytes=max_bytes)  # noqa: E731
        with patch.object(fetch_skill, "policy_for", side_effect=lab_policy):
            ok = asyncio.run(fetch_skill.run(f"fetch {self.url('/page')} --links", ctx))
            missing = asyncio.run(fetch_skill.run(f"fetch {self.url('/missing')}", ctx))
        self.assertTrue(ok["success"], ok)
        self.assertIn("title: Demo & Test", ok["output"])
        self.assertIn("links:", ok["output"])
        self.assertEqual(ok["data"]["status"], 200)
        self.assertFalse(missing["success"])
        self.assertEqual(missing["data"]["status"], 404)

    def test_skill_blocks_without_opt_in(self) -> None:
        for command in ("fetch http://127.0.0.1/", "fetch http://169.254.169.254/latest/meta-data/",
                        "fetch http://[::ffff:127.0.0.1]/", "fetch file:///etc/passwd", "fetch",
                        "fetch http://a.example --max-bytes 1"):
            with self.subTest(command=command):
                result = asyncio.run(fetch_skill.run(command, NativeSkillContext()))
                self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
