"""The streaming array scanner.

The scanner's whole risk is mistaking a brace inside a string for structure, or splitting an element
at a chunk boundary. So the tests feed the *same* document at every chunk size — one shot, and one
character at a time — and assert the elements come out identical and re-parse to the original
objects. If depth-tracking or string-state is wrong, a chunked feed and a whole feed disagree.
"""
from __future__ import annotations

import json

import pytest
from app.note_imports.stream_parse import RecommendationStreamScanner

# Deliberately nasty: unescaped-looking braces inside titles, an escaped quote inside an excerpt,
# nested objects (priority, evidence) and nested arrays (evidence, labels) inside each element.
DOC = json.dumps(
    {
        "recommendations": [
            {
                "action": "create_card",
                "title": "Ship the {beta} release",
                "labels": ["a", "b"],
                "evidence": [{"excerpt": 'She said "do {this} now"', "locator": "line:1"}],
                "priority": {"value": "high", "provenance": "explicit"},
            },
            {
                "action": "create_card",
                "title": "Second } card {",
                "evidence": [],
                "priority": {"value": "medium"},
            },
        ]
    }
)

EXPECTED = json.loads(DOC)["recommendations"]


def feed_in_chunks(text: str, size: int) -> list[str]:
    scanner = RecommendationStreamScanner()
    out: list[str] = []
    for i in range(0, len(text), size):
        out.extend(scanner.feed(text[i : i + size]))
    return out


def test_whole_document_yields_every_element():
    got = feed_in_chunks(DOC, len(DOC))
    assert [json.loads(g) for g in got] == EXPECTED


def test_one_character_at_a_time_is_identical():
    """The boundary case: every char its own delta. If string- or depth-state leaks across feed
    calls, this disagrees with the whole-document feed."""
    got = feed_in_chunks(DOC, 1)
    assert [json.loads(g) for g in got] == EXPECTED


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 13, 29, 50])
def test_every_chunk_size_agrees(size):
    got = feed_in_chunks(DOC, size)
    assert [json.loads(g) for g in got] == EXPECTED


def test_braces_inside_strings_do_not_split_an_element():
    """The `{beta}` in a title and `{this}` in a quoted excerpt must not be read as object
    boundaries — the first element spans all of them and closes only at its real brace."""
    got = feed_in_chunks(DOC, 1)
    assert len(got) == 2
    first = json.loads(got[0])
    assert first["title"] == "Ship the {beta} release"
    assert first["evidence"][0]["excerpt"] == 'She said "do {this} now"'


def test_an_empty_array_yields_nothing():
    assert feed_in_chunks('{"recommendations": []}', 1) == []


def test_elements_are_emitted_as_they_close_not_all_at_the_end():
    """The point of streaming: the first element is available before the second is written. Feeding
    up to just past the first element's closing brace must already yield it."""
    scanner = RecommendationStreamScanner()
    first_close = DOC.index("}", DOC.index("priority")) + 1  # end of the first element's priority obj
    # feed up to the end of the first element (its closing brace comes right after priority closes)
    cut = DOC.index("},", first_close) + 1
    emitted = scanner.feed(DOC[:cut])
    assert len(emitted) == 1
    assert json.loads(emitted[0])["title"] == "Ship the {beta} release"
