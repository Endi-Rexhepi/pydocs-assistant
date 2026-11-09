"""Parse the downloaded HTML docs into records.

Each page becomes a list of records in reading order: one per section (its own
text only) and one per documented object (function, class, method, option...).
The text itself is built in render.py.

Writes data/processed/records.jsonl and manifest.json.

    uv run python -m pydocs.ingestion.parse
"""

import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from pydocs.ingestion.download import DOCS_VERSION, RAW_DIR
from pydocs.ingestion.html_utils import (
    HEADINGS,
    attr,
    child_tags,
    classes,
    displayed_name,
    heading_text,
    qualifiers,
    signature_text,
)
from pydocs.ingestion.models import DocRecord
from pydocs.ingestion.render import Rendered, render

PROCESSED_DIR = RAW_DIR.parent / "processed"
DOCS_FOLDERS = ("library", "builtins")  # builtins/ only exists since 3.15


@dataclass
class _PageContext:
    """State shared while walking one page."""

    page: str  # e.g. "library/pathlib.html"
    library: str
    version: str
    records: list[DocRecord] = field(default_factory=list)
    anchors: dict[str, list[str]] = field(default_factory=dict)  # record id -> html ids
    order: int = 0

    def next_order(self) -> int:
        self.order += 1
        return self.order

    def unique_id(self, base: str) -> str:
        # a few pages document the same name twice, so add ~2, ~3...
        candidate, n = base, 2
        while candidate in self.anchors:
            candidate, n = f"{base}~{n}", n + 1
        self.anchors[candidate] = []
        return candidate

    def url(self, anchor: str | None) -> str:
        base = f"https://docs.python.org/{self.version}/{self.page}"
        return f"{base}#{anchor}" if anchor else base


def _unique(items: Iterable[str]) -> list[str]:
    """Drop empty strings and duplicates, keep the order."""
    return list(dict.fromkeys(item for item in items if item))


def _section_title(section: Tag) -> str:
    for child in child_tags(section):
        if child.name in HEADINGS:
            return heading_text(child)
    return ""


def _declared_module(tag: Tag) -> str | None:
    # Sphinx marks a module with id="module-<name>", either on the <section> or on
    # a span/div/p right after the title. Careful: a heading like "Module Contents"
    # also gets id="module-contents", so on a section we check the title too.
    tag_id = attr(tag, "id") or ""
    if not tag_id.startswith("module-"):
        return None
    name = tag_id.removeprefix("module-")
    if tag.name != "section":
        return name
    if _section_title(tag).lower().startswith(name.lower()):
        return name
    return None


def _module_of(section: Tag) -> str | None:
    # check the section itself and its direct children (not sub-sections)
    candidates = [section, *(c for c in child_tags(section) if c.name != "section")]
    for tag in candidates:
        module = _declared_module(tag)
        if module:
            return module
    return None


def _python_name(anchor: str, module: str | None) -> str:
    # Sphinx strips leading underscores from ids:
    # __future__.annotations -> "future__.annotations", _thread.error -> "thread.error"
    if module and module.startswith("_"):
        stripped = module.lstrip("_")
        if anchor == stripped or anchor.startswith(stripped + "."):
            return module + anchor[len(stripped) :]
    return anchor


def _walk_section(
    section: Tag, heading_path: list[str], module: str | None, ctx: _PageContext
) -> None:
    title = _section_title(section)
    path = [*heading_path, title] if title else heading_path
    module = _module_of(section) or module
    anchor = attr(section, "id")
    order = ctx.next_order()  # taken before rendering, so the section comes before its objects

    def on_api(dl: Tag, kind: str) -> None:
        _walk_api(dl, kind, path, module, anchor, None, ctx)

    rendered = render(section, on_api)
    if rendered.text:
        ctx.records.append(
            DocRecord(
                id=ctx.unique_id(f"{ctx.page}#{anchor or title}"),
                kind="section",
                name=title,
                library=ctx.library,
                module=module,
                heading_path=path,
                text=rendered.text,
                versions=rendered.version_notes,
                url=ctx.url(anchor),
                order=order,
            )
        )
    for sub in child_tags(section, "section"):
        _walk_section(sub, path, module, ctx)


def _walk_api(
    dl: Tag,
    kind: str,
    heading_path: list[str],
    module: str | None,
    section_anchor: str | None,
    parent: str | None,
    ctx: _PageContext,
) -> None:
    dts = child_tags(dl, "dt")  # one per signature
    dd = next(iter(child_tags(dl, "dd")), None)  # the description
    if not dts:
        return

    # Several <dt> can share one <dd>: overloads like max(), or grouped names like
    # decimal.Context.Emin/Emax. We keep them as one record.
    anchors = _unique(attr(dt, "id") or "" for dt in dts)
    anchor = anchors[0] if anchors else None
    is_python = "py" in classes(dl)
    if anchor and is_python:
        name = _python_name(anchor, module)
    else:
        name = displayed_name(dts[0])  # options, opcodes...: "--sort-keys", "NOP"
    aliases = [_python_name(a, module) if is_python else a for a in anchors[1:]]

    # no anchor (rare): fall back to section + name
    local_id = anchor or f"{section_anchor or 'page'}:{name}"
    record_id = ctx.unique_id(f"{ctx.page}#{local_id}")
    ctx.anchors[record_id] = anchors
    order = ctx.next_order()

    def on_api(child: Tag, child_kind: str) -> None:
        # an object inside this one, e.g. a method inside a class
        _walk_api(child, child_kind, heading_path, module, section_anchor, record_id, ctx)

    rendered = render(dd, on_api) if dd is not None else Rendered()
    ctx.records.append(
        DocRecord(
            id=record_id,
            kind=kind,
            name=name,
            signatures=_unique(signature_text(dt) for dt in dts),
            qualifiers=_unique(q for dt in dts for q in qualifiers(dt, kind)),
            aliases=aliases,
            parent=parent,
            library=ctx.library,
            module=module,
            heading_path=heading_path,
            text=rendered.text,
            versions=rendered.version_notes,
            url=ctx.url(anchor or section_anchor),
            order=order,
        )
    )


def _link_parents(records: list[DocRecord], anchors: dict[str, list[str]]) -> None:
    # Some pages (pathlib) document methods next to their class, not inside it.
    # The anchor still tells us the class: "pathlib.Path.rename" -> "pathlib.Path".
    by_anchor = {a: record_id for record_id, ids in anchors.items() for a in ids}
    for record in records:
        own = anchors.get(record.id, [])
        if record.kind == "section" or record.parent or not own:
            continue
        prefix = own[0].rpartition(".")[0]
        if prefix in by_anchor:
            record.parent = by_anchor[prefix]


def _library_for(page: str, main: Tag) -> str:
    # What the UI filters on. Order of preference: builtins/ folder, the module the
    # page declares, the most common object prefix (asyncio-task.html has no module
    # but its objects are asyncio.*), and last the file name.
    folder, _, filename = page.partition("/")
    if folder == "builtins":
        return "builtins"
    for tag in main.find_all(id=re.compile(r"^module-")):
        module = _declared_module(tag) if isinstance(tag, Tag) else None
        if module:
            return module
    prefixes: Counter[str] = Counter()
    for dl in main.find_all("dl", class_="py"):
        if isinstance(dl, Tag):
            for dt in child_tags(dl, "dt"):
                dt_id = attr(dt, "id")
                if dt_id and "." in dt_id:
                    prefixes[dt_id.split(".")[0]] += 1
    if prefixes:
        return prefixes.most_common(1)[0][0]
    return Path(filename).stem.split("-")[0]  # "asyncio-dev.html" -> "asyncio"


def parse_page(html: str, page: str, version: str = DOCS_VERSION) -> list[DocRecord]:
    """Parse one page, e.g. page="library/pathlib.html". Redirect stubs give []."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("div", attrs={"role": "main"})
    if not isinstance(main, Tag):
        return []
    sections = child_tags(main, "section")
    if not sections:
        return []
    ctx = _PageContext(page=page, library=_library_for(page, main), version=version)
    for section in sections:
        _walk_section(section, [], None, ctx)
    records = sorted(ctx.records, key=lambda r: r.order)
    _link_parents(records, ctx.anchors)
    return records


def iter_pages(docs_dir: Path) -> Iterator[Path]:
    for folder in DOCS_FOLDERS:
        yield from sorted((docs_dir / folder).glob("*.html"))


def parse_docs(docs_dir: Path, version: str = DOCS_VERSION) -> Iterator[DocRecord]:
    for path in iter_pages(docs_dir):
        page = path.relative_to(docs_dir).as_posix()
        yield from parse_page(path.read_text(encoding="utf-8"), page, version)


def write_jsonl(records: Iterable[DocRecord], out_path: Path) -> Counter[str]:
    """Write one record per line (empty fields left out). Returns a count per kind."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kinds: Counter[str] = Counter()
    with out_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json(exclude_defaults=True) + "\n")
            kinds[record.kind] += 1
    return kinds


def main() -> None:
    docs_dir = RAW_DIR / f"python-{DOCS_VERSION}-docs-html"
    out_path = PROCESSED_DIR / "records.jsonl"
    kinds = write_jsonl(parse_docs(docs_dir), out_path)
    manifest = {
        "python_version": DOCS_VERSION,
        "source": docs_dir.name,
        "records": sum(kinds.values()),
        "kinds": dict(kinds.most_common()),
    }
    (PROCESSED_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {manifest['records']} records to {out_path}")


if __name__ == "__main__":
    main()
