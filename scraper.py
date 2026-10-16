#!/usr/bin/env python3
"""
CITS5501 Project Phase 2 -- Task 1 minimal prototype (Group 11), built on the
unit's starter code.

What it does:

- reads an optional start URL from the command line (defaults to the
  policy index page),
- fetches that page via `fetch()`, which sets a timeout and a User-Agent,
  waits between requests, and stops the run before it would exceed
  MAX_REQUESTS (every redirect hop counts as a request),
- extracts every <a href="..."> link on the page and makes it absolute,
- keeps the links that look like policy documents (`is_policy_link()`) on
  the same host as the index page, and picks up to 5 distinct ones: the
  first of each file type in page order, then round-robin,
- downloads each chosen document once (`describe_download()`), streaming it
  without saving it, and prints its metadata, its first bytes and a verdict
  on whether the body looks like the file type its extension claims.

Run with:

    uv run --with-requirements requirements.txt scraper.py
    uv run --with-requirements requirements.txt scraper.py http://localhost:3000/

(See README.md for other ways to run it.)
"""

import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

START_URL = "https://uwa-policy.cits5501.arranstewart.io/policy/"

# Politeness and safety limits. Please don't raise these when running
# against the live site.
MAX_REQUESTS = 20  # total requests per run, counting every redirect hop
TIMEOUT_SECONDS = 10  # per request; `requests` has NO timeout by default
DELAY_SECONDS = 1.0  # pause between consecutive requests

USER_AGENT = "CITS5501-student-scraper/0.1 (university course project, Group 11)"

MAX_REDIRECTS = 5  # per URL
DEFAULT_COUNT = 5  # documents to download; the brief asks for 3-5
SIZE_CAP = 50 * 1024 * 1024  # stop reading a document body past this many bytes
HEAD_SIZE = 16  # leading bytes kept for the signature check
CHUNK_SIZE = 64 * 1024

# Document extension -> signature its first bytes should carry.
EXPECTED_SIGNATURE = {"pdf": "PDF", "rtf": "RTF"}
EXPECTED_SIGNATURE.update(dict.fromkeys(("doc", "ppt", "xls"), "OLE2"))
EXPECTED_SIGNATURE.update(dict.fromkeys(("docx", "pptx", "xlsx", "odt"), "ZIP"))
MAGIC_NUMBERS = (
    (b"%PDF", "PDF"),
    (b"PK\x03\x04", "ZIP"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "OLE2"),
    (b"{\\rtf", "RTF"),
)


class RequestLimitExceeded(Exception):
    """Raised when a run tries to make more than MAX_REQUESTS requests."""


# keep track of how many requests we have made, and how long we paused.
_request_count = 0
_seconds_paused = 0.0


def fetch(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    """
    Make a GET request, enforcing the request cap, a timeout and a delay.

    Use this for every request the prototype makes, rather than calling
    `session.get()` directly. Any extra keyword arguments (e.g. `stream=True`)
    are passed through to `session.get()`.

    Returns a `requests.Response`. Note that this does NOT raise an exception
    for 4xx/5xx status codes -- check `response.status_code` yourself.
    """

    global _request_count, _seconds_paused
    if _request_count >= MAX_REQUESTS:
        raise RequestLimitExceeded(
            f"request limit of {MAX_REQUESTS} reached; not fetching {url}"
        )
    if _request_count > 0:
        start = time.perf_counter()
        time.sleep(DELAY_SECONDS)
        _seconds_paused += time.perf_counter() - start
    _request_count += 1

    kwargs.setdefault("timeout", TIMEOUT_SECONDS)
    return session.get(url, **kwargs)


def fetch_with_redirects(
    session: requests.Session, url: str, **kwargs: Any
) -> requests.Response:
    """
    Like `fetch()`, but follow up to MAX_REDIRECTS redirects one hop at a
    time, so that every hop goes through `fetch()` and counts towards
    MAX_REQUESTS. Each redirect response is closed before the next hop; the
    caller must close the response returned.
    """
    for _ in range(MAX_REDIRECTS + 1):
        response = fetch(session, url, allow_redirects=False, **kwargs)
        target = session.get_redirect_target(response)
        if target is None:
            return response
        response.close()
        url = urljoin(response.url, target)
    raise requests.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")


def extract_links(html: str | bytes, base_url: str) -> list[str]:
    """
    Return a list of absolute URLs for every <a href="..."> in `html`,
    in the order they appear. Relative links are resolved against the page's
    <base href> if it has one, otherwise against `base_url`.
    Duplicates are NOT removed.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = soup.select_one("base[href]")
    if base is not None:
        base_url = urljoin(base_url, str(base["href"]).strip())
    links = []
    for anchor in soup.select("a[href]"):
        try:
            links.append(urljoin(base_url, str(anchor["href"]).strip()))
        except ValueError:  # e.g. a malformed IPv6 host; skip just this link
            continue
    return links


def doc_extension(url: str) -> str:
    """Return the lower-case document extension of url's path, or "" if none."""
    name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext if ext in EXPECTED_SIGNATURE else ""


def is_policy_link(url: str) -> bool:
    """
    Return True if `url` looks like it leads to a downloadable policy
    document, otherwise False.

    A policy link is an http(s) URL whose path, percent-decoded, ends in one
    of the extensions in EXPECTED_SIGNATURE, ignoring case, query string and
    fragment. This trusts the extension; the limitations are in the report.
    """
    return urlsplit(url).scheme in ("http", "https") and doc_extension(url) != ""


def split_by_host(links: list[str], page_url: str) -> tuple[list[str], Counter[str]]:
    """
    Split policy links into distinct same-host URLs, in page order and with
    fragments removed, and a count of distinct other-host URLs per hostname.
    "Same host" means the hostname of `page_url`, the index page's final URL.
    """
    host = urlsplit(page_url).hostname
    same_host: list[str] = []
    other_hosts: Counter[str] = Counter()
    seen: set[str] = set()
    for link in links:
        url = urldefrag(link).url
        if url in seen:
            continue
        seen.add(url)
        hostname = urlsplit(url).hostname
        if hostname == host:
            same_host.append(url)
        else:
            other_hosts[hostname or ""] += 1
    return same_host, other_hosts


def select_documents(links: list[str], count: int) -> list[str]:
    """Pick count links: the first of each extension in page order, then round-robin."""
    groups: dict[str, list[str]] = {}
    for url in links:
        groups.setdefault(doc_extension(url), []).append(url)
    chosen: list[str] = []
    depth = 0
    while len(chosen) < min(count, len(links)):
        for group in groups.values():
            if depth < len(group) and len(chosen) < count:
                chosen.append(group[depth])
        depth += 1
    return chosen


@dataclass
class DownloadResult:
    """What happened when one document was requested."""

    url: str
    final_url: str = ""
    status: int | None = None
    content_type: str | None = None
    content_length: str | None = None
    content_encoding: str | None = None
    bytes_read: int = 0
    capped: bool = False
    head: bytes = b""
    elapsed: float = 0.0
    error: str | None = None


def classify_signature(head: bytes) -> str:
    """Name the type head starts with: PDF, ZIP, OLE2, RTF, HTML or unknown."""
    for magic, name in MAGIC_NUMBERS:
        if head.startswith(magic):
            return name
    text = head.removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    return "HTML" if text.startswith((b"<!doctype", b"<html")) else "unknown"


def verdict(result: DownloadResult) -> str:
    """Return the first verdict that applies to a download, in documented order."""
    if result.error is not None:
        return f"FAILED ({result.error})"
    if result.status is None or not 200 <= result.status < 300:
        return f"HTTP {result.status}"
    if result.bytes_read == 0:
        return "EMPTY"
    if result.capped:
        return "SIZE CAP"
    declared = (result.content_length or "").strip()
    encoded = (result.content_encoding or "identity").lower() != "identity"
    if declared.isdigit() and not encoded and result.bytes_read < int(declared):
        return "INCOMPLETE"
    detected = classify_signature(result.head)
    if detected == "HTML":
        return "LOOKS LIKE HTML"
    if detected != EXPECTED_SIGNATURE.get(doc_extension(result.url)):
        return "SIGNATURE MISMATCH"
    return "OK"


def format_head(head: bytes) -> str:
    """Render bytes as hex, then as ASCII with '.' for non-printables."""
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in head)
    return f"{head.hex(' ')}  |{text}|"


def say(line: str = "", err: bool = False) -> None:
    """Print line to stdout (or stderr), escaping anything outside ASCII."""
    text = line.encode("ascii", "backslashreplace").decode("ascii")
    print(text, file=sys.stderr if err else sys.stdout)


def describe_download(session: requests.Session, url: str) -> str:
    """
    Download the document at `url`, print its metadata and return its
    verdict (see `verdict()`).

    The body is streamed and counted, not saved; only its first HEAD_SIZE
    bytes are kept. A `requests` error becomes a FAILED verdict, while
    RequestLimitExceeded propagates to the caller.
    """
    result = DownloadResult(url)
    start, paused = time.perf_counter(), _seconds_paused
    try:
        with fetch_with_redirects(session, url, stream=True) as response:
            result.final_url = response.url
            result.status = response.status_code
            result.content_type = response.headers.get("Content-Type")
            result.content_length = response.headers.get("Content-Length")
            result.content_encoding = response.headers.get("Content-Encoding")
            for chunk in response.iter_content(CHUNK_SIZE):
                result.head += chunk[: HEAD_SIZE - len(result.head)]
                result.bytes_read += len(chunk)
                if result.bytes_read > SIZE_CAP:
                    result.capped = True
                    break
    except requests.RequestException as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    # The pauses between requests are politeness, not transfer time.
    result.elapsed = time.perf_counter() - start - (_seconds_paused - paused)

    fields = [("Link URL", result.url)]
    if result.final_url and result.final_url != result.url:
        fields.append(("Final URL", result.final_url))
    fields.append(("HTTP status", "-" if result.status is None else str(result.status)))
    fields.append(("Content-Type", result.content_type or "(none)"))
    if result.content_length is not None:
        fields.append(("Content-Length", result.content_length))
    if result.content_encoding is not None:
        fields.append(("Content-Encoding", result.content_encoding))
    expected = EXPECTED_SIGNATURE.get(doc_extension(url), "unknown")
    fields += [
        ("Bytes downloaded", str(result.bytes_read)),
        ("Elapsed", f"{result.elapsed:.3f} s"),
        ("First bytes", format_head(result.head) if result.head else "(none)"),
        ("Signature", f"{classify_signature(result.head)} (expected {expected})"),
        ("Verdict", verdict(result)),
    ]
    for label, value in fields:
        say(f"  {label + ':':<18}{value}")
    return verdict(result)


def main(argv: list[str] | None = None) -> int:
    """
    main routine. Returns 0 if the index page loaded and at least one
    document's verdict is OK (or --list succeeded), otherwise 1.
    """

    parser = argparse.ArgumentParser(
        description="Minimal policy-document scraper prototype (CITS5501, Group 11)."
    )
    parser.add_argument(
        "start_url",
        nargs="?",
        default=START_URL,
        help=f"index page to start from (default: {START_URL})",
    )
    parser.add_argument(
        "--count",
        type=int,
        choices=range(3, 6),
        default=DEFAULT_COUNT,
        metavar="{3,4,5}",
        help=f"number of documents to download (default: {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print every policy-document link found and exit without downloading",
    )
    args = parser.parse_args(argv)

    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        # Ask for unencoded bodies so byte counts are comparable with Content-Length.
        session.headers["Accept-Encoding"] = "identity"

        say(f"Fetching index page: {args.start_url}", err=True)
        try:
            with fetch_with_redirects(session, args.start_url) as response:
                page_url, html = response.url, response.content
                status = response.status_code
                content_type = response.headers.get("Content-Type")
        except (requests.RequestException, RequestLimitExceeded) as exc:
            say(f"error: index page: {type(exc).__name__}: {exc}", err=True)
            return 1
        say(f"Index page: {page_url}")
        say(f"  status: {status}, content type: {content_type}")
        if not 200 <= status < 300:
            say(f"error: index page returned HTTP {status}", err=True)
            return 1

        links = extract_links(html, page_url)
        say(f"Found {len(links)} links.")
        policy_links = [link for link in links if is_policy_link(link)]
        documents, other_hosts = split_by_host(policy_links, page_url)
        by_ext = Counter(doc_extension(url) for url in documents)
        exts = ", ".join(f"{ext} {n}" for ext, n in by_ext.most_common())
        say(
            f"{len(documents)} of these look like policy documents on this host"
            + (f" ({exts})." if exts else ".")
        )
        if other_hosts:
            hosts = ", ".join(f"{h} {n}" for h, n in other_hosts.most_common())
            skipped = sum(other_hosts.values())
            say(f"Skipped {skipped} policy-document links on other hosts ({hosts}).")

        ok = 0
        if args.list:
            for url in documents:
                say(url)
        else:
            chosen = select_documents(documents, args.count)
            verdicts: list[str] = []
            for number, url in enumerate(chosen, 1):
                say(f"downloading {number}/{len(chosen)}: {url}", err=True)
                say()
                say(f"[{number}/{len(chosen)}]")
                try:
                    verdicts.append(describe_download(session, url))
                except RequestLimitExceeded as exc:
                    say("  (not downloaded: request limit reached)")
                    say(f"stopping: {exc}", err=True)
                    break
            ok = verdicts.count("OK")
            say()
            say(
                f"Downloaded {len(verdicts)}: {ok} OK, {len(verdicts) - ok} with problems."
            )

    say(f"Requests made: {_request_count} (limit {MAX_REQUESTS})")
    return 0 if args.list or ok else 1


if __name__ == "__main__":
    sys.exit(main())
