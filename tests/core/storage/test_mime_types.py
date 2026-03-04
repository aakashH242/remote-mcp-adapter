from __future__ import annotations

from remote_mcp_adapter.core.storage import mime_types as mt


def test_looks_like_text_and_magic():
    assert mt._looks_like_text(b"") is True
    assert mt._looks_like_text("hello".encode()) is True
    assert mt._looks_like_text(b"a\x00b") is False
    assert mt._looks_like_text(b"\xff\xfe") is False

    assert mt._mime_from_magic(b"%PDF-1.7 abc") == "application/pdf"
    assert mt._mime_from_magic(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert mt._mime_from_magic(b"\xff\xd8\xff\xdbrest") == "image/jpeg"
    assert mt._mime_from_magic(b"GIF87a") == "image/gif"
    assert mt._mime_from_magic(b"GIF89a") == "image/gif"
    assert mt._mime_from_magic(b"RIFFxxxxWEBPrest") == "image/webp"
    assert mt._mime_from_magic(b"unknown") is None


def test_detect_mime_type_branches(tmp_path):
    pdf = tmp_path / "file.bin"
    pdf.write_bytes(b"%PDF-1.4 data")
    assert mt.detect_mime_type(pdf) == "application/pdf"

    txt = tmp_path / "a.txt"
    txt.write_text("hello", encoding="utf-8")
    assert mt.detect_mime_type(txt) == "text/plain"

    noext = tmp_path / "blob"
    noext.write_text("hello", encoding="utf-8")
    assert mt.detect_mime_type(noext) == "text/plain"

    binary = tmp_path / "blob2"
    binary.write_bytes(b"\x00\x01\x02")
    assert mt.detect_mime_type(binary) == "application/octet-stream"
    assert mt.detect_mime_type(binary, fallback="application/custom") == "application/custom"


def test_detect_mime_type_open_error(monkeypatch, tmp_path):
    path = tmp_path / "x.dat"
    path.write_text("x", encoding="utf-8")

    original_open = path.__class__.open

    def raising_open(self, *args, **kwargs):
        if self == path:
            raise OSError("boom")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(path.__class__, "open", raising_open)
    assert mt.detect_mime_type(path) == "application/octet-stream"
    assert mt.detect_mime_type(path, fallback="application/custom") == "application/custom"
