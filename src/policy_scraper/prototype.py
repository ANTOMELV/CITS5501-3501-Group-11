"""Task 1 proof of concept: find policy-document links on one index page,
download a few of them and print metadata about each.

Run with: uv run python -m policy_scraper.prototype [--help]
"""

import argparse
import sys
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

DEFAULT_START_URL = "https://uwa-policy.cits5501.arranstewart.io/policy/"
DEFAULT_COUNT = 5
DEFAULT_TIMEOUT = 10.0
DEFAULT_DELAY = 1.0
MAX_REQUESTS = 20
MAX_REDIRECTS = 5
SIZE_CAP = 50 * 1024 * 1024
HEAD_SIZE = 16
CHUNK_SIZE = 64 * 1024
USER_AGENT = (
    "cits5501-group11-policy-prototype/0.1 "
    "(+https://github.com/ANTOMELV/CITS5501-3501-Group-11)"
)

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


class BudgetExhausted(Exception):
    """Raised when the next request would exceed the per-run request budget."""


class RequestBudget:
    """Counts HTTP requests, every redirect hop included, and paces them."""

    def __init__(self, limit: int = MAX_REQUESTS, delay: float = DEFAULT_DELAY) -> None:
        self.limit = limit
        self.delay = delay
        self.used = 0
        self.waited = 0.0

    def charge(self) -> None:
        """Account for one request, sleeping first unless it is the run's first."""
        if self.used >= self.limit:
            raise BudgetExhausted(f"request budget of {self.limit} exhausted")
        if self.used and self.delay > 0:
            start = time.perf_counter()
            time.sleep(self.delay)
            self.waited += time.perf_counter() - start
        self.used += 1


@dataclass
class Discovery:
    """Document links found on an index page."""

    anchors: int
    links: list[str]
    off_host: Counter[str]  # hostname -> distinct document-like URLs skipped


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


def doc_extension(url: str) -> str:
    """Return the lower-case document extension of url's path, or "" if none."""
    name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext if ext in EXPECTED_SIGNATURE else ""


def extract_doc_links(html: str | bytes, page_url: str) -> Discovery:
    """Find same-host document links in page order, without fragments or repeats.

    page_url is the URL the page was finally served from; hrefs resolve against
    a <base href> if the page has one, but "same host" always means page_url's.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = soup.select_one("base[href]")
    base_url = page_url
    if base is not None:
        base_url = urljoin(page_url, str(base["href"]).strip())
    host = urlsplit(page_url).hostname
    anchors = soup.select("a[href]")
    links: list[str] = []
    off_host: Counter[str] = Counter()
    seen: set[str] = set()
    for anchor in anchors:
        try:
            url = urldefrag(urljoin(base_url, str(anchor["href"]).strip())).url
            parts = urlsplit(url)
        except ValueError:  # e.g. a malformed IPv6 host; skip just this link
            continue
        if parts.scheme not in ("http", "https") or not doc_extension(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        if parts.hostname == host:
            links.append(url)
        else:
            off_host[parts.hostname or ""] += 1
    return Discovery(len(anchors), links, off_host)


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


def open_url(
    session: requests.Session, url: str, budget: RequestBudget, timeout: float
) -> requests.Response:
    """GET url streamed, following up to MAX_REDIRECTS redirects by hand.

    Every hop is charged to budget and closed before the next; the caller must
    close the returned response.
    """
    for _ in range(MAX_REDIRECTS + 1):
        budget.charge()
        response = session.get(url, stream=True, timeout=timeout, allow_redirects=False)
        target = session.get_redirect_target(response)
        if target is None:
            return response
        response.close()
        url = urljoin(response.url, target)
    raise requests.TooManyRedirects(f"more than {MAX_REDIRECTS} redirects")


def fetch_index(
    session: requests.Session, url: str, budget: RequestBudget, timeout: float
) -> tuple[str, bytes]:
    """Fetch the index page and return its final URL and body; raise on HTTP errors."""
    with open_url(session, url, budget, timeout) as response:
        response.raise_for_status()
        return response.url, response.content


def download_document(
    session: requests.Session, url: str, budget: RequestBudget, timeout: float
) -> DownloadResult:
    """Stream one document without saving it, recording what came back.

    requests exceptions are recorded in the result; BudgetExhausted propagates.
    """
    result = DownloadResult(url)
    start, waited = time.perf_counter(), budget.waited
    try:
        with open_url(session, url, budget, timeout) as response:
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
    # Pacing sleeps are politeness, not transfer time.
    result.elapsed = time.perf_counter() - start - (budget.waited - waited)
    return result


def say(line: str = "", err: bool = False) -> None:
    """Print line to stdout (or stderr), escaping anything outside ASCII."""
    text = line.encode("ascii", "backslashreplace").decode("ascii")
    print(text, file=sys.stderr if err else sys.stdout)


def print_discovery(discovery: Discovery) -> None:
    """Print the anchor count, document links by extension and off-host skips."""
    by_ext = Counter(doc_extension(url) for url in discovery.links)
    exts = ", ".join(f"{ext} {n}" for ext, n in by_ext.most_common())
    hosts = ", ".join(f"{host} {n}" for host, n in discovery.off_host.most_common())
    say(f"Anchors scanned: {discovery.anchors}")
    say(f"Document links: {len(discovery.links)}")
    say(f"By extension: {exts or '-'}")
    say(f"Skipped, other host: {sum(discovery.off_host.values())} ({hosts or '-'})")


def print_result(number: int, total: int, result: DownloadResult) -> None:
    """Print one labelled field per line for a download."""
    fields = [("Link URL", result.url)]
    if result.final_url and result.final_url != result.url:
        fields.append(("Final URL", result.final_url))
    fields.append(("HTTP status", "-" if result.status is None else str(result.status)))
    fields.append(("Content-Type", result.content_type or "(none)"))
    if result.content_length is not None:
        fields.append(("Content-Length", result.content_length))
    if result.content_encoding is not None:
        fields.append(("Content-Encoding", result.content_encoding))
    expected = EXPECTED_SIGNATURE.get(doc_extension(result.url), "unknown")
    fields += [
        ("Bytes downloaded", str(result.bytes_read)),
        ("Elapsed", f"{result.elapsed:.3f} s"),
        ("First bytes", format_head(result.head) if result.head else "(none)"),
        ("Signature", f"{classify_signature(result.head)} (expected {expected})"),
        ("Verdict", verdict(result)),
    ]
    say(f"[{number}/{total}]")
    for label, value in fields:
        say(f"  {label + ':':<18}{value}")


def run(session: requests.Session, args: argparse.Namespace) -> int:
    """Discover, select and download documents with session; return the exit code."""
    budget = RequestBudget(MAX_REQUESTS, args.delay)
    try:
        page_url, html = fetch_index(session, args.start_url, budget, args.timeout)
    except (requests.RequestException, BudgetExhausted) as exc:
        say(f"error: index {args.start_url}: {type(exc).__name__}: {exc}", err=True)
        return 1
    discovery = extract_doc_links(html, page_url)
    say(f"Index: {page_url}")
    print_discovery(discovery)
    if args.list:
        for url in discovery.links:
            say(url)
        return 0
    chosen = select_documents(discovery.links, args.count)
    results: list[DownloadResult] = []
    for number, url in enumerate(chosen, 1):
        say(f"downloading {number}/{len(chosen)}: {url}", err=True)
        try:
            result = download_document(session, url, budget, args.timeout)
        except BudgetExhausted:
            left = len(chosen) - number + 1
            say(
                f"stopping: request budget of {budget.limit} reached, {left} left",
                err=True,
            )
            break
        results.append(result)
        say()
        print_result(number, len(chosen), result)
    ok = sum(verdict(r) == "OK" for r in results)
    say()
    say(
        f"Summary: attempted {len(results)}, OK {ok}, problems {len(results) - ok}, "
        f"requests used {budget.used} of {budget.limit}"
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Parse the command line, open one session and run the prototype."""
    parser = argparse.ArgumentParser(
        description="Find policy-document links on one index page and download a few."
    )
    add = parser.add_argument
    add("--start-url", default=DEFAULT_START_URL, help="index page URL")
    add("--count", type=int, default=DEFAULT_COUNT, help="documents to fetch (5)")
    add("--list", action="store_true", help="list document URLs and exit")
    add("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds (10)")
    add("--delay", type=float, default=DEFAULT_DELAY, help="pause in seconds (1)")
    args = parser.parse_args(argv)
    if args.count < 1 or args.timeout <= 0 or args.delay < 0:
        parser.error("need --count >= 1, --timeout > 0 and --delay >= 0")
    with requests.Session() as session:
        session.headers["User-Agent"] = USER_AGENT
        session.headers["Accept-Encoding"] = "identity"
        return run(session, args)


if __name__ == "__main__":
    sys.exit(main())
