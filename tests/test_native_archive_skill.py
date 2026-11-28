"""``archive`` native skill: listing, verification, reading, and hostile archives."""
from __future__ import annotations

import asyncio
import bz2
import gzip
import io
import lzma
import os
import struct
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from botboy.skills.native import archive
from botboy.skills.native.base import NativeSkillContext


def run(command: str, ctx: NativeSkillContext) -> dict:
    return asyncio.run(archive.run(command, ctx))


def zip_bytes(members, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for item in members:
            if isinstance(item, zipfile.ZipInfo):
                zf.writestr(item, b"")
            else:
                name, data, *method = item
                zf.writestr(name, data, compress_type=method[0] if method else compression)
    return buffer.getvalue()


def patch_central(data: bytes, index: int, **fields) -> bytes:
    """Rewrite fields of the index-th central directory record."""
    offsets = {"crc": (16, "<I"), "compressed": (20, "<I"), "size": (24, "<I"), "offset": (42, "<I"),
               "flags": (8, "<H")}
    buf = bytearray(data)
    position = buf.find(b"PK\x01\x02")
    for _ in range(index):
        name_len, extra_len, comment_len = struct.unpack("<HHH", buf[position + 28:position + 34])
        position += 46 + name_len + extra_len + comment_len
    for name, value in fields.items():
        offset, fmt = offsets[name]
        struct.pack_into(fmt, buf, position + offset, value)
    return bytes(buf)


def tar_bytes(members, mode="w", fmt=tarfile.PAX_FORMAT) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode, format=fmt) as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def tinfo(name, size=0, type_=tarfile.REGTYPE, link="", mode=0o644):
    info = tarfile.TarInfo(name)
    info.size, info.type, info.linkname, info.mode = size, type_, link, mode
    return info


class ArchiveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.ws = self.root / "ws"
        self.ws.mkdir()
        self.ctx = NativeSkillContext(workspace_root=self.ws)

    def put(self, name: str, data: bytes) -> str:
        (self.ws / name).write_bytes(data)
        return name

    def listing(self, name: str) -> dict:
        out = run(f"archive list {name}", self.ctx)
        self.assertTrue(out["success"], out["output"])
        return out

    def flags(self, out: dict) -> dict:
        return {entry["name"]: entry["flags"] for entry in out["data"]["listed"]}


class ZipTests(ArchiveTestCase):
    def test_list_check_read_every_zip_method(self) -> None:
        self.put("good.zip", zip_bytes([
            ("src/README.md", b"# Hello\nworld\n", zipfile.ZIP_DEFLATED),
            ("src/b.txt", b"bzip2 text " * 300, zipfile.ZIP_BZIP2),
            ("src/l.txt", b"lzma text " * 300, zipfile.ZIP_LZMA),
            ("stored.txt", b"plain", zipfile.ZIP_STORED),
            ("src/", b""),
        ]))
        out = self.listing("good.zip")
        self.assertEqual(out["data"]["verdict"], "ok")
        self.assertEqual(out["data"]["files"], 4)
        check = run("archive check good.zip", self.ctx)
        self.assertTrue(check["success"], check["output"])
        self.assertEqual(check["data"]["verified"], 4)
        self.assertEqual(check["data"]["problems"], [])
        for member, needle in (("src/README.md", "world"), ("src/b.txt", "bzip2 text"),
                               ("src/l.txt", "lzma text"), ("stored.txt", "plain")):
            with self.subTest(member=member):
                read = run(f"archive read good.zip {member}", self.ctx)
                self.assertTrue(read["success"], read["output"])
                self.assertIn(needle, read["data"]["text"])
        self.assertIn("is a dir", run("archive read good.zip src/", self.ctx)["output"])
        self.assertIn("no member", run("archive read good.zip missing.txt", self.ctx)["output"])

    def test_unsafe_names_and_links_are_flagged(self) -> None:
        link = zipfile.ZipInfo("docs/link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        inner_link = zipfile.ZipInfo("docs/inner")
        inner_link.create_system = 3
        inner_link.external_attr = 0o120777 << 16
        fifo = zipfile.ZipInfo("pipe")
        fifo.create_system = 3
        fifo.external_attr = 0o010644 << 16
        backslashed = zipfile.ZipInfo("placeholder")
        backslashed.filename = "a\\..\\..\\b.txt"  # ZipInfo() turns "\\" into "/" on Windows
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            for name in ("../../etc/cron.d/x", "/etc/passwd", "C:/Windows/evil.dll",
                         "CON.txt", "Readme.md", "README.md", "spoof\u202etxt.exe", "line\nforged: ok"):
                zf.writestr(name, b"x")
            zf.writestr(backslashed, b"x")
            zf.writestr(link, b"../../../home/user/.ssh/authorized_keys")
            zf.writestr(inner_link, b"../docs/README")
            zf.writestr(fifo, b"")
        self.put("evil.zip", buffer.getvalue())
        out = self.listing("evil.zip")
        flags = self.flags(out)
        self.assertEqual(out["data"]["verdict"], "unsafe")
        self.assertIn("traversal", flags["../../etc/cron.d/x"])
        self.assertIn("absolute", flags["/etc/passwd"])
        self.assertIn("absolute", flags["C:/Windows/evil.dll"])
        self.assertIn("traversal", flags["a\\..\\..\\b.txt"])
        self.assertIn("backslash", flags["a\\..\\..\\b.txt"])
        self.assertIn("windows-unsafe", flags["CON.txt"])
        self.assertIn("case-collision", flags["README.md"])
        self.assertIn("control-chars", flags["spoof\\u202etxt.exe"])
        self.assertIn("link-escapes", flags["docs/link"])
        self.assertNotIn("link-escapes", flags["docs/inner"])
        self.assertIn("device", flags["pipe"])
        # Names cannot forge report lines or hide behind bidi controls.
        self.assertNotIn("\u202e", out["output"])
        self.assertNotIn("\nforged: ok", out["output"])
        self.assertIn("line\\x0aforged: ok", out["output"])
        self.assertIn("-> ../../../home/user/.ssh/authorized_keys", out["output"])

    def test_zip_link_chains_are_resolved(self) -> None:
        def link(name):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            return info

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(link("a"), b".")
            zf.writestr(link("a/b"), b"..")
            zf.writestr("b/payload", b"pwn")
        self.put("chain.zip", buffer.getvalue())
        flags = self.flags(self.listing("chain.zip"))
        self.assertIn("link-escapes", flags["a/b"])
        self.assertIn("link-traversal", flags["b/payload"])

    def test_declared_sizes_are_not_trusted(self) -> None:
        payload = b"A" * 100_000
        original = zip_bytes([("a.txt", payload), ("b.txt", b"fine")])
        self.put("lie.zip", patch_central(original, 0, size=10))
        check = run("archive check lie.zip", self.ctx)
        self.assertEqual(check["data"]["verdict"], "unsafe")
        self.assertIn("size-mismatch", self.flags_of(check)["a.txt"])
        self.put("crc.zip", patch_central(original, 1, crc=0xDEADBEEF))
        check = run("archive check crc.zip", self.ctx)
        self.assertIn("crc-mismatch", self.flags_of(check)["b.txt"])
        read = run("archive read crc.zip b.txt", self.ctx)
        self.assertFalse(read["success"])
        self.assertIn("CRC mismatch", read["output"])

    def flags_of(self, out: dict) -> dict:
        return {entry["name"]: entry["flags"] for entry in out["data"]["flagged"]}

    def test_overlapping_entries_and_name_mismatch(self) -> None:
        # Fifield-style: a second central record points at the first local
        # header, so one stored body is counted (and extracted) twice.
        data = patch_central(zip_bytes([("a.txt", b"x" * 1000), ("b.txt", b"y" * 1000)]), 1, offset=0)
        self.put("overlap.zip", data)
        out = self.listing("overlap.zip")
        flags = self.flags(out)
        self.assertIn("overlap", flags["a.txt"])
        self.assertIn("overlap", flags["b.txt"])
        self.assertIn("name-mismatch", flags["b.txt"])
        self.assertEqual(out["data"]["verdict"], "unsafe")

    def test_encrypted_member_is_reported_not_read(self) -> None:
        self.put("enc.zip", patch_central(zip_bytes([("secret.txt", b"data", zipfile.ZIP_STORED)]), 0, flags=0x1))
        out = self.listing("enc.zip")
        self.assertIn("encrypted", self.flags(out)["secret.txt"])
        self.assertIn("encrypted", run("archive read enc.zip secret.txt", self.ctx)["output"])

    def test_bzip2_bomb_member_is_bounded(self) -> None:
        # zipfile itself decompresses bzip2 members without an output bound on
        # older Pythons; this skill's reader produces 64 KiB per call.
        self.put("bomb.zip", zip_bytes([("zeros.bin", b"\0" * (64 * 1024 * 1024), zipfile.ZIP_BZIP2)]))
        listing = self.listing("bomb.zip")
        self.assertIn("high-ratio", self.flags(listing)["zeros.bin"])
        with mock.patch.object(archive, "STREAM_BUDGET_BYTES", 8 * 1024 * 1024):
            started = time.perf_counter()
            check = run("archive check bomb.zip", self.ctx)
        self.assertTrue(check["success"], check["output"])
        self.assertTrue(check["data"]["incomplete"])
        self.assertEqual(check["data"]["verdict"], "unsafe")
        self.assertLessEqual(check["data"]["decompressed_total"], 8 * 1024 * 1024 + archive.CHUNK)
        self.assertLess(time.perf_counter() - started, 10)
        read = run("archive read bomb.zip zeros.bin", self.ctx)
        self.assertIn("binary", read["output"])

    def test_central_directory_cap(self) -> None:
        self.put("many.zip", zip_bytes([(f"f{i:05d}.txt", b"") for i in range(2000)]))
        with mock.patch.object(archive, "MAX_CENTRAL_DIRECTORY_BYTES", 4096):
            out = run("archive list many.zip", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("central directory larger", out["output"])
        self.assertEqual(self.listing("many.zip")["data"]["entries"], 2000)

    def test_member_data_may_not_run_into_the_central_directory(self) -> None:
        # Review finding: compress_size extended into the central directory
        # verified as ok (inflate stops at its own end marker); zipfile's
        # extractor rejects it as "Overlapped entries".
        original = zip_bytes([("a.txt", b"hello " * 200)])
        compressed = zipfile.ZipFile(io.BytesIO(original)).getinfo("a.txt").compress_size
        self.put("intocd.zip", patch_central(original, 0, compressed=compressed + 20))
        check = run("archive check intocd.zip", self.ctx)
        self.assertEqual(check["data"]["verdict"], "unsafe", check["output"])
        self.assertIn("overlap", self.flags_of(check)["a.txt"])
        self.assertIn("runs into the central directory", run("archive list intocd.zip", self.ctx)["output"])
        self.assertEqual(self.listing(self.put("plain.zip", original))["data"]["verdict"], "ok")

    def test_malformed_lzma_properties_are_an_archive_error(self) -> None:
        # Review finding: properties byte 8 (lc=8) passed the range check and
        # LZMADecompressor() raised an uncaught LZMAError.
        data = bytearray(zip_bytes([("l.txt", b"lzma " * 100, zipfile.ZIP_LZMA)]))
        props = data.find(b"\x5d", 30)
        data[props] = 8
        self.put("props.zip", bytes(data))
        for command in ("archive check props.zip", "archive read props.zip l.txt"):
            with self.subTest(command=command):
                out = run(command, self.ctx)
                self.assertTrue(out["output"].startswith(("archive", "props.zip")), out["output"])
                if out["success"]:
                    self.assertEqual(out["data"]["verdict"], "unsafe")
                else:
                    self.assertIn("LZMA", out["output"])

    def test_lzma_dictionary_cap(self) -> None:
        data = bytearray(zip_bytes([("l.txt", b"lzma " * 100, zipfile.ZIP_LZMA)]))
        props = data.find(b"\x5d", 30)  # lc=3 lp=0 pb=2 properties byte after the 4-byte header
        struct.pack_into("<I", data, props + 1, 0xFFFFFFFF)
        self.put("dict.zip", bytes(data))
        out = run("archive read dict.zip l.txt", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("dictionary", out["output"])

    def test_garbage_and_truncated_zips_fail_cleanly(self) -> None:
        good = zip_bytes([("a.txt", b"hello " * 1000)])
        self.put("trunc.zip", good[: len(good) // 2])
        self.put("junk.zip", b"PK\x03\x04" + os.urandom(2000))
        for name in ("trunc.zip", "junk.zip"):
            with self.subTest(name=name):
                out = run(f"archive list {name}", self.ctx)
                self.assertFalse(out["success"])
                self.assertTrue(out["output"].startswith("archive:"))


class TarTests(ArchiveTestCase):
    def members(self):
        return [
            (tinfo("docs/a.txt", 6), b"first\n"),
            (tinfo("docs/link", type_=tarfile.SYMTYPE, link="/etc/shadow"), None),
            (tinfo("docs/rel", type_=tarfile.SYMTYPE, link="../docs/a.txt"), None),
            (tinfo("docs/hard", type_=tarfile.LNKTYPE, link="../../outside"), None),
            (tinfo("dev/sda", type_=tarfile.BLKTYPE), None),
            (tinfo("bin/su", 1, mode=0o4755), b"x"),
            (tinfo("docs/a.txt", 7), b"second\n"),
        ]

    def test_all_tar_compressions(self) -> None:
        for suffix, mode in (("tar", "w"), ("tar.gz", "w:gz"), ("tar.bz2", "w:bz2"), ("tar.xz", "w:xz")):
            with self.subTest(suffix=suffix):
                name = self.put(f"t.{suffix}", tar_bytes(self.members(), mode))
                out = self.listing(name)
                flags = self.flags(out)
                self.assertEqual(out["data"]["entries"], 7)
                self.assertIn("link-escapes", flags["docs/link"])
                self.assertEqual(flags["docs/rel"], [])
                self.assertIn("link-escapes", flags["docs/hard"])
                self.assertIn("device", flags["dev/sda"])
                self.assertIn("setuid", flags["bin/su"])
                self.assertEqual(out["data"]["verdict"], "unsafe")
                check = run(f"archive check {name}", self.ctx)
                self.assertEqual(check["data"]["verified"], 3)
                self.assertEqual(check["data"]["problems"], [])
                read = run(f"archive read {name} docs/a.txt", self.ctx)
                self.assertEqual(read["data"]["text"], "second\n")  # the member extraction keeps
                self.assertEqual(read["data"]["duplicates"], 1)
                self.assertIn("is a symlink", run(f"archive read {name} docs/link", self.ctx)["output"])

    def test_link_chains_are_resolved_through_earlier_links(self) -> None:
        # Review finding: each link looks harmless on its own, but extraction
        # follows a -> . and then a/b -> .. so b/payload lands outside.
        members = [(tinfo("a", type_=tarfile.SYMTYPE, link="."), None),
                   (tinfo("a/b", type_=tarfile.SYMTYPE, link=".."), None),
                   (tinfo("b/payload", 3), b"pwn")]
        self.put("chain.tar", tar_bytes(members))
        out = self.listing("chain.tar")
        flags = self.flags(out)
        self.assertEqual(out["data"]["verdict"], "unsafe")
        self.assertIn("link-escapes", flags["a/b"])
        self.assertIn("link-traversal", flags["b/payload"])
        self.assertEqual(flags["a"], [])
        # A file written through a directory link that points outside.
        self.put("dirlink.tar", tar_bytes([(tinfo("up", type_=tarfile.SYMTYPE, link="x/../.."), None),
                                           (tinfo("up/evil.sh", 1), b"x")]))
        self.assertIn("link-traversal", self.flags(self.listing("dirlink.tar"))["up/evil.sh"])
        # Writing a file onto a final-component link follows the link.
        self.put("final.tar", tar_bytes([(tinfo("cfg", type_=tarfile.SYMTYPE, link="../../.bashrc"), None),
                                         (tinfo("cfg", 1), b"x")]))
        self.assertIn("link-traversal", self.flags(self.listing("final.tar"))["cfg"])
        # Hardlink targets are resolved through earlier symlinks too.
        self.put("hard.tar", tar_bytes([(tinfo("d", type_=tarfile.SYMTYPE, link=".."), None),
                                        (tinfo("h", type_=tarfile.LNKTYPE, link="d/secret"), None)]))
        self.assertIn("link-escapes", self.flags(self.listing("hard.tar"))["h"])

    def test_hardlink_aliases_of_symlinks_are_followed(self) -> None:
        # Review finding: hardlink b -> a (a symlink to ".") is a second name
        # for that symlink, so b/c -> .. escapes and c/payload lands outside.
        self.put("alias.tar", tar_bytes([(tinfo("a", type_=tarfile.SYMTYPE, link="."), None),
                                         (tinfo("b", type_=tarfile.LNKTYPE, link="a"), None),
                                         (tinfo("b/c", type_=tarfile.SYMTYPE, link=".."), None),
                                         (tinfo("c/payload", 3), b"pwn")]))
        out = self.listing("alias.tar")
        flags = self.flags(out)
        self.assertEqual(out["data"]["verdict"], "unsafe")
        self.assertIn("link-escapes", flags["b/c"])
        self.assertIn("link-traversal", flags["c/payload"])
        # Through a chain of hardlink aliases as well.
        self.put("alias2.tar", tar_bytes([(tinfo("a", type_=tarfile.SYMTYPE, link="."), None),
                                          (tinfo("b", type_=tarfile.LNKTYPE, link="a"), None),
                                          (tinfo("d", type_=tarfile.LNKTYPE, link="b"), None),
                                          (tinfo("d/e", type_=tarfile.SYMTYPE, link=".."), None),
                                          (tinfo("e/x", 1), b"x")]))
        self.assertIn("link-traversal", self.flags(self.listing("alias2.tar"))["e/x"])

    def test_empty_tar_archives_are_recognised(self) -> None:
        # Review finding: an empty tar is only zero blocks and was "unknown".
        for suffix, mode in (("tar", "w"), ("tar.gz", "w:gz"), ("tar.bz2", "w:bz2"), ("tar.xz", "w:xz")):
            with self.subTest(suffix=suffix):
                name = self.put(f"empty.{suffix}", tar_bytes([], mode))
                out = self.listing(name)
                self.assertEqual(out["data"]["entries"], 0)
                self.assertEqual(out["data"]["verdict"], "ok")
                self.assertTrue(out["data"]["format"].startswith("tar"), out["data"]["format"])
                check = run(f"archive check {name}", self.ctx)
                self.assertTrue(check["success"], check["output"])
                self.assertEqual(check["data"]["verified"], 0)
                self.assertIn("no member named", run(f"archive read {name} x", self.ctx)["output"])
        self.put("short-zeros.bin", bytes(700))
        self.assertFalse(run("archive list short-zeros.bin", self.ctx)["success"])

    def test_benign_link_chains_stay_ok(self) -> None:
        self.put("lib.tar", tar_bytes([(tinfo("usr/lib64", type_=tarfile.DIRTYPE), None),
                                       (tinfo("usr/lib", type_=tarfile.SYMTYPE, link="lib64"), None),
                                       (tinfo("usr/lib/libc.so", 3), b"elf"),
                                       (tinfo("usr/bin/tool", type_=tarfile.SYMTYPE, link="../lib/libc.so"), None)]))
        out = self.listing("lib.tar")
        self.assertEqual(out["data"]["verdict"], "ok", out["output"])

    def test_link_loops_are_reported_not_followed_forever(self) -> None:
        self.put("loop.tar", tar_bytes([(tinfo("x", type_=tarfile.SYMTYPE, link="y"), None),
                                        (tinfo("y", type_=tarfile.SYMTYPE, link="x"), None),
                                        (tinfo("x/f", 1), b"x")]))
        out = self.listing("loop.tar")
        self.assertEqual(out["data"]["verdict"], "unsafe")
        self.assertIn("link-unresolved", self.flags(out)["x/f"])
        self.assertIn("could not be resolved", out["output"])

    def test_damaged_compression_footer_is_not_verified(self) -> None:
        # Review finding: tarfile stops at the end-of-archive blocks, so a
        # truncated or zeroed gzip footer used to verify as ok.
        for mode, cut in (("w:gz", 8), ("w:bz2", 4), ("w:xz", 12)):
            data = tar_bytes([(tinfo("x.txt", 5), b"hello")], mode)
            for label, damaged in (("truncated", data[:-cut]), ("zeroed", data[:-cut] + bytes(cut))):
                with self.subTest(mode=mode, damage=label):
                    name = self.put(f"d-{label}.{mode[2:]}", damaged)
                    check = run(f"archive check {name}", self.ctx)
                    self.assertTrue(check["success"], check["output"])
                    self.assertEqual(check["data"]["verdict"], "unsafe", check["output"])
                    self.assertEqual(check["data"]["verified"], 0)
                    self.assertIn("damaged", check["output"])
                    read = run(f"archive read {name} x.txt", self.ctx)
                    if read["success"]:
                        self.assertIn("WARNING", read["output"])
            intact = self.put(f"intact.{mode[2:]}", data)
            self.assertEqual(run(f"archive check {intact}", self.ctx)["data"]["verified"], 1)

    def test_gzip_bomb_is_stopped_by_the_budget(self) -> None:
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9) as gz:
            gz.write(tinfo("zeros", 256 * 1024 * 1024).tobuf())
            block = b"\0" * (1024 * 1024)
            for _ in range(256):
                gz.write(block)
        self.put("bomb.tar.gz", buffer.getvalue())
        with mock.patch.object(archive, "STREAM_BUDGET_BYTES", 16 * 1024 * 1024):
            started = time.perf_counter()
            out = run("archive list bomb.tar.gz", self.ctx)
            read = run("archive read bomb.tar.gz zeros", self.ctx)
        self.assertTrue(out["success"], out["output"])
        self.assertTrue(out["data"]["incomplete"])
        self.assertEqual(out["data"]["verdict"], "unsafe")
        self.assertIn("budget", out["output"])
        self.assertLess(time.perf_counter() - started, 10)
        self.assertFalse(read["success"])

    def test_time_budget(self) -> None:
        self.put("big.tar.gz", tar_bytes([(tinfo(f"f{i}", 1), b"x") for i in range(3000)], "w:gz"))
        with mock.patch.object(archive, "DEADLINE_S", 0.0):
            out = run("archive list big.tar.gz", self.ctx)
        self.assertIn("time budget", out["output"])

    def test_oversized_headers_are_refused(self) -> None:
        long_name = "d/" + "n" * (400 * 1024)
        self.put("long.tar", tar_bytes([(tinfo(long_name, 1), b"x")], fmt=tarfile.GNU_FORMAT))
        out = run("archive list long.tar", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("exceeds", out["output"])
        info = tinfo("p", 1)
        info.pax_headers = {"comment": "c" * (400 * 1024)}
        self.put("pax.tar.gz", tar_bytes([(info, b"x")], "w:gz"))
        self.assertIn("exceeds", run("archive list pax.tar.gz", self.ctx)["output"])

    def test_sparse_map_chains_are_bounded(self) -> None:
        # Old GNU sparse member followed by thousands of extension blocks: each
        # read is small, but tarfile would build one huge sparse map.
        header = bytearray(tinfo("sparse", 0).tobuf(tarfile.GNU_FORMAT))
        header[156:157] = tarfile.GNUTYPE_SPARSE
        header[482] = 1  # isextended
        extension = bytearray(512)
        for index in range(21):
            struct_offset = index * 24
            extension[struct_offset:struct_offset + 12] = b"%011o\0" % (index * 1024)
            extension[struct_offset + 12:struct_offset + 24] = b"%011o\0" % 512
        extension[504] = 1
        def with_checksum(block):
            block[148:156] = b" " * 8
            block[148:155] = b"%06o\0" % sum(block)
            return bytes(block)
        body = with_checksum(header) + bytes(extension) * 4000 + bytes(1024)
        self.put("sparse.tar.gz", gzip.compress(body))
        out = run("archive list sparse.tar.gz", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("headers of one member exceed", out["output"])

    def test_invalid_header_after_the_first_member_is_not_end_of_archive(self) -> None:
        # Review finding: tarfile.next() returns None on a bad checksum after
        # member 1, which certified the intact prefix as a complete archive.
        data = bytearray(tar_bytes([(tinfo("a.txt", 3), b"aaa"), (tinfo("b.txt", 3), b"bbb")]))
        data[1024] ^= 0x01  # first byte of the second header's name; checksum now wrong
        for name, blob in (("bad.tar", bytes(data)), ("bad.tar.gz", gzip.compress(bytes(data))),
                           ("cut.tar", bytes(data[:1024 + 300]))):
            with self.subTest(name=name):
                self.put(name, blob)
                check = run(f"archive check {name}", self.ctx)
                self.assertTrue(check["success"], check["output"])
                self.assertEqual(check["data"]["verdict"], "unsafe")
                self.assertTrue(check["data"]["incomplete"])
                self.assertEqual(check["data"]["verified"], 0)
        self.assertIn("invalid tar header at offset 1024", run("archive list bad.tar", self.ctx)["output"])
        self.assertIn("truncated inside a header", run("archive list cut.tar", self.ctx)["output"])
        # A missing end marker (input ends at a member boundary) is a normal end.
        intact = tar_bytes([(tinfo("a.txt", 3), b"aaa")])
        self.put("noend.tar", intact[:1024])
        self.assertEqual(self.listing("noend.tar")["data"]["verdict"], "ok")

    def test_chained_long_name_headers_fail_cleanly(self) -> None:
        # Review finding: 500 chained GNU long-name headers raised an uncaught
        # RecursionError.
        chain = b"".join(
            tarfile.TarInfo._create_gnu_long_header("x" * 600, tarfile.GNUTYPE_LONGNAME, "utf-8", "surrogateescape")
            for _ in range(500))
        self.put("chain.tar", chain + tinfo("final").tobuf(tarfile.GNU_FORMAT) + bytes(1024))
        self.put("chain.tar.gz", gzip.compress(chain + tinfo("final").tobuf(tarfile.GNU_FORMAT) + bytes(1024)))
        for name in ("chain.tar", "chain.tar.gz"):
            for action in ("list", "check", "read"):
                with self.subTest(name=name, action=action):
                    out = run(f"archive {action} {name}" + (" final" if action == "read" else ""), self.ctx)
                    self.assertFalse(out["success"])
                    self.assertTrue(out["output"].startswith("archive:"), out["output"])

    def test_deep_names_resolve_in_linear_time_within_the_budget(self) -> None:
        # Review finding: 'a/' * 64000 + 'f' took 21 s (prefix rebuilt per
        # component) and ignored the 15 s deadline.
        deep = "a/" * 64000 + "f"
        self.put("deep.tar", tar_bytes([(tinfo(deep, 0), b""), (tinfo("a", type_=tarfile.SYMTYPE, link="."), None),
                                        (tinfo(deep + "2", 0), b"")]))
        started = time.perf_counter()
        out = self.listing("deep.tar")
        self.assertLess(time.perf_counter() - started, 5)
        self.assertEqual(out["data"]["entries"], 3)
        resolver = archive.LinkResolver(archive.Budget(deadline_s=0.0))
        with self.assertRaises(archive.BudgetExceeded):
            resolver.resolve(["a"] * 5000, follow_last=True)
        entries = [archive.Entry(deep, "file", 0)]
        with mock.patch.object(archive, "DEADLINE_S", 0.0):
            reasons = archive.analyse(entries, archive.Budget())
        self.assertTrue(any("could not be resolved" in reason for reason in reasons))
        self.assertIn("link-unresolved", entries[0].flags)

    def test_entry_cap(self) -> None:
        self.put("n.tar", tar_bytes([(tinfo(f"f{i}"), b"") for i in range(50)]))
        with mock.patch.object(archive, "MAX_SCAN_ENTRIES", 10):
            out = run("archive list n.tar", self.ctx)
        self.assertTrue(out["data"]["incomplete"])
        self.assertEqual(out["data"]["verdict"], "unsafe")

    def test_truncated_tar_is_reported(self) -> None:
        data = tar_bytes([(tinfo("a.txt", 5000), b"a" * 5000), (tinfo("b.txt", 5000), b"b" * 5000)])
        self.put("cut.tar", data[:3000])
        out = run("archive check cut.tar", self.ctx)
        self.assertTrue((not out["success"]) or out["data"]["problems"], out["output"])
        gz = gzip.compress(data)
        self.put("cut.tar.gz", gz[: len(gz) // 2])
        self.assertFalse(run("archive list cut.tar.gz", self.ctx)["success"])


class StreamTests(ArchiveTestCase):
    def test_single_compressed_files(self) -> None:
        text = b"line one\nline two\n"
        for name, data in (("app.log.gz", gzip.compress(text) + gzip.compress(b"third\n")),
                           ("app2.log.bz2", bz2.compress(text)), ("app3.log.xz", lzma.compress(text))):
            with self.subTest(name=name):
                self.put(name, data)
                member = name.rsplit(".", 1)[0]
                out = self.listing(name)
                self.assertEqual(out["data"]["listed"][0]["name"], member)
                read = run(f"archive read {name} {member}", self.ctx)
                self.assertTrue(read["success"], read["output"])
                self.assertTrue(read["data"]["text"].startswith("line one"))
                if name.endswith(".gz"):
                    self.assertTrue(read["data"]["text"].endswith("third\n"))  # every gzip member

    def test_decompressing_reader_bounds_each_call(self) -> None:
        payload = bz2.compress(b"\0" * (32 * 1024 * 1024))
        source = io.BytesIO(payload)
        budget = archive.Budget(max_bytes=10 ** 9)
        reader = archive.DecompressingReader(source.read, "bzip2", budget)
        sizes = []
        while len(sizes) < 20:
            chunk = reader.read(10 ** 9)
            if not chunk:
                break
            sizes.append(len(chunk))
        self.assertTrue(sizes and max(sizes) <= archive.CHUNK)

    def test_trailing_garbage_and_zero_padding(self) -> None:
        self.put("pad.gz", gzip.compress(b"padded\n") + b"\0" * 1024)
        self.assertEqual(run("archive read pad.gz pad", self.ctx)["data"]["text"], "padded\n")
        self.put("junk.gz", gzip.compress(b"data\n") + b"JUNK")
        self.assertTrue(run("archive read junk.gz junk", self.ctx)["success"])

    def test_xz_memory_limit(self) -> None:
        # Claim a 4 GiB LZMA2 dictionary in the block header (and fix its CRC):
        # the decoder must refuse it under the memory limit, not allocate it.
        data = bytearray(lzma.compress(b"x" * 1000, format=lzma.FORMAT_XZ))
        header_size = (data[12] + 1) * 4
        self.assertEqual(bytes(data[14:16]), b"\x21\x01")  # one LZMA2 filter with 1 property byte
        data[16] = 40
        struct.pack_into("<I", data, 12 + header_size - 4, zlib.crc32(bytes(data[12:12 + header_size - 4])))
        self.put("huge-dict.xz", bytes(data))
        out = run("archive read huge-dict.xz huge-dict", self.ctx)
        self.assertFalse(out["success"])
        self.assertIn("corrupt xz", out["output"])


class ConfinementTests(ArchiveTestCase):
    def test_nothing_is_written(self) -> None:
        self.put("evil.zip", zip_bytes([("../../escaped.txt", b"x"), ("/abs.txt", b"y")]))
        before = sorted(os.listdir(self.ws))
        for command in ("archive list evil.zip", "archive check evil.zip", "archive read evil.zip ../../escaped.txt"):
            run(command, self.ctx)
        self.assertEqual(sorted(os.listdir(self.ws)), before)
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_outside_hidden_and_linked_archives_are_refused(self) -> None:
        (self.root / "outside.zip").write_bytes(zip_bytes([("secret.txt", b"secret")]))
        for target in ("../outside.zip", str(self.root / "outside.zip"), ".hidden.zip"):
            with self.subTest(target=target):
                out = run(f"archive list '{target}'", self.ctx)
                self.assertFalse(out["success"])
                self.assertNotIn("secret.txt", out["output"])
        if sys.platform != "win32":
            os.symlink(self.root / "outside.zip", self.ws / "link.zip")
            self.assertIn("Symbolic links", run("archive list link.zip", self.ctx)["output"])

    def test_unknown_format_and_usage(self) -> None:
        self.put("notes.txt", b"just text\n" * 10)
        self.assertIn("unsupported archive format", run("archive list notes.txt", self.ctx)["output"])
        self.assertIn("Usage", run("archive", self.ctx)["output"])
        self.assertFalse(run("archive explode x.zip", self.ctx)["success"])
        self.assertFalse(run("archive read x.zip", self.ctx)["success"])


class AnalysisUnitTests(unittest.TestCase):
    def test_link_escape_rules(self) -> None:
        cases = [("a/l", "../b", False, False), ("a/l", "../../b", False, True), ("l", "/etc", False, True),
                 ("a/b/l", "../../c", False, False), ("h", "../x", True, True), ("h", "a/b", True, False),
                 ("l", "C:\\x", False, True), ("a/l", "..\\..\\x", False, True)]
        for name, target, hard, escapes in cases:
            with self.subTest(name=name, target=target):
                self.assertEqual(archive._link_escapes(name, target, hard), escapes)

    def test_safe_name_escapes_every_dangerous_category(self) -> None:
        self.assertEqual(archive.safe_name("a\nb\u202ec\udcffd\u2028"), "a\\x0ab\\u202ec\\udcffd\\u2028")

    def test_detect(self) -> None:
        self.assertEqual(archive.detect(b"\x1f\x8b\x08"), "gzip")
        self.assertEqual(archive.detect(b"BZh91AY"), "bzip2")
        self.assertEqual(archive.detect(b"\xfd7zXZ\x00\x00"), "xz")
        self.assertEqual(archive.detect(b"PK\x03\x04"), "zip")
        self.assertEqual(archive.detect(tinfo("x").tobuf(tarfile.USTAR_FORMAT)), "tar")
        self.assertEqual(archive.detect(tinfo("x").tobuf(tarfile.GNU_FORMAT)), "tar")
        self.assertEqual(archive.detect(b"hello"), "unknown")


if __name__ == "__main__":
    unittest.main()
