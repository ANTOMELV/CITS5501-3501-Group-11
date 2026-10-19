# Phase 2 - Task 4: Adversarial Inputs

Suppose we were testing another team's scraper. We started by writing down what a normal ("happy path")
run looks like, and then asked at each step what the scraper is quietly relying on:

1. Fetch the index page, which is complete, well-formed HTML.
2. Find links, where each link to a document is a different document and its URL ends in `.pdf`, `.docx` and so on.
3. Request each document. The server answers directly, quickly and honestly.
4. Decide it is a "good" document from the status code, the first few bytes and the length.
5. Stay within the request limits.

Each idea below attacks one of those steps. The scraper is supposed to say the run failed when it
did, so a "failure" in every row means the scraper reports something that is not true (a false `OK`,
a miscount, a silent skip) or breaks one of its own limits.

We wrote the Task 2 and Task 3 material and the Task 1 prototype ourselves, so for the table we
treated the prototype as if it were another team's code. We ran ideas 1, 2, 4 and 5 against it with
`responses` mocks and they do break it (marked **confirmed**). Ideas 3, 6, 7 and 8 come from reading the
code and from how `requests` behaves (marked **predicted**), and they would need a fixture to confirm.

## 4.1 Ideas

| # | (a) Assumption being challenged | (b) Input artifact or environment | (c) What counts as failure, and how we detect it |
|---|---|---|---|
| **A1** | A body that starts with the right magic bytes is a complete, usable file. | A local fixture server returns `200` for `/policy/cut-off.pdf` with `Content-Type: application/pdf`, a body that starts `%PDF-1.7` followed by a few hundred bytes, **no `%%EOF`**, and **no `Content-Length`** (chunked transfer). Variants: a 4-byte body `%PDF`, and a `.docx` that is only the first 100 bytes of a real one. | The scraper reports `OK`. Detect this by asserting that the verdict is not `OK` and that the reason mentions truncation. A second check is to open the same bytes in a real PDF library, which fails. **Confirmed**: the prototype gives `OK` for all three, because it only checks 16 bytes and compares with `Content-Length` when that header exists. |
| **A2** | The file extension and the signature together identify the file type. | Fixtures that carry a legitimate signature of the wrong kind: (i) a plain `.zip` (containing `hello.txt`) served at `/policy/a.docx`; (ii) a `.pptx` served at `.docx`; (iii) an OLE2 file that is really an `.xls` served at `.doc`. | The scraper reports `OK` for a file that Word cannot open as a document. Detect by checking the verdict, or by looking for the OOXML parts (`[Content_Types].xml`, `word/document.xml`). **Confirmed** for (i) and (iii), because `PK\x03\x04` and OLE2 are shared by many types. |
| **A3** | The size on the wire is the size of the file, and the server does what we ask for in headers. | A server that ignores `Accept-Encoding: identity` and sends `Content-Encoding: gzip`, with a `Content-Length` that is the compressed size. Variants: `Content-Length` larger than the body (short read) and `Content-Length: 0` on a non-empty body. | The scraper reports a size that disagrees with the file, or skips the `INCOMPLETE` check and reports `OK` on a short body. Detect by comparing the reported size with a size computed in the test. **Predicted**: `verdict()` skips the length check whenever the body is encoded, and `requests` decodes the stream, so the two counts are not comparable. |
| **A4** | Links that differ are different documents. | An index page that links the same file many ways: `/x/Policy.PDF`, `/x/policy.pdf`, `/x/p%2Epdf`, `/x/p.pdf`, and `/x/a.pdf?download=1` and `?download=2` on one file. The fixture server serves the same bytes for all of them. | The scraper counts them as distinct, so "3 distinct documents" can be one file downloaded three times and the request budget is wasted. Detect by comparing the SHA-256 of the downloaded bodies against the number of "distinct" documents reported. **Confirmed**: all six URLs were kept as separate documents. |
| **A5** | Every document link looks like a document link. | An index page whose real documents are linked as `/download?id=7`, `/files/get/7` or `/x/report.pdf/` (the `Content-Disposition` header carries the filename), next to a page with a normal `.pdf` link. | The scraper finds only the normal link, or finds nothing and still exits 0 with a short list. Detect with a fixture where the number of real documents is known, and assert that the number found equals it (or that the run says some links were not understood). **Confirmed**: `/download?id=7` is dropped without any message. |
| **A6** | Redirects stay where we expect and only the original link is checked. | A fixture where a same-host document link `302`s to (i) a different host that serves a valid PDF, (ii) a chain of 5 and 6 hops, and (iii) an `http://` URL after an `https://` request. | (i) The same-host rule is applied to the link and never to where the redirect ends, so an off-host file is accepted as in scope. (ii) The hop limit is off by one or the request budget is not shared with the hops. (iii) A downgrade passes without any warning. Detect from the final URL and the request counter. **Predicted** from `fetch_with_redirects()`, which follows every hop and only reports the final URL. |
| **A7** | A request is bounded in time because it has a timeout. | A slow-drip server that sends one byte every 5 seconds forever for a 1 MB document, and a second one that stalls after the headers. The per-request timeout is 10 s. | Under `requests`, the timeout applies between bytes, not to the whole transfer, so the first server is never timed out and one document can hold the run for hours. Detect with a wall-clock limit in the test: the run must finish in under a fixed time or the test fails. **Predicted**: there is no deadline on the whole run or on a whole download, only the 20-request cap. |
| **A8** | The index page is small, and relative links mean what we think. | (i) An index with `<base href="https://other.test/">` so every relative link resolves off-host; (ii) a 200 MB index; (iii) an index declared as UTF-8 that contains invalid bytes. | (i) The run finds zero documents and may exit as if nothing were wrong, or downloads from the wrong site. (ii) The whole page is read into memory with no cap, so memory grows without limit. (iii) Parsing raises an exception that is not handled. Detect with the exit code, the memory use of the process, and whether a traceback reaches stderr. **Predicted**: the index is read with `response.content` and the 50 MB cap applies only to documents. |

## 4.2 How we would use these

A1, A2 and A3 are all about the same weakness: a verdict of `OK` is based on very little. They are
the ideas we would run first against any scraper, because a false `OK` is the failure Task 2 says is
worst. A4 and A5 attack the link-finding step in the two directions (too many "documents" and too few).
A6, A7 and A8 attack the environment itself, meaning the network path, time and the size of the page.

Each idea needs only a small fixture server or a `responses` mock, so it can run in CI with no live
requests, which follows what we decided in Tasks 2 and 3.
