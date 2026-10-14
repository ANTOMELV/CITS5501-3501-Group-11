# Task 1 prototype: notes for the report

Sources: `src/policy_scraper/prototype.py` (commit 2978030), the site recon from
14 Oct 2026, which made one request to the index page, and one live run with
default options at 2026-10-13 18:54 UTC (14 Oct, 02:54 Perth time).

## 1. What it does

The prototype fetches one index page, by default
`https://uwa-policy.cits5501.arranstewart.io/policy/`. It keeps the `<a href>`
links that, once resolved against the page's final URL, are http(s) links on
the same hostname whose path ends in pdf, doc, docx, rtf, odt, ppt, pptx, xls
or xlsx, with fragments removed and duplicates dropped. It then selects
`--count` documents (default 5): the first link of each extension in page
order, then round-robin across extensions. Each selected document is requested
once and streamed without being saved, while the prototype records the HTTP
status, declared headers, bytes received, elapsed time and first 16 bytes. It
prints a labelled block with a verdict for each document and a summary line,
and exits 0 if the index loaded and at least one document is OK.

Run command, from the repository root:

```sh
uv run python -m policy_scraper.prototype
```

Without uv, after `pip install requests beautifulsoup4` in a venv:

```sh
python src/policy_scraper/prototype.py
```

## 2. Design choices

- **Same-host and extension filter:** a link is kept only if its hostname
  equals that of the index page's final URL and its percent-decoded path, with
  the query string ignored, ends in one of the nine extensions. On the live
  page 216 of 413 anchors qualified and 1 off-host document link was skipped.
- **One-per-type selection:** taking the first link of each extension before
  any repeats meant the 5 downloads covered every extension present (pdf, doc,
  docx, pptx, rtf), even though doc makes up 96 of the 216 links.
- **Streaming without saving:** bodies are read in 64 KiB chunks, only the
  first 16 bytes and a byte count are kept, and reading stops past 50 MB.
  Nothing is written to disk.
- **20-request budget including redirect hops:** redirects are followed by
  hand (`allow_redirects=False`, at most 5 per URL) so that every hop is
  counted. There is a 1 s pause between requests, and the run stops before a
  21st request. The live run used 6 requests.
- **Signature checks:** `%PDF` = PDF; `PK\x03\x04` = ZIP (docx, pptx, xlsx,
  odt); `D0 CF 11 E0 A1 B1 1A E1` = OLE2 (doc, ppt, xls); `{\rtf` = RTF. The
  check also detects HTML bodies. The brief's PK/%PDF hint alone would flag
  every legacy .doc as wrong. The site has 96 .doc links (44% of its document
  links), and the live run's .doc began `d0 cf 11 e0`.

## 3. Results (live run, default options)

| # | File | Status | Declared Content-Type | Bytes (= Content-Length) | First 8 bytes | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | policy-governance-framework.pdf | 200 | application/pdf | 301,201 | `25 50 44 46 2d 31 2e 36` (`%PDF-1.6`) | OK |
| 2 | delegations-framework-policy.doc | 200 | application/msword | 256,512 | `d0 cf 11 e0 a1 b1 1a e1` | OK |
| 3 | risk-policy.docx | 200 | application/vnd.openxmlformats-officedocument.wordprocessingml.document | 154,182 | `50 4b 03 04 14 00 06 00` (`PK..`) | OK |
| 4 | fraud-and-corruption-training.pptx | 200 | application/vnd.openxmlformats-officedocument.presentationml.presentation | 4,298,871 | `50 4b 03 04 14 00 06 00` (`PK..`) | OK |
| 5 | fees-payment-and-refund-of-tuition-fees-for-domestic-postgraduate-students-policy.rtf | 200 | application/rtf | 207,353 | `7b 5c 72 74 66 31 5c 61` (`{\rtf1\a`) | OK |

The full output, with all 16 bytes and the elapsed times (0.219 to 0.929 s),
is in `scratch/live-run.txt`.

All five documents, one per extension present, returned 200. Each had a byte
count equal to its Content-Length and the expected signature. The run used 6
of its 20 requests (index plus 5 documents, no redirects) and exited 0.

## 4. Assumptions

Numbered per our Phase 1 convention; renumber if other sections use the same
series.

- **A001** The documents of interest are linked from the single index page.
  Only that page is scanned; there is no crawling.
- **A002** A link is a document if its URL path, percent-decoded, ends in pdf,
  doc, docx, rtf, odt, ppt, pptx, xls or xlsx (case-insensitive). The query
  string and fragment are ignored for this test.
- **A003** Only documents on the same hostname as the index page's final URL
  are in scope. The scheme and port may differ.
- **A004** Links that differ only in their fragment are the same document.
  Links that differ in their query string are different documents.
- **A005** The first `<base href>` on the page, if any, governs how relative
  links resolve.
- **A006** A 2xx final status means the document was served. Up to 5 redirects
  are followed. Any other status is reported, not retried.
- **A007** The first 16 bytes are enough to judge whether a body is plausibly
  the type its extension claims.
- **A008** The server honours `Accept-Encoding: identity`, so bytes received
  can be compared with Content-Length. If it encodes the body anyway, the
  comparison is skipped.
- **A009** A document does not need to be read past 50 MB to be assessed;
  anything larger gets the `SIZE CAP` verdict.
- **A010** The defaults are acceptable load on the site: 5 documents, a 1 s
  pause between requests, a 10 s timeout per request and at most 20 requests
  per run.

## 5. Limitations

1. **The extension filter both misses documents and trusts misleading
   extensions.**
   - Recon found four PDFs whose URL path has no extension: three SharePoint
     `AllItems.aspx?id=…/….pdf` links and one Outlook safelinks wrapper around
     a SharePoint `:b:` (PDF) link. They are neither downloaded nor reported;
     the run's "Skipped, other host" count is 1, not 5.
   - In the other direction, any same-host path ending in `.pdf` and so on is
     listed as a document unchecked. Only the 5 downloaded (of 216) were
     verified, and a misleading link is caught only if it happens to be
     downloaded, as `LOOKS LIKE HTML` or `SIGNATURE MISMATCH`. None was seen
     in the run.
2. **The signature check is shallow.** Only the first 16 bytes are examined.
   - Corruption after the header goes undetected, so the five OK verdicts rest
     on the header and the byte count alone.
   - Truncation is only detected when Content-Length is present (and the body
     is not encoded). All five responses carried one, so this path was not
     exercised.
   - OLE2 is shared by doc, xls and ppt, so an .xls served at a .doc URL
     passes.
   - `PK\x03\x04` matches any ZIP. In the run, the .docx and .pptx share their
     first 14 of 16 bytes (`50 4b 03 04 14 00 06 00 08 00 00 00 21 00`), so
     the header cannot tell a docx from a pptx, or from a plain .zip.
3. **The same-host rule drops documents hosted elsewhere.** The run skipped 1
   off-host document: `Fitness-to-Study.doc` on www.uwa.edu.au, linked 3
   times. The index also links a same-host `fitness-to-study.pdf`. The
   SharePoint PDFs in point 1 are off-host as well.
4. **There are no retries, no backoff and no Retry-After handling.** Each URL
   gets one attempt. A requests exception becomes `FAILED (…)`, and a 429 or
   503 becomes `HTTP 429` or `HTTP 503`, without reading Retry-After. The
   pause is a fixed 1 s whatever the server returns. The live run had only
   200 responses, so none of this was exercised.
5. **De-duplication treats case variants as different files.** URLs are
   compared as exact strings after the fragment is removed, so differences in
   path case, host case or percent-encoding (`%2E` versus `.`) count as
   different documents.
   - Recon found no case-folded duplicates among the 216 same-host links, so
     the run was unaffected.
   - The site does spell the same path both ways in its links, though:
     same-host links use `/project/uwa/uwa/policy-library/…` and the off-host
     Fitness-to-Study link uses `/Project/UWA/UWA/Policy-Library/…`. Whether
     the server treats such paths as one file was not tested.
6. **Only `<a href>` is scanned.** Documents linked from iframes, embeds,
   forms or scripts would be missed. On this page that loses nothing: recon
   found 1 iframe (Google Tag Manager) and 1 form (site search), neither of
   them a document.
7. **The index page is read in full, with no size cap.** It was 584,187 bytes
   at recon. The 50 MB cap applies to documents only.

## 6. Handover for the Task 2, 3 and 4 owners

1. **Local server:** run `uv run python -m policy_scraper.prototype --start-url http://localhost:3000 --delay 0`. Documents must be linked on the same hostname (the port is ignored), and `--list` shows what was found without downloading anything.
2. **Functions that take a session:** `open_url`, `fetch_index` and `download_document` take `(session, url, budget, timeout)`, and `run(session, args)` runs the whole flow. Only `main()` creates the real `requests.Session`, so a test can pass a session wired to responses, requests-mock or a custom adapter.
3. **Network-free parts:** `doc_extension`, `extract_doc_links`, `select_documents`, `classify_signature`, `verdict` and `format_head` are pure. `RequestBudget(limit, delay)` counts requests and raises `BudgetExhausted` past the limit. The limits are module constants (`MAX_REQUESTS`, `MAX_REDIRECTS`, `SIZE_CAP`, `HEAD_SIZE`).
4. **Redirects, off-site links and mismatches on the live site:** no redirects were seen (the index answered 200 directly, and 6 requests served 6 URLs). The off-site document links are one .doc on www.uwa.edu.au plus PDFs hidden in SharePoint and safelinks query strings. There were no signature mismatches in the 5 documents checked, leaving 211 same-host documents unchecked.
5. **Speed:** documents took 0.22 to 0.93 s, the largest being the 4,298,871-byte .pptx at 0.929 s. Nothing came near the 10 s timeout, so the slow-response, `FAILED`, `HTTP <code>` and `INCOMPLETE` paths were not exercised live and need test doubles.
