# Phase 2 – Task 2: Test Suite Design for the Policy Library Scraper

For this task we had to design a test suite for a fully working version of our policy library scraper,
not for the prototype we built in Task 1. In section 2.2 we go through each of the five scenarios from
the brief and say what we think the scraper should do. Section 2.3 has the test cases themselves, with
the setup, the action and what we would check. Section 2.4 explains the reasoning behind the suite.

One thing shaped most of our decisions. The scraper only ever runs against the policy library site,
which we do not control and cannot fix. So we cannot rely on the site behaving reasonably, and we had
to decide in advance what the scraper does when it doesn't. Writing those decisions down as test cases
is the point of this task.

---

## 2.1 Conventions we use throughout

Our Phase 1 report already settled most of the tooling for this. We are using pytest, with `responses`
or `requests-mock` to fake HTTP responses, and saved HTML pages committed to the repository as test
data (Question 14). We also agreed that tests needing the real website stay out of the main CI
workflow and run as a separate scheduled job instead (Question 15). Both decisions carry over here.
Everything below runs against fixture pages or a faked transport, with the single exception of T011,
which is the scheduled live test.

For a test to check anything, the scraper's behaviour has to be visible from outside. So we fixed the
following conventions, and the scraper is expected to follow them.

**What happens to each document.** Every link we attempt ends up with exactly one of these outcomes:

- `OK`
- `HTTP_ERROR`
- `TIMEOUT`
- `MALFORMED_DOCUMENT`
- `ACCESS_DENIED`, for a 401 or 403
- `NOT_A_DOCUMENT`, when the link turned out not to be a policy document at all
- `TOO_LARGE`

**What happens to the run as a whole.** The run itself ends in one of three states, and each one has
its own exit code:

| Run outcome | What it means | Exit code |
|---|---|---|
| `SUCCESS` | We got and validated every document we attempted | 0 |
| `PARTIAL` | We got at least one document, but at least one failed | 1 |
| `FAILED` | We got nothing usable, including the case where we found no candidates at all | 2 |

The exit code matters because it is the one thing a test (or a shell script, or a marker running our
program) can check without reading our output. It also means someone can use the scraper in a
pipeline and branch on whether it worked. This follows what we wrote in Phase 1 Question 9, where an
unexpected condition should print a clear message to stderr and exit cleanly, and in Question 10,
where "finished" means it runs cleanly on Ubuntu 22.04 and passes both the test suite and mypy.

**The manifest.** Each run writes one record per URL. A record holds the URL, the HTTP status, the
declared content type, how many bytes arrived, the magic bytes we actually saw, the outcome, and a
short reason in plain words. The run also records how many retries happened and how many requests it
made in total.

We put this in mostly so the tests have something solid to check. Asserting on a manifest record is
much safer than asserting on the exact wording of a log line, because log messages get reworded and
then tests break for no good reason. Diagnostics and logs go to stderr so stdout stays clean for query
results, which is what we said we wanted in Question 10.

---

## 2.2 What we expect in each scenario

All five scenarios are cases where the site is at fault, not our code. Our general position is that
the scraper should fail loudly and fail fast. Every failure gets a status and a reason, and a run that
retrieved nothing exits non-zero.

The failure we are most worried about is not a crash. It is the scraper quietly producing a result
that looks fine: an empty list of policies, or an HTML error page saved as a `.pdf` file. Those look
like success to whoever reads the output, and nobody notices until much later. Five rules came out of
that concern, and every test case below comes from one of them.

1. **Finding nothing is a failure, not a success.** If we find no candidate documents, that is an
   error, not an empty but successful run.
2. **A partial result is a real result.** One broken link should not stop the whole run. We record the
   failure against that URL and carry on with the documents that do work.
3. **Do not trust what the server says.** A download only counts as successful if the response was
   2xx, the declared content type matches the link, and the first bytes look like a known format
   (`%PDF`, `PK` for OOXML, `{\rtf`). All three, not just the first two.
4. **Put a limit on everything.** A timeout per request, a cap on retries, a cap on total requests, a
   maximum response size, and a deadline for the whole run.
5. **Be a good citizen.** Honour `Retry-After`, back off exponentially with some jitter, and stay
   inside the request cap so we are not hammering a site that is already struggling.

### Scenario 1: the site's structure changes and our scraping no longer works

**We want the scraper to notice and stop, not to guess.** Every time it loads the index page it checks
its own assumptions: the page has to parse, and our selectors have to find at least one candidate
policy document. If either check fails, the run ends as `FAILED` with the reason
`INDEX_STRUCTURE_CHANGED`. It exits non-zero, and it tells us which selectors found nothing and how
many anchors it saw on the page.

What it must not do is invent new selectors and carry on, or report success with an empty result.
Silently returning nothing is the worst outcome here, because a green run that collected no policies
looks exactly like a green run that collected all of them.

Against fixtures we can test this directly. Against the real site, the only thing that can catch a
genuine change is T011, which is why we made it a scheduled test rather than part of CI.

### Scenario 2: the HTML looks truncated

**We want the scraper to throw the response away rather than try to salvage it.** Truncated markup is
tricky because HTML parsers are deliberately forgiving. Give a lenient parser half a page and it will
close the tags for you and hand back something that looks perfectly reasonable. That half page could
easily contain a few valid download links, and we would never know we had missed the rest.

So we check the response before we extract anything from it. If the `Content-Length` header is there,
we compare it against how many bytes actually arrived. We also require the closing `</html>`, and we
reject anything empty or only whitespace. Responses bigger than our maximum size are dropped partway
through as `TOO_LARGE` instead of being buffered.

Any links we had already pulled out of a page that fails these checks get discarded. A truncated page
should not be able to contribute even a partial set of candidates.

### Scenario 3: the site, or one link, is unavailable and times out

**We want the scraper to wait a bounded amount of time and to recover when the site comes back.**
Every request gets a connect timeout and a read timeout. Failures that look temporary are retried a
fixed number of times with exponential backoff and jitter. On top of that, the whole run has a
deadline, so we cannot get stuck retrying slowly forever.

If one document times out, that URL is recorded as `TIMEOUT` and the rest of the run continues, ending
as `PARTIAL`. A slow server that is still responding should succeed if we give it enough time. A dead
endpoint should give up quickly rather than being retried all afternoon.

Recovery matters too. After a short outage the run should just work on a later attempt with nobody
intervening. That is what we want from the scheduled CI job, and it is why retries and backoff are
worth having at all.

### Scenario 4: the site or a link returns a 4xx or 5xx status

**We want the scraper to tell the difference between a bad request and a broken server, and to never
treat an error page as a document.**

| Response | What we do | Why |
|---|---|---|
| 5xx, other than 501 and 505 | Retry with backoff up to the cap, then record `HTTP_ERROR` with the status | These are usually temporary |
| 501, 505 | Do not retry, record `HTTP_ERROR` | These are permanent by definition |
| 404, 410 | Do not retry, record it as permanently unavailable | Retrying cannot bring the file back, it just adds load |
| 401, 403 | Record `ACCESS_DENIED` or `BLOCKED`, not "not found" | The fix is on our side, so retrying is pointless |
| 429 | Honour `Retry-After`, otherwise back off, and cap the attempts | The server is asking us to slow down |
| 3xx | Follow a limited number of hops, report a redirect loop rather than retry | Avoids going round in circles |

Whichever case it is, the reason we record includes the status code and the content type of the body.
That is deliberate. If a `.pdf` URL returns a body with content type `text/html`, the most likely
explanation is that we got an error page or a login page, and knowing that saves us debugging time.

### Scenario 5: a link does not return a well-formed office or PDF file

**We want the scraper to reject anything it cannot verify as a real document, and to say why.** A 200
response at a `.pdf` URL proves nothing about what we received. We apply two checks.

First, the declared type and the magic bytes. This is the first example in the brief, where a `.pdf`
link returns HTML. The content type says `text/html` and the body does not start with `%PDF`. We
record `MALFORMED_DOCUMENT`, say both of those things in the reason, and write no file, even though
the status was 200.

Second, whether the file is actually usable, not just correctly signed. This is the second example,
where the bytes start with `%PDF-` and the file is still corrupt. So for PDFs we also require the
`%%EOF` marker at the end, and for `.docx` we require the `PK` signature plus the OOXML archive
members we expect to find inside. Anything that fails these checks is recorded as
`MALFORMED_DOCUMENT` with a reason like `truncated: missing %%EOF`, and is never counted as
retrieved. We could keep the bytes for diagnosis, but they are not part of the output.

The extension on its own is never enough. This is what catches a server returning HTML with a forged
or missing content type. It also means the extension check we used in our Task 1 prototype is only a
way of shortlisting links. It was never a way of validating a download, and we should not treat it as
one.

---

## 2.3 Test cases

Levels: **U** means a unit test against a fake HTTP transport, **I** means an integration test against
a local fixture site, and **C** means a contract test against the live site that only runs in the
scheduled job.

| ID | Scenario | Level | Arrange | Act | Assert |
|----|----------|-------|---------|-----|--------|
| **T001** | S1 – structure change | I | Fixture index page returning HTTP 200, but the markup no longer contains the anchors we look for. The links are rendered by JavaScript into a `<div>` with different attributes. | Run the scraper against the fixture base URL. | The run ends `FAILED` with reason `INDEX_STRUCTURE_CHANGED` and exit code 2. Zero documents are reported as downloaded. The diagnostics name the selectors that found nothing and give the number of anchors seen. The process does not retry forever and does not die with an unhandled exception. |
| **T002** | S1 – subtle structure change | I | Fixture index page that still has anchors, but every one of them points at another host, so the candidate count is 0. | Run the scraper against the fixture base URL. | The run ends `FAILED` with reason `NO_CANDIDATE_DOCUMENTS` and a non-zero exit code. The reason makes clear that the index parsed fine but held no policy document candidates, which is different from the index failing to parse. |
| **T003** | S2 – truncated HTML | I | Fixture index cut off mid-tag with no `</html>`, and a `Content-Length` header larger than the body actually sent. The cut leaves several valid anchors above it, so a lenient parser would happily produce a usable-looking partial list. | Request the index and attempt extraction. | The response is rejected as `TRUNCATED_RESPONSE` because the declared and received lengths disagree. The extractor is either never called or its results are discarded. The log records the bytes received against the declared length. The run ends `FAILED` with an empty result set, and does not report success with a partial candidate list. |
| **T004** | S3 – site unavailable | U | Fake HTTP transport that never responds. Connect and read timeouts set to 200 ms, retry cap 2, total request cap configured. | Run the scraper against the fake transport, timing the run and counting requests. | The run ends `FAILED` with reason `TIMEOUT` and exit code 2. The elapsed time stays within (retries + 1) × timeout plus a small margin. The request count is inside the cap, and no request goes out after the run deadline. |
| **T005** | S3 – single link unavailable | I | Fixture index linking to three documents. One endpoint accepts the connection, sleeps for 10 s, then responds. Another responds successfully after 300 ms. Per-request timeout is 1 s. | Run the scraper against the fixture base URL. | The slow but working link is reported `OK`, which confirms that a generous timeout tolerates a slow server. The sleeping link is recorded `TIMEOUT`, and only that URL is affected. The other documents are still retrieved. The run ends `PARTIAL` with exit code 1 and stays inside the run deadline. |
| **T006** | S3 – outage then recovery | I | Fake transport that resets the connection for the first two index requests, then serves the index normally. Retry budget of at least three. | Run the scraper once against the fake transport. | The run finishes as `SUCCESS` with no manual intervention. The manifest shows two retries with increasing backoff delays. The total number of requests stays inside the configured cap. |
| **T007** | S4 – 5xx on the index | I | Fixture index that always returns `503 Service Unavailable`. Retry cap 3, backoff base 50 ms. | Run the scraper against the fixture base URL. | Exactly four requests go to the index, the first attempt plus three retries, and no more. The delays increase between attempts. The run ends `FAILED` with `HTTP_ERROR` and status 503, nothing is downloaded, and the exit code is 2. The failure message names the URL and the status. |
| **T008** | S4 – 404 on a document link | I | Fixture index with four document links. One returns `404`, the other three return valid minimal PDFs. | Run the scraper against the fixture base URL. | The 404 URL is recorded `HTTP_ERROR` with status 404 and is requested exactly once, because a 4xx will not improve by retrying. The other three are `OK`. The run ends `PARTIAL`, no file is written for the failed URL, and the manifest lists all four URLs with their statuses. |
| **T009** | S4 – 429 rate limiting | U | Fake transport returning `429` with `Retry-After: 2` for the first two document requests, then `200`. | Run the scraper against the fake transport, recording the delays between requests. | The `Retry-After` value is honoured and overrides the shorter default backoff. Each retry is logged along with its delay. The total number of requests stays inside the cap, and the document is eventually reported `OK`. |
| **T010** | S5 – document is not a well-formed file | I | Four fixture endpoints linked from the index: (a) `/policy/foo.pdf` returning 200 with `Content-Type: text/html` and body `<html><body>Sign in to continue</body></html>`, which is Example 1; (b) `/policy/corrupt.pdf` starting with `%PDF-` but truncated with no `%%EOF`, which is Example 2; (c) `/policy/report.docx` starting with `PK` but containing no valid OOXML members; (d) a valid control PDF. | Download all four documents. | (a) is rejected as `MALFORMED_DOCUMENT`, with the reason naming the declared content type and the missing `%PDF` magic, and no file is written. (b) is rejected as `MALFORMED_DOCUMENT` with reason `truncated: missing %%EOF`. (c) is rejected as `MALFORMED_DOCUMENT` with reason `not a valid archive`. None of the three counts as retrieved, the control PDF is `OK`, and the run ends `PARTIAL`. |
| **T011** | S1 and S5 – live contract test | C | The real policy index, accessed by the scheduled job described in Phase 1 Question 15, and kept out of the main CI workflow. | Run the scraper against the live index and validate every document it downloads. | If the site is reachable, at least one policy document candidate is found and at least one document passes validation. If this fails we report it to the team as possible site drift. It does not block merges. |

---

## 2.4 Discussion

### 2.4.1 Why the suite stops where it does

We kept the suite as small as we could while still covering the decisions that would be expensive to
get wrong. For each one we check the result and the side effects together, meaning the outcome name,
the exit code, how many requests were made, the retry delays, and whether a file was written at all.

Most of our assertions are negative. T001, T003, T004, T007 and T010 all check that something is
reported as a failure and is not counted as a success. That is deliberate, because a scraper that
reports false success is far more dangerous than one that crashes, and false success is the harder
problem to catch.

We also left things out on purpose. We test a truncated index page in T003 but not a truncated
document body, because both are caught by the same length check, and T010(b) already covers the
document case. We left out situations we could not make repeatable, like intermittent DNS failures,
rather than write tests that would be flaky and then get ignored.

### 2.4.2 What this means for how we build the scraper

These tests can only exist if the scraper is built to allow them. So the suite effectively sets some
requirements on the design:

1. **HTTP access behind a seam.** An injectable client or transport interface, so a fake can produce
   hangs, resets, status codes and `Retry-After` headers with no network involved and no real
   sleeping. This is the same approach our Phase 1 plan described with `responses` and
   `requests-mock` in Question 14.
2. **Configuration we can inject.** Base URL, timeouts, retry cap, request cap, maximum response size
   and run deadline all need to be settable, so a test can drop a timeout to 200 ms without editing
   the code.
3. **A clock we can inject.** Backoff and timeouts should be checked deterministically instead of by
   sleeping, and jitter should be seeded or switchable off so the timings come out the same each run.
4. **A structured manifest and stderr logging.** Tests check records rather than formatted text, and
   stdout stays clean for query results, which is what our definition of "finished" requires.
5. **Validation kept separate from downloading.** Magic byte, end marker and archive member checks
   should be callable on their own, so we can unit test them against byte strings.
6. **A predictable set of documents to download.** Either configurable or in a stable order, so
   assertions about "three to five documents" do not depend on dictionary ordering or on concurrency.

### 2.4.3 What this suite does not cover

Worth being honest about the gaps:

- **Redirects that land on a login page** returning 200 with HTML. T010(a) catches the HTML body, but
  the redirect handling itself is not tested here.
- **Compression and chunking.** A chunked response has no `Content-Length`, and a wrong
  `Content-Encoding` can make our lengths disagree. The truncation check needs to cope with that
  instead of rejecting every page, and that needs its own test once we have picked a client.
- **Encoding mismatches** in the index HTML. These affect how policy titles come out, not whether we
  retrieve anything, so we left them out of this suite.
- **Concurrency.** If we parallelise downloads later, then per-host politeness and the ordering of the
  manifest both need tests of their own.
- **Fixture fidelity.** Every integration test above uses a fixture we wrote ourselves, so none of
  them can detect a real change to the live site. T011 exists to cover that gap, which is exactly why
  it is scheduled rather than part of the merge-blocking workflow.

There is also one assumption the whole suite rests on, we shortlist candidate documents by file extension. T010 shows why that is only a filter and not a guarantee, and we expect the Task 4 work on adversarial inputs to go straight at it.

### 2.4.4 How this connects to the other tasks

This section says what the scraper should do and how we would know. It does not supply any of the
machinery. The fixtures and test doubles that make these cases repeatable, which means the local
fixture site, the fake transport, the injected clock and the configuration, are Task 3. We wrote the
test cases so that Task 3 can add all of that underneath without changing any of them. 
