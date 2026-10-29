# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

A local-only tool for removing sensitive information (Israeli ID numbers, bank
accounts, credit cards, phone numbers) from PDFs — payslips, bank/credit
reports — before sharing them. FastAPI + PyMuPDF backend, dependency-free
vanilla HTML/JS/CSS frontend, listens on `127.0.0.1:8477` only. The user
processes **real personal financial documents** in this tool, not samples.

Full user-facing behavior is documented in Hebrew in [README.md](README.md) —
read it for feature details (merge flow, redaction modes, security model).
This file covers what a coding agent needs that the README doesn't.

## Commands

Run (manual, opens browser):
```bash
run.bat
```

Restart after a code change (kills only this project's process by full path,
never by process name):
```bash
scripts/restart.ps1
```

Test suites (must set `PYTHONIOENCODING=utf-8` — Hebrew test output breaks on
Windows' default cp1252):
```bash
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe tests/test_pipeline.py
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe tests/test_security.py
PYTHONIOENCODING=utf-8 venv/Scripts/python.exe tests/test_merge_api.py
```

- `test_pipeline.py` — redaction engine unit tests, no server needed.
- `test_security.py` — runs against the **live server on 8477**. If
  `auth.json` already exists it skips login tests; otherwise it creates and
  deletes a temporary password.
- `test_merge_api.py` — spins up an isolated server instance on **8478**
  with auth disabled, to test merge endpoints without touching the live
  server or the user's real password. It port-checks first and fails fast if
  8478 is occupied — a past bug had it silently attach to a stale instance
  and report PASS against old code.

There is no single-test runner — each file is a plain script executed
top-to-bottom; run the whole file.

## Architecture

**Request flow**: `static/app.js` → `app.py` (FastAPI, one process, in-memory
session dict) → `redactor.py` (PyMuPDF operations) / `detectors.py` (pure
regex+checksum, no I/O). Nothing touches disk except the final file the user
explicitly downloads.

**Sessions (`app.py` `SESSIONS` dict)**: each upload gets an in-memory session
holding the open `fitz.Document`, accumulated `candidates`/`pages`, and the
list of merged source files. Sessions expire after 30 min of inactivity
(`SESSION_TTL_SECONDS`) and are capped at `MAX_SESSIONS`, since the server
runs as a permanent background process (autostart), not a short-lived CLI.

**Merging happens on upload, not on output.** Sending header `X-Session: <id>`
with a new upload appends that file's pages to the existing session's
document via `redactor.merge_into()`, and `analyze(start_page=...)` only
scans the newly added pages — already-analyzed pages aren't rescanned. This
is why `sources` in a session is an ordered list of
`{name, start, pages}` rather than the merge being a separate step.

**Merging is deduplicated by page content, not by file bytes.**
`redactor.page_signature()` hashes page dimensions + full extracted text +
the raw streams of embedded images; `merge_into()` skips any incoming page
whose signature is already in the session (`sess["page_sigs"]`). A file hash
would miss the common case — the same document re-saved has completely
different bytes. Two deliberate exceptions: a page with neither text nor
images gets signature `None` and is never treated as a duplicate (otherwise
blank separator pages would vanish), and duplicates *within* a single
uploaded file are not filtered — only pages already in the session are.
When every page of an upload is a duplicate the session is left completely
untouched: no `sources` entry, no re-analysis, `bytes` not rebuilt. The
response carries `added` and `skipped_pages` (1-based, within the uploaded
file) so the client can say what happened.

**Search terms are re-run after a merge.** `state.queries` in `app.js` holds
the active search terms; appending a document replays each one so that
"remove every occurrence of X" also covers the newly added pages. Without
this, `/redact` reported a `term_leak` for a term the user had already asked
to remove. `runSearch()` dedupes by page+rects+query, so replaying a term — or
a user searching it twice — never marks the same spot twice.

**Upload bypasses `UploadFile` on purpose.** FastAPI's `UploadFile` uses
`SpooledTemporaryFile`, which spills to disk above 1MB — a direct violation
of "nothing touches disk." `/upload` reads `request.stream()` chunk-by-chunk
into memory instead. Don't reintroduce `UploadFile` for this route.

**Redaction is real deletion, not a visual overlay.** `apply_redactions()` in
`redactor.py` uses PyMuPDF's `add_redact_annot` + `apply_redactions`, which
strips content from the PDF's content stream — not a black box drawn on top
(a real information-leak pattern that `test_pipeline.py` explicitly regression
-tests against). `black_boxes=True` only changes the fill color drawn *after*
the underlying content is already gone; it is cosmetic, not a security
control. Default is `fill=False` (blank area).

**Verification checks regions, not document-wide value search.** `verify()`
in `redactor.py` re-opens the *output* file and confirms each marked
rectangle is empty of text, and separately checks that any explicit search
terms don't appear anywhere in the final document. It deliberately does NOT
do a document-wide "same digits anywhere" scan — an earlier version did, and
produced false positives (e.g. a bank account number that was a substring of
an ID the user chose to keep, or a date `07/2026` matching digits elsewhere).
Do not reintroduce a digit-substring heuristic here.

**Auth (`auth.py`) is deliberately memoryless of the password.** Only a
PBKDF2-HMAC-SHA256 hash (600k iterations, random salt) is persisted, in
`%LOCALAPPDATA%\PdfRedactor\auth.json` — outside the project directory, not
in git. Session tokens live only in the in-memory `_TOKENS` dict and die on
restart. `security_gate` middleware in `app.py` enforces auth on every route
not in `PUBLIC_PATHS`, plus Host/Origin checks (DNS-rebinding/CSRF), CSP, and
`Cache-Control: no-store` (without which the browser would disk-cache the
rendered page images — i.e. full document content).

**Detectors (`detectors.py`) are pure functions**: given
`page.get_text("words")` tuples, they return `Candidate` objects with
bounding boxes. `normalize()` strips bidi control characters and normalizes
the many Unicode dash/space variants that show up in Hebrew PDFs before any
regex runs — text from Hebrew PDF extraction is not already normalized.
Multi-word matching (`detect_in_words`'s second loop) exists because credit
card numbers are sometimes split across separate word tokens on the same
line (`block`/`line` index must match, not just visual position).

## Critical operational constraints

**Process isolation**: this machine also runs MoneySaver (node :3000, plus a
`python.exe` ML service on :8300), BOTI (node :3001), and WebtopBot. Never
kill by process name — `scripts/stop-pdfredactor.ps1` matches on full project
path in the command line, with a second safety layer that explicitly skips
any process path containing `MONEY SAVER`, `BOTI`, or `WEBTOP`. After any
script that stops processes, run `scripts/check-others.ps1` to confirm ports
3000/3001/8300 are still listening.

**File encodings** (two real past failures):
- `.bat` files must stay ASCII-only — `cmd.exe` breaks on Hebrew and throws
  parse errors.
- `.ps1` files with Hebrew content must be saved UTF-8 **with BOM** —
  PowerShell 5.1 reads BOM-less UTF-8 as ANSI and fails to parse it.

**Testing the UI in a browser**: the live server requires the user's real
password, which Codex does not have. UI testing runs against an isolated
instance on port 8478 with `auth.token_valid` patched out and `appmod.PORT`
set to 8478 (the Origin check derives from it too), plus a `/testfile/{name}`
route serving the sample PDFs in `tests/`. Never paste base64 PDF content by
hand into JS to feed such a test — it has silently corrupted bytes before
(`Israel` → `Ùsrael`) and produced a false diagnosis. Extract text with
`fitz` to verify redaction, not raw byte/string inspection — output content
is Flate-compressed, so raw byte checks prove nothing.
