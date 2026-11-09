"""Print records from records.jsonl so I can check them by eye.

Usage:
    uv run python -m pydocs.ingestion.show pathlib
    uv run python -m pydocs.ingestion.show pathlib rename
    uv run python -m pydocs.ingestion.show builtins dict.get --full
    uv run python -m pydocs.ingestion.show --random 20
"""

import argparse
import random
from pathlib import Path

from pydocs.ingestion.models import DocRecord
from pydocs.ingestion.parse import PROCESSED_DIR

RECORDS_PATH = PROCESSED_DIR / "records.jsonl"
PREVIEW_CHARS = 400


def load_records(path: Path = RECORDS_PATH) -> list[DocRecord]:
    with path.open(encoding="utf-8") as f:
        return [DocRecord.model_validate_json(line) for line in f]


def format_record(record: DocRecord, full: bool = False) -> str:
    text = record.text
    if not full and len(text) > PREVIEW_CHARS:
        text = text[:PREVIEW_CHARS] + " [...]"

    lines = [
        f"=== {record.kind.upper()}  {record.name}",
        f"id:      {record.id}",
        f"library: {record.library}   module: {record.module}",
        f"where:   {' > '.join(record.heading_path)}",
        f"link:    {record.url}",
    ]
    if record.signatures:
        lines.append("signatures:")
        lines.extend(f"  {s}" for s in record.signatures)
    if record.qualifiers:
        lines.append(f"qualifiers: {', '.join(record.qualifiers)}")
    if record.aliases:
        lines.append(f"aliases: {', '.join(record.aliases)}")
    if record.parent:
        lines.append(f"parent:  {record.parent}")
    if record.versions:
        notes = ", ".join(f"{v.kind} {v.version}" for v in record.versions)
        lines.append(f"versions: {notes}")
    lines.append("")
    lines.append(text or "(no description in the docs)")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Show parsed docs records.")
    parser.add_argument("library", nargs="?", help="e.g. pathlib, json, builtins")
    parser.add_argument("name", nargs="?", help="part of the name, e.g. rename")
    parser.add_argument("--random", type=int, metavar="N", help="pick N random records")
    parser.add_argument("--full", action="store_true", help="don't cut the text")
    args = parser.parse_args()

    records = load_records()
    if args.library:
        records = [r for r in records if r.library == args.library]
    if args.name:
        records = [r for r in records if args.name in r.name]
    if args.random:
        records = random.sample(records, min(args.random, len(records)))

    for record in records:
        print(format_record(record, full=args.full))
        print()
    print(f"{len(records)} record(s)")


if __name__ == "__main__":
    main()
