"""
Task 4 adversarial inputs: executable versions of ideas A1, A2, A4 and A5.

Each test states what a *correct* scraper would do. The prototype currently
gets these wrong, so every test is marked xfail(strict=True): the suite stays
green while the weakness is documented, and once someone fixes the scraper the
test XPASSes, which strict mode turns into a failure, prompting removal of the
marker.
"""

import io
import zipfile

import pytest
import requests
import responses

import scraper

BASE = "https://example.test/policy/"


@pytest.fixture(autouse=True)
def fast_and_isolated(monkeypatch):
    """No pauses between requests, and fresh request counters for every test."""
    monkeypatch.setattr(scraper, "_request_count", 0)
    monkeypatch.setattr(scraper, "_seconds_paused", 0.0)
    monkeypatch.setattr(scraper, "DELAY_SECONDS", 0.0)


def zip_bytes(*names: str) -> bytes:
    """Build a small ZIP archive holding empty files with the given names."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "x")
    return buffer.getvalue()


def download(url: str, body: bytes, content_type: str) -> str:
    """Serve `body` at `url` from a mock server; return the scraper's verdict."""
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, url, body=body, status=200, content_type=content_type)
        with requests.Session() as session:
            return scraper.describe_download(session, url)


# --- A1: right magic bytes does not mean a complete file ---------------------

REAL_DOCX = zip_bytes("[Content_Types].xml", "word/document.xml")

A1_CASES = [
    pytest.param(
        b"%PDF-1.7\n1 0 obj<<>>endobj\n" + b"x" * 500,
        "https://example.test/a1/cut-off.pdf",
        "application/pdf",
        id="pdf-without-eof-marker",
    ),
    pytest.param(
        b"%PDF",
        "https://example.test/a1/four-bytes.pdf",
        "application/pdf",
        id="pdf-four-bytes-only",
    ),
    pytest.param(
        REAL_DOCX[:100],
        "https://example.test/a1/cut-off.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        id="docx-first-100-bytes",
    ),
]


@pytest.mark.xfail(strict=True, reason="A1: verdict only checks 16 leading bytes")
@pytest.mark.parametrize(("body", "url", "content_type"), A1_CASES)
def test_a1_truncated_file_with_valid_header_is_not_ok(body, url, content_type):
    """No Content-Length is sent, so the length check cannot catch the cut."""
    assert download(url, body, content_type) != "OK"


# --- A2: extension plus signature does not identify the file type ------------


@pytest.mark.xfail(strict=True, reason="A2: any ZIP passes for any ZIP extension")
def test_a2_plain_zip_at_docx_url_is_not_ok():
    body = zip_bytes("hello.txt")
    verdict = download("https://example.test/a2/plain.docx", body, "application/zip")
    assert verdict != "OK"


@pytest.mark.xfail(strict=True, reason="A2: any ZIP passes for any ZIP extension")
def test_a2_pptx_at_docx_url_is_not_ok():
    body = zip_bytes("[Content_Types].xml", "ppt/presentation.xml")
    verdict = download(
        "https://example.test/a2/slides.docx",
        body,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    assert verdict != "OK"


@pytest.mark.xfail(strict=True, reason="A2: Content-Type is never compared")
def test_a2_declared_excel_type_at_doc_url_is_not_ok():
    ole2_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
    verdict = download(
        "https://example.test/a2/sheet.doc", ole2_header, "application/vnd.ms-excel"
    )
    assert verdict != "OK"


# --- A4: links that differ are not necessarily different documents ------------


@pytest.mark.xfail(strict=True, reason="A4: URLs are compared as plain strings")
def test_a4_percent_encoded_dot_is_the_same_document():
    """/p.pdf and /p%2Epdf are equivalent URLs (RFC 3986), so one document."""
    html = '<a href="/x/p.pdf">a</a><a href="/x/p%2Epdf">b</a>'
    links = scraper.extract_links(html, BASE)
    policy = [link for link in links if scraper.is_policy_link(link)]
    same_host, _ = scraper.split_by_host(policy, BASE)
    assert len(same_host) == 1


# --- A5: not every document link ends in an extension -------------------------


@pytest.mark.xfail(strict=True, reason="A5: extensionless links are dropped silently")
def test_a5_extensionless_download_link_is_listed_or_reported(capsys):
    """Dropping /download?id=7 is acceptable only if the scraper says so."""
    index = (
        '<a href="/policy/real.pdf">real</a>'
        '<a href="/download?id=7">Download the leave policy</a>'
    )
    with responses.RequestsMock() as mock:
        mock.add(responses.GET, BASE, body=index, status=200, content_type="text/html")
        exit_code = scraper.main([BASE, "--list"])
    out = capsys.readouterr()
    assert exit_code == 0
    assert "https://example.test/policy/real.pdf" in out.out
    assert "/download?id=7" in out.out + out.err
