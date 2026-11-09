"""Turn a chunk of docs HTML (a section or an object's description) into text.

Output is markdown-ish: paragraphs, "- " lists, ``` code, "|" tables, "Note: ..."
and the "Changed in version ..." lines, which are also collected as data.

API objects are not rendered here. They go to the on_api callback so parse.py can
make them their own records. Sub-sections are skipped too, parse.py walks them.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from bs4 import Comment, Tag
from bs4.element import NavigableString, PageElement

from pydocs.ingestion.html_utils import (
    BLOCK_TAGS,
    HEADINGS,
    api_kind,
    child_tags,
    classes,
    clean,
    code_block,
    inline_text,
    is_skipped,
    signature_text,
)
from pydocs.ingestion.models import VersionNote, VersionNoteKind

VERSION_NOTE_KINDS: dict[str, VersionNoteKind] = {
    "versionadded": "added",
    "versionchanged": "changed",
    "deprecated": "deprecated",
    "deprecated-removed": "deprecated",
    "versionremoved": "removed",
}
VERSION_RE = re.compile(r"version (\d+(?:\.\d+)+)")
REMOVED_RE = re.compile(r"removed in version (\d+(?:\.\d+)+)")

OnApi = Callable[[Tag, str], None]  # called with the <dl> and its kind


@dataclass
class Rendered:
    blocks: list[str] = field(default_factory=list)
    version_notes: list[VersionNote] = field(default_factory=list)
    api_names: list[str] = field(default_factory=list)  # objects we handed to on_api

    @property
    def text(self) -> str:
        return "\n\n".join(b for b in self.blocks if b.strip())


def render(container: Tag, on_api: OnApi) -> Rendered:
    renderer = _Renderer(on_api)
    renderer.children(container)
    renderer.flush()
    return renderer.out


def _version_note(kind: VersionNoteKind, first_sentence: str) -> VersionNote:
    # "Deprecated since version 3.12, removed in version 3.14: ..."
    versions = VERSION_RE.findall(first_sentence)
    removed = REMOVED_RE.search(first_sentence)
    return VersionNote(
        kind=kind,
        version=versions[0] if versions else "",
        removed_in=removed.group(1) if removed else None,
    )


class _Renderer:
    def __init__(self, on_api: OnApi) -> None:
        self.on_api = on_api
        self.out = Rendered()
        self._inline: list[str] = []  # loose text, becomes a paragraph on flush()

    def flush(self) -> None:
        text = clean("".join(self._inline))
        if text:
            self.out.blocks.append(text)
        self._inline = []

    def block(self, text: str) -> None:
        self.flush()
        if text.strip():
            self.out.blocks.append(text)

    def children(self, tag: Tag) -> None:
        for child in tag.children:
            self.node(child)

    def sub_render(self, tag: Tag, skip: Tag | None = None) -> Rendered:
        # Render a list item / note / cell on its own so we can format it, but keep
        # its version notes. Objects inside still go to the same on_api.
        sub = _Renderer(self.on_api)
        for child in tag.children:
            if child is not skip:
                sub.node(child)
        sub.flush()
        self.out.version_notes.extend(sub.out.version_notes)
        return sub.out

    def node(self, node: PageElement) -> None:
        if isinstance(node, Comment):
            return
        if isinstance(node, NavigableString):
            self._inline.append(str(node))
            return
        if not isinstance(node, Tag) or is_skipped(node):
            return

        name, tag_classes = node.name, classes(node)
        kind = api_kind(node)

        if kind is not None:
            self.api_object(node, kind)
        elif name == "section" or name in HEADINGS:
            self.flush()
        elif name == "p" and node.find(BLOCK_TAGS) is not None:
            # a few pages put a version box or a list inside a <p>
            self.flush()
            self.children(node)
            self.flush()
        elif name == "p":
            self.block(clean(inline_text(node)))
        elif name == "pre" or any(c.startswith("highlight-") for c in tag_classes):
            self.code(node)
        elif any(c in VERSION_NOTE_KINDS for c in tag_classes):
            self.version_note(node, tag_classes)
        elif "admonition" in tag_classes or any(c.startswith("admonition-") for c in tag_classes):
            self.admonition(node)
        elif name in {"ul", "ol"}:
            self.bullet_list(node)
        elif name == "table":
            self.table(node)
        elif name == "dl":
            self.definition_list(node)
        elif name == "aside" and "footnote" in tag_classes:
            self.footnote(node)
        elif name in {"div", "blockquote", "aside", "figure", "figcaption", "dd"}:
            self.flush()
            self.children(node)
            self.flush()
        else:
            self._inline.append(inline_text(node))

    def api_object(self, node: Tag, kind: str) -> None:
        self.flush()
        first_dt = node.find("dt")
        if isinstance(first_dt, Tag):
            self.out.api_names.append(signature_text(first_dt))
        self.on_api(node, kind)

    def code(self, node: Tag) -> None:
        fenced = code_block(node)
        if fenced is not None:
            self.block(fenced)

    def version_note(self, node: Tag, tag_classes: list[str]) -> None:
        # Version boxes aren't always one sentence. Some have code, lists or even
        # whole objects inside, so render them fully.
        kind = next(VERSION_NOTE_KINDS[c] for c in tag_classes if c in VERSION_NOTE_KINDS)
        body = self.sub_render(node)
        if not body.blocks:
            return
        self.out.version_notes.append(_version_note(kind, body.blocks[0]))
        for text in body.blocks:
            self.block(text)

    def admonition(self, node: Tag) -> None:
        # Note / Warning / See also boxes -> "Note: ..."
        title_tag = node.find(class_="admonition-title")
        title = clean(title_tag.get_text()) if isinstance(title_tag, Tag) else "Note"
        skip = title_tag if isinstance(title_tag, Tag) else None
        body = self.sub_render(node, skip=skip).blocks
        if body:
            self.block(f"{title.rstrip(':')}: {body[0]}")
            for text in body[1:]:
                self.block(text)

    def bullet_list(self, node: Tag) -> None:
        ordered = node.name == "ol"
        lines: list[str] = []
        for i, item in enumerate(child_tags(node, "li"), start=1):
            blocks = self.sub_render(item).blocks
            if not blocks:
                continue
            first, *rest = blocks
            lines.append(f"{i}. {first}" if ordered else f"- {first}")
            lines.extend("  " + line for text in rest for line in text.splitlines())
        self.block("\n".join(lines))

    def table(self, node: Tag) -> None:
        rows: list[list[str]] = []
        header_rows = 0
        for tr in node.find_all("tr"):
            if not isinstance(tr, Tag):
                continue
            cells = [c for c in tr.children if isinstance(c, Tag) and c.name in {"td", "th"}]
            rows.append([self.cell_text(c) for c in cells])
            if tr.parent is not None and tr.parent.name == "thead":
                header_rows += 1
        if not rows:
            return
        lines = ["| " + " | ".join(r) + " |" for r in rows]
        if header_rows:
            width = max(len(r) for r in rows)
            lines.insert(header_rows, "| " + " | ".join(["---"] * width) + " |")
        self.block("\n".join(lines))

    def cell_text(self, cell: Tag) -> str:
        # __future__ has objects inside table cells. They get their own records,
        # so the cell just shows their names.
        rendered = self.sub_render(cell)
        text = rendered.text or ", ".join(rendered.api_names)
        return text.replace("\n", " ").replace("|", "\\|")

    def definition_list(self, node: Tag) -> None:
        # plain <dl> (field lists etc.) -> "term: definition"
        term = ""
        for child in child_tags(node):
            if child.name == "dt":
                term = clean(inline_text(child))
            elif child.name == "dd":
                blocks = self.sub_render(child).blocks
                if not blocks:
                    continue
                self.block(f"{term}: {blocks[0]}" if term else blocks[0])
                for text in blocks[1:]:
                    self.block(text)
                term = ""

    def footnote(self, node: Tag) -> None:
        label_tag = node.find(class_="label")
        label = clean(label_tag.get_text()).strip("[]") if isinstance(label_tag, Tag) else ""
        skip = label_tag if isinstance(label_tag, Tag) else None
        body = self.sub_render(node, skip=skip).blocks
        if body:
            self.block(f"[{label}] {body[0]}" if label else body[0])
            for text in body[1:]:
                self.block(text)
