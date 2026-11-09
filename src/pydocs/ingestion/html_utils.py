"""Helpers for reading single elements of the Sphinx HTML."""

import re

from bs4 import Comment, Tag
from bs4.element import NavigableString, PageElement

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_TAGS = ["div", "pre", "ul", "ol", "table", "dl", "blockquote", "aside"]
SKIPPED_TAGS = {"script", "style", "hr", "img", "colgroup"}
SKIPPED_CLASSES = {"headerlink", "toctree-wrapper"}  # the ¶ links and page menus
CODE_LANGUAGE_ALIASES = {"python3": "python", "default": "python", "none": "text"}
WHITESPACE_RE = re.compile(r"\s+")


# bs4 returns attributes as a str or a list (class is a list), so wrap both cases
def classes(tag: Tag) -> list[str]:
    value = tag.get("class")
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    return list(value)


def attr(tag: Tag, name: str) -> str | None:
    value = tag.get(name)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return " ".join(value)


def child_tags(tag: Tag, name: str | None = None) -> list[Tag]:
    return [c for c in tag.children if isinstance(c, Tag) and (name is None or c.name == name)]


def clean(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def is_skipped(tag: Tag) -> bool:
    return tag.name in SKIPPED_TAGS or bool(SKIPPED_CLASSES.intersection(classes(tag)))


def api_kind(tag: Tag) -> str | None:
    """'method' for <dl class="py method">, 'option' for <dl class="std option">.

    None for normal definition lists (field lists, glossaries...).
    """
    if tag.name != "dl":
        return None
    tag_classes = classes(tag)
    if "py" in tag_classes or "std" in tag_classes:
        kinds = [c for c in tag_classes if c not in {"py", "std"}]
        return kinds[0] if kinds else None
    if "describe" in tag_classes:
        return "describe"
    return None


def inline_text(element: PageElement) -> str:
    """Text of an inline element. Inline code gets backticks."""
    if isinstance(element, Comment):
        return ""
    if isinstance(element, NavigableString):
        return str(element)
    if not isinstance(element, Tag) or is_skipped(element):
        return ""
    if element.name == "br":
        return "\n"
    inner = "".join(inline_text(c) for c in element.children)
    if element.name == "code" and inner.strip():
        return f"`{clean(inner)}`"
    if "footnote-reference" in classes(element):
        return f"[{clean(inner).strip('[]')}]"
    return inner


def heading_text(tag: Tag) -> str:
    return clean(inline_text(tag)).replace("`", "")


def signature_text(dt: Tag) -> str:
    """'classmethod Path.from_uri(uri)¶' -> 'Path.from_uri(uri)'"""
    parts: list[str] = []
    for node in dt.descendants:
        if not isinstance(node, NavigableString) or isinstance(node, Comment):
            continue
        # skip text inside the ¶ link or the "classmethod" span. Stop at the dt,
        # otherwise a <dl class="py property"> above us would match "property".
        skip = False
        for parent in node.parents:
            if parent is dt:
                break
            if {"headerlink", "property"}.intersection(classes(parent)):
                skip = True
                break
        if not skip:
            parts.append(str(node))
    return clean("".join(parts))


def qualifiers(dt: Tag, kind: str) -> list[str]:
    # "classmethod", "async", "abstractmethod"... (minus the kind itself, so a
    # class doesn't get "class" as a qualifier)
    words: list[str] = []
    for span in dt.find_all("span", class_="property"):
        if isinstance(span, Tag):
            words.extend(span.get_text(" ").split())
    return [w for w in dict.fromkeys(words) if w != kind]


def displayed_name(dt: Tag) -> str:
    # The name as printed, for objects without a python anchor: "--sort-keys", "NOP".
    # sig-prename is only a prefix when it comes first ("json." + "dumps"). For
    # options and pdb commands it comes after the name and holds the argument
    # ("-m" + " <mode>", "pp" + " expression"), so it's not part of the name.
    name = dt.find(class_="sig-name")
    if not isinstance(name, Tag):
        return signature_text(dt)
    prefix = name.find_previous_sibling(class_="sig-prename")
    text = (prefix.get_text() if isinstance(prefix, Tag) else "") + name.get_text()
    return clean(text) or signature_text(dt)


def code_block(tag: Tag) -> str | None:
    # get_text() on the <pre> as-is: stripping the pieces would lose the newlines
    pre = tag if tag.name == "pre" else tag.find("pre")
    if not isinstance(pre, Tag):
        return None
    language = "text"
    for c in classes(tag):
        if c.startswith("highlight-"):
            language = c.removeprefix("highlight-")
    language = CODE_LANGUAGE_ALIASES.get(language, language)
    code = pre.get_text().rstrip("\n")
    return f"```{language}\n{code}\n```"
