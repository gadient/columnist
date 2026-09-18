# Tests

```bash
bash scripts/test.sh              # everything
bash scripts/test.sh -k schema    # one file's worth
```

`scripts/test.sh` installs pytest into the backend venv if it's missing. pytest and httpx are
**dev-only** — they are not in `requirements.txt`, because the product doesn't need them to run.

## What's here, and what deliberately isn't

These are **fast, deterministic, free**. Nothing here calls a model. That is a boundary, not an
oversight:

- **Everything a model does is graded by the corpus** — a separate,
  scored, paid run over `agent_test_suite/`. Quality is a distribution, not an assertion.
- **Everything a model *cannot* do is tested here** — because that is structural, and structure is
  exactly what a unit test is for. `test_schema.py` asserting the model has no column field is the
  test that column-free extraction is real rather than aspirational.

Put another way: these tests can't tell you the agent is *good*. They tell you it is *bounded*.

## Files

A partial guide — the note-import input and contract tests. The other `test_*.py` files each open
with a docstring saying what they cover.

| File | Covers |
|---|---|
| `test_extract.py` | The input boundary. Every rejection lands before inference, so bad input costs nothing. |
| `test_schema.py` | The contract. What the model may say, and (mostly) what it may not. |
| `test_prompt.py` | The prompt is provider-free and column-free. |
| `test_errors.py` | The error catalog, and "every error names a next action" enforced structurally. |

## Fixtures

`fixtures/` are authored artifacts — you cannot download a DOCX with exactly one
tracked change. Several are hand-built byte-wise:

| Fixture | Is |
|---|---|
| `valid.docx` / `utf8.txt` | The happy paths. |
| `spoofed.docx` | **ASCII text named `.docx`.** Extension lies; content is checked. |
| `legacy.doc` / `encrypted.docx` | OLE2 containers (old `.doc`, and encrypted DOCX). Detected by magic bytes, not extension. |
| `corrupt.docx` | A broken zip. |
| `tracked.docx` | One unresolved tracked change (`w:ins`/`w:del`) — rejected, because unaccepted revision text is present in the XML and would be extracted as if it were settled document text. |
| `macro.docx` | Macro-enabled (a `word/vbaProject` part) — rejected as `UNSUPPORTED_FILE_TYPE`. A type boundary, not a security one: nothing here executes a macro. |
| `empty.docx` | Valid container, no body text. |
| `latin1.txt` / `utf16.txt` | Not UTF-8. Rejected rather than guessed at. |
| `bom.txt` | UTF-8 with a BOM — accepted, BOM stripped. |
