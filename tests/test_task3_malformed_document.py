from pathlib import Path

import requests
import responses

import scraper

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FAKE_PDF_URL = "https://example.test/policy/fake-policy.pdf"


@responses.activate
def test_pdf_url_returning_html_is_rejected(monkeypatch):
    """
    Demonstrate a malformed-document failure using a fixture and test double.

    The URL appears to reference a PDF document, but the mocked server
    actually returns HTML.
    """

    # Reset global request tracking so this test is independent.
    monkeypatch.setattr(scraper, "_request_count", 0)
    monkeypatch.setattr(scraper, "_seconds_paused", 0.0)

    # Automated tests should not wait between requests.
    monkeypatch.setattr(scraper, "DELAY_SECONDS", 0.0)

    fixture = FIXTURE_DIR / "html_instead_of_pdf.html"
    fake_html = fixture.read_bytes()

    # Test double: intercept the request instead of contacting a real server.
    responses.add(
        responses.GET,
        FAKE_PDF_URL,
        body=fake_html,
        status=200,
        content_type="text/html",
    )

    with requests.Session() as session:
        result = scraper.describe_download(session, FAKE_PDF_URL)

    # The URL claims to be a PDF, but the scraper should detect HTML.
    assert result == "LOOKS LIKE HTML"

    # Confirm that exactly one mocked request occurred.
    assert len(responses.calls) == 1