"""Record format for records.jsonl (one record per line).

A record is either a section (kind="section") or one documented object (function,
method, class, option...). Empty fields aren't written to the file.
"""

from typing import Literal

from pydantic import BaseModel, Field

VersionNoteKind = Literal["added", "changed", "deprecated", "removed"]


class VersionNote(BaseModel):
    kind: VersionNoteKind
    version: str  # "3.12"
    removed_in: str | None = None  # "deprecated since 3.12, removed in 3.14"


class DocRecord(BaseModel):
    id: str  # page + anchor: "library/pathlib.html#pathlib.Path.rename"
    kind: str
    name: str  # "pathlib.Path.rename", or the title for a section
    signatures: list[str] = Field(default_factory=list)  # more than one for overloads
    qualifiers: list[str] = Field(default_factory=list)  # classmethod, async...
    aliases: list[str] = Field(default_factory=list)
    parent: str | None = None  # id of the class, for methods and attributes
    library: str  # "pathlib", "asyncio", "builtins"
    module: str | None = None
    heading_path: list[str]  # page title down to the closest section
    text: str
    versions: list[VersionNote] = Field(default_factory=list)
    url: str
    order: int  # reading order inside the page
