CITS5501 Group 11 - Web Scraper Project

Repository for CITS5501/3501 Software Testing and Quality Assurance Group Project.

## Task 1 prototype: how to run

The Task 1 prototype (`src/policy_scraper/prototype.py`) fetches the UWA
policy library index page once and finds the links on it that point to policy
documents (pdf, doc, docx, rtf, odt, ppt, pptx, xls, xlsx) on the same host.
It then downloads a few of them (5 by default) and prints metadata about each,
including a check that the file's first bytes match its extension. Downloads
are counted and inspected in memory, not saved to disk.

There is no compilation step. It is a Python program and runs directly with
the Python interpreter.

### Requirements

- Python 3.10 or later. Ubuntu 22.04's system `python3` is 3.10, which is
  enough.
- Internet access to `https://uwa-policy.cits5501.arranstewart.io/` and, for
  installing dependencies, to PyPI.
- [uv](https://docs.astral.sh/uv/) 0.9 or later (recommended). To install it:
  `curl -LsSf https://astral.sh/uv/install.sh | sh`

### Install and run with uv (recommended)

From the repository root:

```sh
uv sync
uv run python -m policy_scraper.prototype
```

`uv sync` creates `.venv/` and installs the exact dependency versions recorded
in `uv.lock`, using Python 3.10 as pinned in `.python-version`. It uses the
system Python 3.10 if present and downloads one otherwise. `uv run` on its own
also performs this step if needed.

### Install and run without uv (pip)

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install requests beautifulsoup4
python src/policy_scraper/prototype.py
```

On Ubuntu, if `python3 -m venv` reports that `ensurepip is not available`,
install venv support first with `sudo apt install python3-venv` and repeat
the steps. This route installs the latest versions of the two dependencies
rather than the locked ones.

### Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--start-url URL` | `https://uwa-policy.cits5501.arranstewart.io/policy/` | Index page to scan. Only this page is scanned; links are not followed further. |
| `--count N` | `5` | Number of documents to download (the brief asks for 3 to 5). |
| `--list` | off | Print every document URL found and exit without downloading. |
| `--timeout S` | `10` | Timeout in seconds for each HTTP request. |
| `--delay S` | `1` | Pause in seconds between HTTP requests. |
| `-h`, `--help` | | Show usage and exit. |

Examples:

```sh
uv run python -m policy_scraper.prototype --list
uv run python -m policy_scraper.prototype --count 3
uv run python -m policy_scraper.prototype --start-url http://localhost:3000
```

### Limits on network use

- At most 20 HTTP requests per run, counting every redirect hop. Redirects
  are followed one hop at a time, up to 5 per document. If the next request
  would exceed 20, the run stops and says so.
- One request at a time, with `--delay` seconds between requests, and no
  retries.

### Output

Results are written to standard output, while progress messages and errors go
to standard error. All output is plain ASCII.

**Discovery summary**, printed first:

| Field | Meaning |
| --- | --- |
| `Index` | URL of the index page after any redirects. Links are resolved against it, or against the page's `<base href>` if it has one. |
| `Anchors scanned` | Number of `<a>` elements with an `href` on the page. |
| `Document links` | Distinct links on the same host whose path ends in a document extension (case-insensitive, after percent-decoding, ignoring query string and fragment). |
| `By extension` | The same links counted by extension. |
| `Skipped, other host` | Distinct document links on other hosts, which are not downloaded, with a count per hostname. |

Documents are then chosen deterministically. The first link of each extension
is taken in page order, then the remaining links round-robin across
extensions until `--count` documents have been chosen.

**One block per document**, headed `[n/N]`:

| Field | Meaning |
| --- | --- |
| `Link URL` | The document link as found on the index page (fragment removed). |
| `Final URL` | Where redirects ended. Shown only if different from the link URL. |
| `HTTP status` | Status code of the final response, or `-` if no response arrived. |
| `Content-Type` | Content type declared by the server, or `(none)`. |
| `Content-Length` | Length declared by the server. Shown only if sent. |
| `Content-Encoding` | Encoding declared by the server. Shown only if sent. The prototype asks for unencoded responses, so it is normally absent. |
| `Bytes downloaded` | Body bytes received. Streaming stops once past 50 MB. |
| `Elapsed` | Seconds from the first request to the end of the body, including redirect hops but not the `--delay` pauses. |
| `First bytes` | The first 16 bytes in hex, then as ASCII with `.` for non-printable bytes. |
| `Signature` | File type detected from the first bytes, with the type expected from the extension in brackets. |
| `Verdict` | Overall result. See below. |

Detected signatures are `PDF` (`%PDF`), `ZIP` (`PK\x03\x04`, used by docx,
pptx, xlsx and odt), `OLE2` (`D0 CF 11 E0 A1 B1 1A E1`, used by doc, ppt and
xls), `RTF` (`{\rtf`), `HTML` (`<!doctype` or `<html`, possibly after a byte
order mark or whitespace) and `unknown`.

The verdict is the first of these that applies, checked in this order:

| Verdict | Meaning |
| --- | --- |
| `FAILED (<error>)` | The request raised an error, such as a connection failure, a timeout or too many redirects. |
| `HTTP <code>` | The final status code was not 2xx. |
| `EMPTY` | The response had no body. |
| `SIZE CAP` | The body was larger than 50 MB and streaming was stopped. |
| `INCOMPLETE` | Fewer bytes arrived than `Content-Length` declared. This check is skipped if the length is missing or the body was encoded. |
| `LOOKS LIKE HTML` | The body starts like a web page, for example an error or login page served in place of the document. |
| `SIGNATURE MISMATCH` | The detected signature is not the one expected for the extension. |
| `OK` | None of the above. |

**Summary line**, printed last: documents attempted, how many were `OK`, how
many had any other verdict, and HTTP requests used out of 20.

The exit status is 0 if the index page loaded and at least one document is
`OK` (or `--list` succeeded), and 1 otherwise.

### Example output

The excerpt below is from a run with default options on 13 October 2026
(UTC). It shows the standard output, with documents 3 to 5 replaced by `...`.
The progress lines that go to standard error are omitted.

```text
Index: https://uwa-policy.cits5501.arranstewart.io/policy/
Anchors scanned: 413
Document links: 216
By extension: doc 96, pdf 65, docx 48, rtf 4, pptx 3
Skipped, other host: 1 (www.uwa.edu.au 1)

[1/5]
  Link URL:         https://uwa-policy.cits5501.arranstewart.io/policy/-/media/project/uwa/uwa/policy-library/policy/code-of-conduct/integrity/policy-framework/policy-governance-framework.pdf
  HTTP status:      200
  Content-Type:     application/pdf
  Content-Length:   301201
  Bytes downloaded: 301201
  Elapsed:          0.219 s
  First bytes:      25 50 44 46 2d 31 2e 36 0d 25 e2 e3 cf d3 0d 0a  |%PDF-1.6.%......|
  Signature:        PDF (expected PDF)
  Verdict:          OK

[2/5]
  Link URL:         https://uwa-policy.cits5501.arranstewart.io/policy/-/media/project/uwa/uwa/policy-library/policy/code-of-conduct/integrity/delegations/delegations-framework-policy.doc
  HTTP status:      200
  Content-Type:     application/msword
  Content-Length:   256512
  Bytes downloaded: 256512
  Elapsed:          0.220 s
  First bytes:      d0 cf 11 e0 a1 b1 1a e1 00 00 00 00 00 00 00 00  |................|
  Signature:        OLE2 (expected OLE2)
  Verdict:          OK

...

Summary: attempted 5, OK 5, problems 0, requests used 6 of 20
```

### Static checks

These need the uv setup above. Run them from the repository root:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

CI runs the same three commands on Ubuntu 22.04 with Python 3.10 for every
push and every pull request to `main`.
