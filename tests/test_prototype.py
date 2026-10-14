"""Offline tests for the Task 1 prototype's pure functions.

Nothing here makes an HTTP request; conftest.py fails any test that tries to
open a socket. HTTP-level test doubles are Task 3's deliverable.
"""

import dataclasses
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from policy_scraper import prototype as p

FIXTURE = Path(__file__).parent / "fixtures" / "policy_index_sample.html"
SITE_INDEX = "https://uwa-policy.cits5501.arranstewart.io/policy/"
PAGE = "https://h.test/dir/index.html"

PDF = b"%PDF-1.6\r%\xe2\xe3\xcf\xd3\r\n"
ZIP = b"PK\x03\x04\x14\x00\x06\x00\x08\x00\x00\x00!\x00"
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00\x00\x00\x00\x00"
RTF = b"{\\rtf1\\adeflang1"
HTML = b"<!DOCTYPE html>\n"


def page(*hrefs: str, head: str = "") -> str:
    """Build a minimal HTML page with one anchor per href."""
    anchors = "".join(f'<a href="{href}">x</a>' for href in hrefs)
    return f"<html><head>{head}</head><body>{anchors}</body></html>"


def links_on(*hrefs: str, head: str = "") -> list[str]:
    """Document links extracted from a minimal page served at PAGE."""
    return p.extract_doc_links(page(*hrefs, head=head), PAGE).links


def names(urls: list[str]) -> list[str]:
    """Last path segment of each URL."""
    return [url.rsplit("/", 1)[-1] for url in urls]


@pytest.fixture(scope="module")
def site() -> p.Discovery:
    """Discovery result for the trimmed copy of the real index page."""
    return p.extract_doc_links(FIXTURE.read_bytes(), SITE_INDEX)


# Extraction from the trimmed real page


def test_real_page_documents_found_in_page_order(site: p.Discovery) -> None:
    assert names(site.links) == [
        "delegations-framework-policy.doc",
        "delegations-framework-diagram.pdf",
        "risk-policy.docx",
        "risk-management-guideline.docx",
        "compliance-management-guideline.doc",
        "fraud-and-corruption-policy.doc",
        "fraud-and-corruption-investigation-guideline.doc",
        "fraud-and-corruption-control-plan.doc",
        "fraud-and-corruption-training.pptx",
        "fees-payment-and-refund-of-tuition-fees-for-domestic-postgraduate-students-policy.rtf",
        "student-enrolment-policy-schedules-and-procedure.pdf",
    ]


def test_real_page_root_relative_hrefs_resolve_to_site(site: p.Discovery) -> None:
    assert all(url.startswith(SITE_INDEX + "-/media/") for url in site.links)


def test_real_page_extension_counts(site: p.Discovery) -> None:
    by_ext = Counter(p.doc_extension(url) for url in site.links)
    assert by_ext == {"doc": 5, "docx": 2, "pdf": 2, "pptx": 1, "rtf": 1}


def test_real_page_other_host_document_is_skipped_by_hostname(
    site: p.Discovery,
) -> None:
    assert site.off_host == {"www.uwa.edu.au": 1}


def test_real_page_counts_every_anchor_with_href(site: p.Discovery) -> None:
    assert site.anchors == 21


def test_real_page_assets_and_external_pages_are_not_documents(
    site: p.Discovery,
) -> None:
    unwanted = (".css", ".js", ".svg", "sharepoint.com", "facebook.com", "/policy/#")
    assert not [url for url in site.links if any(u in url for u in unwanted)]


# Extraction: how hrefs resolve


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        pytest.param("https://h.test/a/x.pdf", "https://h.test/a/x.pdf", id="absolute"),
        pytest.param("/a/x.pdf", "https://h.test/a/x.pdf", id="root-relative"),
        pytest.param("x.pdf", "https://h.test/dir/x.pdf", id="relative"),
        pytest.param("../x.pdf", "https://h.test/x.pdf", id="parent-relative"),
        pytest.param("//h.test/x.pdf", "https://h.test/x.pdf", id="protocol-relative"),
        pytest.param("  /a/x.pdf\n", "https://h.test/a/x.pdf", id="surrounding-space"),
    ],
)
def test_href_resolves_against_page_url(href: str, expected: str) -> None:
    assert links_on(href) == [expected]


def test_base_href_is_used_to_resolve_relative_links() -> None:
    assert links_on("x.pdf", head='<base href="/files/">') == [
        "https://h.test/files/x.pdf"
    ]


def test_root_relative_href_ignores_base_path() -> None:
    assert links_on("/x.pdf", head='<base href="/files/">') == ["https://h.test/x.pdf"]


def test_only_first_base_href_counts() -> None:
    head = '<base href="/one/"><base href="/two/">'
    assert links_on("x.pdf", head=head) == ["https://h.test/one/x.pdf"]


def test_base_on_other_host_makes_relative_links_off_host() -> None:
    found = p.extract_doc_links(
        page("x.pdf", head='<base href="https://cdn.test/"/>'), PAGE
    )
    assert found.links == []
    assert found.off_host == {"cdn.test": 1}


# Extraction: which paths count as documents


@pytest.mark.parametrize(
    "ext", ["pdf", "doc", "docx", "rtf", "odt", "ppt", "pptx", "xls", "xlsx"]
)
def test_each_document_extension_is_recognised(ext: str) -> None:
    assert links_on(f"/a/file.{ext}") == [f"https://h.test/a/file.{ext}"]


@pytest.mark.parametrize("name", ["FILE.PDF", "File.Docx", "report.pPtX"])
def test_extension_match_ignores_case(name: str) -> None:
    assert links_on(f"/a/{name}") == [f"https://h.test/a/{name}"]


def test_hostname_match_ignores_case() -> None:
    assert links_on("HTTPS://H.TEST/a/x.pdf") == ["https://H.TEST/a/x.pdf"]


def test_query_string_is_ignored_for_matching_but_kept_in_url() -> None:
    assert links_on("/a/x.pdf?download=1") == ["https://h.test/a/x.pdf?download=1"]


def test_extension_in_query_string_does_not_count() -> None:
    assert links_on("/a/view?file=x.pdf") == []


def test_fragment_is_dropped_from_url() -> None:
    assert links_on("/a/x.pdf#page=2") == ["https://h.test/a/x.pdf"]


def test_extension_in_fragment_does_not_count() -> None:
    assert links_on("/a/page#x.pdf") == []


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("two%20words.doc", id="encoded-space"),
        pytest.param("dotted%2Epdf", id="encoded-dot-before-extension"),
        pytest.param("x.%50%44%46", id="encoded-extension-letters"),
        pytest.param("%E2%80%93dash.docx", id="encoded-non-ascii"),
    ],
)
def test_percent_encoded_name_is_matched_after_decoding(name: str) -> None:
    assert links_on(f"/a/{name}") == [f"https://h.test/a/{name}"]


@pytest.mark.parametrize(
    "name", ["policy-(16).pdf", "policy-%2816%29.docx", "(draft).rtf"]
)
def test_parentheses_in_file_name_are_kept(name: str) -> None:
    assert links_on(f"/a/{name}") == [f"https://h.test/a/{name}"]


def test_same_document_in_different_forms_collapses_to_first() -> None:
    hrefs = (
        "/b.doc",
        "/dir/../a.pdf",
        "../b.doc",
        "/b.doc",
        "https://h.test/b.doc#top",
    )
    assert links_on(*hrefs) == ["https://h.test/b.doc", "https://h.test/a.pdf"]


def test_duplicate_keeps_position_of_first_occurrence() -> None:
    assert names(links_on("/a.pdf", "/b.pdf", "/a.pdf", "/c.pdf")) == [
        "a.pdf",
        "b.pdf",
        "c.pdf",
    ]


def test_links_differing_only_in_query_are_distinct() -> None:
    assert len(links_on("/a.pdf?v=1", "/a.pdf?v=2")) == 2


def test_other_host_documents_are_excluded() -> None:
    assert links_on("https://other.test/a.pdf", "http://third.test/b.doc") == []


def test_other_host_documents_are_counted_by_hostname() -> None:
    html = page(
        "https://other.test/a.pdf",
        "https://other.test/b.doc",
        "http://third.test/c.rtf",
    )
    assert p.extract_doc_links(html, PAGE).off_host == {
        "other.test": 2,
        "third.test": 1,
    }


def test_other_host_duplicates_are_counted_once() -> None:
    html = page("https://other.test/a.pdf", "https://other.test/a.pdf#again")
    assert p.extract_doc_links(html, PAGE).off_host == {"other.test": 1}


def test_other_host_non_documents_are_not_counted() -> None:
    html = page("https://other.test/page.html", "https://other.test/")
    assert p.extract_doc_links(html, PAGE).off_host == {}


def test_same_hostname_on_other_port_counts_as_same_host() -> None:
    assert links_on("http://h.test:8080/x.pdf") == ["http://h.test:8080/x.pdf"]


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("/img/logo.svg", id="svg-image"),
        pytest.param("/img/photo-(16).jpg?w=1440", id="jpg-image"),
        pytest.param("/js/main.js", id="script"),
        pytest.param("/css/main.css?v=1", id="stylesheet"),
        pytest.param("/a/page.html", id="html-page"),
        pytest.param("/a/archive.zip", id="other-extension"),
        pytest.param("/a/report.pdf.html", id="pdf-not-last"),
        pytest.param("mailto:policy@uwa.edu.au", id="mailto"),
        pytest.param("mailto:policy.pdf", id="mailto-ending-in-pdf"),
        pytest.param("javascript:void(0)", id="javascript"),
        pytest.param("javascript:open('x.pdf')", id="javascript-ending-in-pdf"),
        pytest.param("tel:+61864886000", id="tel"),
        pytest.param("ftp://h.test/x.pdf", id="ftp"),
        pytest.param("#", id="hash"),
        pytest.param("", id="empty"),
    ],
)
def test_non_document_link_is_excluded(href: str) -> None:
    assert links_on(href) == []


@pytest.mark.parametrize(
    "href",
    [
        pytest.param("/a/policy", id="no-dot"),
        pytest.param("/a/policy.pdf/", id="trailing-slash"),
        pytest.param("/a/policy/", id="directory"),
        pytest.param("https://h.test", id="bare-host"),
        pytest.param("/a/pdf", id="extension-as-name"),
    ],
)
def test_extensionless_url_is_excluded(href: str) -> None:
    assert links_on(href) == []


def test_only_anchor_elements_are_scanned() -> None:
    html = (
        '<link rel="stylesheet" href="/a.pdf"><script src="/b.pdf"></script>'
        '<img src="/c.pdf"><iframe src="/d.pdf"></iframe><a href="/e.pdf">e</a>'
    )
    assert names(p.extract_doc_links(html, PAGE).links) == ["e.pdf"]


def test_anchor_count_includes_only_anchors_with_href() -> None:
    html = '<a name="top"></a><a href="/a.pdf">a</a><a href="#">b</a><a>c</a>'
    assert p.extract_doc_links(html, PAGE).anchors == 2


def test_malformed_href_is_skipped_without_losing_others() -> None:
    assert links_on("http://[::1/x.pdf", "/ok.pdf") == ["https://h.test/ok.pdf"]


def test_page_with_no_anchors_finds_nothing() -> None:
    found = p.extract_doc_links("<html><body><p>No links</p></body></html>", PAGE)
    assert (found.anchors, found.links, found.off_host) == (0, [], {})


# Signatures


@pytest.mark.parametrize(
    ("head", "name"),
    [
        pytest.param(PDF, "PDF", id="pdf"),
        pytest.param(ZIP, "ZIP", id="zip"),
        pytest.param(OLE2, "OLE2", id="ole2"),
        pytest.param(RTF, "RTF", id="rtf"),
    ],
)
def test_magic_number_is_recognised(head: bytes, name: str) -> None:
    assert p.classify_signature(head) == name


@pytest.mark.parametrize(
    "head",
    [
        pytest.param(b"<!DOCTYPE html>", id="doctype-upper"),
        pytest.param(b"<!doctype html>", id="doctype-lower"),
        pytest.param(b"<html lang='en'>", id="html-tag"),
        pytest.param(b"<HTML>", id="html-tag-upper"),
        pytest.param(b"\xef\xbb\xbf<!DOCTYPE html>", id="bom"),
        pytest.param(b" \r\n\t<html>", id="whitespace"),
        pytest.param(b"\xef\xbb\xbf\n  <html>", id="bom-and-whitespace"),
    ],
)
def test_html_is_recognised(head: bytes) -> None:
    assert p.classify_signature(head) == "HTML"


def test_empty_bytes_are_unknown() -> None:
    assert p.classify_signature(b"") == "unknown"


@pytest.mark.parametrize(
    "head",
    [
        pytest.param(b"plain text file", id="text"),
        pytest.param(b"<?xml version=", id="xml"),
        pytest.param(b"GIF89a", id="gif"),
        pytest.param(b"%PD", id="truncated-magic"),
        pytest.param(b"  %PDF-1.7", id="magic-after-whitespace"),
        pytest.param(b"<head><title>", id="html-without-html-tag"),
    ],
)
def test_unrecognised_bytes_are_unknown(head: bytes) -> None:
    assert p.classify_signature(head) == "unknown"


# Verdicts

GOOD = p.DownloadResult(
    url="https://h.test/a.pdf",
    final_url="https://h.test/a.pdf",
    status=200,
    content_type="application/pdf",
    content_length="100",
    bytes_read=100,
    head=PDF,
)


def result(**changes: Any) -> p.DownloadResult:
    """A copy of a good PDF download with some fields changed."""
    return dataclasses.replace(GOOD, **changes)


def test_verdict_ok_when_everything_matches() -> None:
    assert p.verdict(GOOD) == "OK"


@pytest.mark.parametrize(
    ("ext", "head"),
    [
        ("pdf", PDF),
        ("doc", OLE2),
        ("ppt", OLE2),
        ("xls", OLE2),
        ("docx", ZIP),
        ("pptx", ZIP),
        ("xlsx", ZIP),
        ("odt", ZIP),
        ("rtf", RTF),
        ("DOCX", ZIP),
    ],
)
def test_verdict_ok_for_each_extension_with_its_signature(
    ext: str, head: bytes
) -> None:
    assert p.verdict(result(url=f"https://h.test/a.{ext}", head=head)) == "OK"


def test_verdict_failed_names_the_exception() -> None:
    failed = result(status=None, bytes_read=0, head=b"", error="ReadTimeout: too slow")
    assert p.verdict(failed) == "FAILED (ReadTimeout: too slow)"


@pytest.mark.parametrize("status", [304, 403, 404, 500])
def test_verdict_reports_non_2xx_status(status: int) -> None:
    assert p.verdict(result(status=status)) == f"HTTP {status}"


def test_verdict_accepts_any_2xx_status() -> None:
    assert p.verdict(result(status=206)) == "OK"


def test_verdict_empty_when_no_bytes_arrived() -> None:
    assert p.verdict(result(bytes_read=0, head=b"", content_length="0")) == "EMPTY"


def test_verdict_size_cap_when_streaming_was_stopped() -> None:
    assert p.verdict(result(capped=True)) == "SIZE CAP"


def test_verdict_incomplete_when_fewer_bytes_than_declared() -> None:
    assert p.verdict(result(bytes_read=60)) == "INCOMPLETE"


def test_verdict_incomplete_check_applies_to_identity_encoding() -> None:
    assert p.verdict(result(bytes_read=60, content_encoding="identity")) == "INCOMPLETE"


def test_verdict_skips_length_check_when_body_was_encoded() -> None:
    assert p.verdict(result(bytes_read=60, content_encoding="gzip")) == "OK"


@pytest.mark.parametrize("declared", [None, "", "lots", "-5"])
def test_verdict_skips_length_check_without_usable_content_length(
    declared: str | None,
) -> None:
    assert p.verdict(result(bytes_read=60, content_length=declared)) == "OK"


def test_verdict_looks_like_html_for_html_body() -> None:
    assert p.verdict(result(head=HTML)) == "LOOKS LIKE HTML"


@pytest.mark.parametrize(
    ("url", "head"),
    [
        pytest.param("https://h.test/a.pdf", OLE2, id="pdf-link-ole2-body"),
        pytest.param("https://h.test/a.doc", ZIP, id="doc-link-zip-body"),
        pytest.param("https://h.test/a.docx", OLE2, id="docx-link-ole2-body"),
        pytest.param("https://h.test/a.pdf", b"plain text here", id="unknown-body"),
    ],
)
def test_verdict_signature_mismatch(url: str, head: bytes) -> None:
    assert p.verdict(result(url=url, head=head)) == "SIGNATURE MISMATCH"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        pytest.param(
            {"error": "ConnectionError: reset", "status": 404, "bytes_read": 0},
            "FAILED (ConnectionError: reset)",
            id="failed-beats-http-and-empty",
        ),
        pytest.param(
            {"status": 404, "bytes_read": 0, "head": b""},
            "HTTP 404",
            id="http-beats-empty",
        ),
        pytest.param({"status": 404, "head": HTML}, "HTTP 404", id="http-beats-html"),
        pytest.param(
            {"bytes_read": 0, "head": b"", "content_length": "100"},
            "EMPTY",
            id="empty-beats-incomplete",
        ),
        pytest.param(
            {"capped": True, "content_length": "999999999", "head": HTML},
            "SIZE CAP",
            id="size-cap-beats-incomplete-and-html",
        ),
        pytest.param(
            {"bytes_read": 60, "head": HTML},
            "INCOMPLETE",
            id="incomplete-beats-html",
        ),
        pytest.param(
            {"bytes_read": 60, "head": OLE2},
            "INCOMPLETE",
            id="incomplete-beats-mismatch",
        ),
        pytest.param(
            {"url": "https://h.test/a.doc", "head": HTML},
            "LOOKS LIKE HTML",
            id="html-beats-mismatch",
        ),
    ],
)
def test_verdict_precedence(changes: dict[str, Any], expected: str) -> None:
    assert p.verdict(result(**changes)) == expected


# Output helpers


def test_first_bytes_are_shown_as_hex() -> None:
    assert p.format_head(b"%PDF").startswith("25 50 44 46  |")


def test_non_printable_bytes_show_as_dots() -> None:
    assert p.format_head(b"PK\x03\x04\x7f ~").endswith("|PK... ~|")


def test_output_escapes_non_ascii(capsys: pytest.CaptureFixture[str]) -> None:
    p.say("café – ok")
    assert capsys.readouterr().out == "caf\\xe9 \\u2013 ok\n"


def test_diagnostics_go_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    p.say("warning", err=True)
    assert capsys.readouterr() == ("", "warning\n")


# Selection

LINKS = [
    f"https://h.test/{name}"
    for name in ("p1.pdf", "p2.pdf", "p3.pdf", "d1.doc", "x1.docx", "d2.doc", "p4.pdf")
]


def test_selection_takes_one_of_each_extension_before_repeats() -> None:
    assert names(p.select_documents(LINKS, 3)) == ["p1.pdf", "d1.doc", "x1.docx"]


def test_selection_orders_extensions_by_first_appearance() -> None:
    assert names(p.select_documents(list(reversed(LINKS)), 3)) == [
        "p4.pdf",
        "d2.doc",
        "x1.docx",
    ]


def test_selection_round_robins_after_first_pass() -> None:
    assert names(p.select_documents(LINKS, 7)) == [
        "p1.pdf",
        "d1.doc",
        "x1.docx",
        "p2.pdf",
        "d2.doc",
        "p3.pdf",
        "p4.pdf",
    ]


@pytest.mark.parametrize("count", [1, 3, 5])
def test_selection_honours_count(count: int) -> None:
    assert len(p.select_documents(LINKS, count)) == count


def test_selection_is_deterministic() -> None:
    assert p.select_documents(LINKS, 5) == p.select_documents(list(LINKS), 5)


def test_selection_never_repeats_a_link() -> None:
    chosen = p.select_documents(LINKS, 7)
    assert len(set(chosen)) == len(chosen)


def test_selection_returns_all_when_fewer_candidates_than_requested() -> None:
    assert p.select_documents(LINKS[:2], 5) == LINKS[:2]


def test_selection_from_no_candidates_is_empty() -> None:
    assert p.select_documents([], 5) == []


def test_selection_on_real_page_picks_one_per_extension(site: p.Discovery) -> None:
    chosen = p.select_documents(site.links, 5)
    assert [p.doc_extension(url) for url in chosen] == [
        "doc",
        "pdf",
        "docx",
        "pptx",
        "rtf",
    ]


# Request budget (no HTTP: the budget only counts and paces)


def test_budget_starts_unused() -> None:
    assert p.RequestBudget(limit=3, delay=0).used == 0


def test_each_charge_counts_one_request() -> None:
    budget = p.RequestBudget(limit=3, delay=0)
    budget.charge()
    budget.charge()
    assert budget.used == 2


def test_budget_allows_exactly_the_limit() -> None:
    budget = p.RequestBudget(limit=3, delay=0)
    for _ in range(3):
        budget.charge()
    assert budget.used == 3


def test_budget_refuses_request_beyond_limit() -> None:
    budget = p.RequestBudget(limit=2, delay=0)
    budget.charge()
    budget.charge()
    with pytest.raises(p.BudgetExhausted):
        budget.charge()


def test_refused_request_is_not_counted() -> None:
    budget = p.RequestBudget(limit=1, delay=0)
    budget.charge()
    with pytest.raises(p.BudgetExhausted):
        budget.charge()
    assert budget.used == 1


def test_default_budget_is_twenty_requests() -> None:
    assert p.RequestBudget().limit == 20


def test_first_request_is_not_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(p.time, "sleep", sleeps.append)
    p.RequestBudget(limit=5, delay=0.5).charge()
    assert sleeps == []


def test_later_requests_are_delayed(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(p.time, "sleep", sleeps.append)
    budget = p.RequestBudget(limit=5, delay=0.5)
    for _ in range(3):
        budget.charge()
    assert sleeps == [0.5, 0.5]


def test_zero_delay_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(p.time, "sleep", sleeps.append)
    budget = p.RequestBudget(limit=5, delay=0)
    for _ in range(3):
        budget.charge()
    assert sleeps == []


# Command line (rejected before any session is opened)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--count", "0"], id="count-zero"),
        pytest.param(["--timeout", "0"], id="timeout-zero"),
        pytest.param(["--delay", "-1"], id="negative-delay"),
        pytest.param(["--count", "three"], id="count-not-integer"),
    ],
)
def test_invalid_option_exits_with_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        p.main(argv)
    assert exited.value.code == 2
