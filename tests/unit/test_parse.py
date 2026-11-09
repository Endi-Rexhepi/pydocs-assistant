from functools import cache
from pathlib import Path

import pytest
from bs4 import BeautifulSoup, Tag

from pydocs.ingestion.html_utils import displayed_name
from pydocs.ingestion.models import DocRecord
from pydocs.ingestion.parse import _python_name, parse_page

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

# where each fixture lives in the real docs
PAGES = {
    "pathlib.html": "library/pathlib.html",
    "json.html": "library/json.html",
    "functions.html": "builtins/functions.html",
    "stdtypes.html": "builtins/stdtypes.html",
    "exceptions.html": "builtins/exceptions.html",
}


@cache  # stdtypes.html is big, parse each page once
def parsed(filename: str) -> tuple[DocRecord, ...]:
    html = (FIXTURES / filename).read_text(encoding="utf-8")
    return tuple(parse_page(html, PAGES[filename]))


def record(filename: str, name: str) -> DocRecord:
    matches = [r for r in parsed(filename) if r.name == name or name in r.aliases]
    assert len(matches) == 1, f"{len(matches)} records named {name}"
    return matches[0]


@pytest.mark.parametrize("filename", PAGES)
def test_page_parses_into_clean_records(filename: str) -> None:
    records = parsed(filename)
    assert records
    assert len({r.id for r in records}) == len(records)
    assert [r.order for r in records] == sorted(r.order for r in records)
    for r in records:
        assert "¶" not in r.text and all("¶" not in s for s in r.signatures)
        assert r.url.startswith("https://docs.python.org/3.15/")
        if r.kind != "section":
            assert r.signatures, r.id


def test_redirect_stub_gives_no_records() -> None:
    stub = "<html><body><p>You should have been redirected.</p></body></html>"
    assert parse_page(stub, "library/functions.html") == []


def test_page_starts_with_its_title_section() -> None:
    first = parsed("pathlib.html")[0]
    assert first.kind == "section"
    assert first.heading_path == ["pathlib — Object-oriented filesystem paths"]
    assert "Added in version 3.4." in first.text


def test_empty_fields_are_left_out_of_the_json() -> None:
    line = record("json.html", "json.dumps").model_dump_json(exclude_defaults=True)
    assert '"qualifiers"' not in line and '"parent"' not in line


def test_method_has_signature_link_parent_and_version_note() -> None:
    r = record("pathlib.html", "pathlib.Path.rename")
    assert r.kind == "method"
    assert r.signatures == ["Path.rename(target)"]
    assert r.parent == "library/pathlib.html#pathlib.Path"
    assert r.url == "https://docs.python.org/3.15/library/pathlib.html#pathlib.Path.rename"
    assert [(v.kind, v.version) for v in r.versions] == [("changed", "3.8")]


def test_code_examples_keep_their_line_breaks() -> None:
    text = record("pathlib.html", "pathlib.Path.rename").text
    assert "```python\n>>> p = Path('foo')\n>>> p.open('w')" in text


def test_qualifiers_are_separated_from_the_signature() -> None:
    r = record("pathlib.html", "pathlib.Path.from_uri")
    assert r.qualifiers == ["classmethod"]
    assert r.signatures == ["Path.from_uri(uri)"]
    assert ("added", "3.13") in [(v.kind, v.version) for v in r.versions]


def test_several_signatures_become_one_record() -> None:
    r = record("functions.html", "max")
    assert r.library == "builtins"
    assert r.signatures == [
        "max(iterable, /, *, key=None)",
        "max(iterable, /, *, default, key=None)",
        "max(arg1, arg2, /, *args, key=None)",
    ]


def test_nested_method_points_to_its_class() -> None:
    r = record("stdtypes.html", "dict.get")
    assert r.parent == "builtins/stdtypes.html#dict"
    assert r.signatures == ["get(key, default=None, /)"]
    assert "never raises a `KeyError`" in r.text


def test_class_text_does_not_repeat_its_methods() -> None:
    assert "Return the value for key" not in record("stdtypes.html", "dict").text


def test_exception_and_hierarchy_are_extracted() -> None:
    assert record("exceptions.html", "ValueError").kind == "exception"
    hierarchy = next(r for r in parsed("exceptions.html") if r.id.endswith("#exception-hierarchy"))
    assert "```text\nBaseException\n" in hierarchy.text


def test_command_line_options_are_api_records() -> None:
    r = record("json.html", "--sort-keys")
    assert r.kind == "option"
    assert [(v.kind, v.version) for v in r.versions] == [("added", "3.5")]


def test_module_function_keeps_full_signature() -> None:
    r = record("json.html", "json.dumps")
    assert r.module == "json"
    assert r.signatures[0].startswith("json.dumps(obj, *, skipkeys=False")


@pytest.mark.parametrize(
    ("anchor", "module", "expected"),
    [
        ("future__.annotations", "__future__", "__future__.annotations"),
        ("thread.error", "_thread", "_thread.error"),
        ("pathlib.Path.rename", "pathlib", "pathlib.Path.rename"),
    ],
)
def test_python_name_restores_leading_underscores(anchor: str, module: str, expected: str) -> None:
    assert _python_name(anchor, module) == expected


# found in the hand check: pdb's "pp expression" came out as "expressionpp"
NAME = '<span class="sig-name">{}</span>'
PRENAME = '<span class="sig-prename">{}</span>'


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (NAME.format("pp") + PRENAME.format(" expression"), "pp"),
        (NAME.format("-m") + PRENAME.format(" &lt;mode&gt;"), "-m"),
        (PRENAME.format("json.") + NAME.format("dumps"), "json.dumps"),
    ],
)
def test_displayed_name_ignores_arguments_after_the_name(html: str, expected: str) -> None:
    dt = BeautifulSoup(f"<dt>{html}</dt>", "html.parser").dt
    assert isinstance(dt, Tag)
    assert displayed_name(dt) == expected
