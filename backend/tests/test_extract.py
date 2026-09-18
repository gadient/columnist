"""The input boundary.

Every assertion here is about something failing **before** a model is called. That ordering is the
requirement, not an implementation detail: a rejection that happens after inference has already
cost money and already shown the note to a third party.

The fixtures are authored artifacts — you cannot download a DOCX containing exactly one
tracked change, or a `.docx` that is secretly ASCII.
"""
from __future__ import annotations

import io
import zipfile

import pytest
from app.note_imports.errors import ImportError as ImportRejection
from app.note_imports.extract import extract_docx, extract_pasted, extract_txt


def code_of(exc_info) -> str:
    return exc_info.value.detail["code"]


def _docx_zip(parts: dict[str, bytes]) -> bytes:
    """A DOCX-shaped zip (PK magic, has word/document.xml) built in memory, for crafting the
    adversarial containers you can't author as fixtures — decompression bombs, huge entry counts."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    return buf.getvalue()


# --- decompression-bomb defence -----------------------------------------------------------------

def test_a_high_compression_ratio_docx_is_rejected():
    """A small zip whose document.xml expands enormously (3 MB of one byte → a few KB compressed,
    ratio ~1000:1) is a decompression bomb — rejected before it's fully decompressed."""
    raw = _docx_zip({"word/document.xml": b"A" * (3 * 1024 * 1024)})
    with pytest.raises(ImportRejection) as e:
        extract_docx(raw)
    assert code_of(e) == "DOCX_INVALID"


def test_a_docx_with_too_many_entries_is_rejected():
    parts = {"word/document.xml": b"<x/>"}
    for i in range(2100):  # over _MAX_ZIP_ENTRIES (2048)
        parts[f"junk/{i}.bin"] = b"x"
    raw = _docx_zip(parts)
    with pytest.raises(ImportRejection) as e:
        extract_docx(raw)
    assert code_of(e) == "DOCX_INVALID"


# --- TXT ----------------------------------------------------------------------------------------

def test_valid_utf8_reads(fx):
    ex = extract_txt(fx("utf8.txt"))
    assert ex.text.strip()
    assert ex.segments


def test_bom_is_stripped_not_rejected(fx):
    """A BOM is UTF-8 wearing a hat. Rejecting it would fail files Notepad produces by default."""
    ex = extract_txt(fx("bom.txt"))
    assert not ex.text.startswith("﻿")
    assert ex.text.strip()


@pytest.mark.parametrize("name", ["latin1.txt", "utf16.txt"])
def test_non_utf8_is_rejected_not_guessed(fx, name):
    """Never guess an encoding. A wrong guess is worse than a clear refusal — it
    silently corrupts a note the user believes was read correctly."""
    with pytest.raises(ImportRejection) as e:
        extract_txt(fx(name))
    assert code_of(e) == "UNSUPPORTED_ENCODING"


def test_empty_input_is_rejected():
    with pytest.raises(ImportRejection) as e:
        extract_txt(b"")
    assert code_of(e) == "EMPTY_INPUT"


def test_whitespace_only_input_is_rejected():
    with pytest.raises(ImportRejection) as e:
        extract_pasted("   \n\n\t  ")
    assert code_of(e) == "EMPTY_INPUT"


def test_segments_carry_locators(fx):
    """The locator is what makes evidence checkable — the model cites one, and resolution verifies
    the excerpt against it. Without locators, evidence is just prose that claims to be a quote."""
    ex = extract_txt(fx("utf8.txt"))
    assert all(s.locator for s in ex.segments)


# --- DOCX ---------------------------------------------------------------------------------------

def test_valid_docx_reads(fx):
    ex = extract_docx(fx("valid.docx"))
    assert ex.text.strip()
    assert ex.segments


def test_spoofed_extension_is_caught(fx):
    """`spoofed.docx` is ASCII text with a lying extension. The container is inspected; the
    filename is a hint, never evidence."""
    with pytest.raises(ImportRejection) as e:
        extract_docx(fx("spoofed.docx"))
    assert code_of(e) == "DOCX_INVALID"


@pytest.mark.parametrize("name", ["legacy.doc", "encrypted.docx"])
def test_ole2_containers_are_caught_by_magic_bytes(fx, name):
    """Old `.doc` and encrypted DOCX are both OLE2, not zip. Detected by content — an encrypted
    file is *named* `.docx` and is not one."""
    with pytest.raises(ImportRejection) as e:
        extract_docx(fx(name))
    assert code_of(e) == "DOCX_INVALID"


def test_corrupt_container_is_caught(fx):
    with pytest.raises(ImportRejection) as e:
        extract_docx(fx("corrupt.docx"))
    assert code_of(e) == "DOCX_INVALID"


def test_tracked_changes_are_rejected(fx):
    """An unresolved tracked change (`w:ins`/`w:del`) leaves unaccepted revision text in the
    XML: a deleted sentence would be extracted as though it were still in the document, or a
    proposed insertion as though it were settled, and a card would cite it as evidence. Refusing
    beats reading a note that says the opposite of what its author meant."""
    with pytest.raises(ImportRejection) as e:
        extract_docx(fx("tracked.docx"))
    assert code_of(e) == "DOCX_TRACKED_CHANGES"


def test_docx_with_no_body_text_is_rejected(fx):
    with pytest.raises(ImportRejection) as e:
        extract_docx(fx("empty.docx"))
    assert code_of(e) == "NO_EXTRACTABLE_TEXT"


def test_macro_enabled_documents_are_rejected_as_a_type(fx):
    """A `.docm` — or a `.docm` wearing a `.docx` name, which is what this fixture is.

    Rejected as `UNSUPPORTED_FILE_TYPE`, and the distinction matters: this is a **type** boundary,
    not a security one. Nothing here would execute a macro (we read XML). The product accepts TXT
    and DOCX; `.docm` is neither, and the user gets told to save it as plain `.docx`. Detected by
    a `word/vbaProject` part, so renaming the file doesn't get you past it.
    """
    with pytest.raises(ImportRejection) as e:
        extract_docx(fx("macro.docx"))
    assert code_of(e) == "UNSUPPORTED_FILE_TYPE"


def test_the_size_limit_is_one_megabyte():
    """The limit lives here; **enforcement is at the API boundary** (`api.py`), before the
    reader is ever called — a huge file must cost a length check, not a decode.

    So this asserts the constant, not the behaviour. The behaviour is asserted at the API level by
    `test_api.py::test_an_oversized_note_is_rejected_before_the_model`.
    """
    from app.note_imports.extract import MAX_RAW_BYTES

    assert MAX_RAW_BYTES == 1024 * 1024


# --- corpus inputs ---------------------------------------------------------------------------------

def test_every_committed_note_fixture_reads_except_the_corrupt_one():
    """The suite in `agent_test_suite/` is the corpus foundation; a file the reader can't open is
    a file the corpus can't score. `text2.txt` is the deliberate exception — see that README."""
    from pathlib import Path

    suite = Path(__file__).resolve().parents[2] / "agent_test_suite"
    if not suite.is_dir():  # not checked out in this tree
        pytest.skip("agent_test_suite/ not present")

    rejected = []
    for path in sorted(suite.iterdir()):
        if path.suffix not in (".txt", ".docx"):
            continue
        raw = path.read_bytes()
        try:
            (extract_docx if path.suffix == ".docx" else extract_txt)(raw)
        except ImportRejection:
            rejected.append(path.name)

    assert rejected == ["text2.txt"], f"unexpected rejections: {rejected}"
