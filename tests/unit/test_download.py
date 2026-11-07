import zipfile
from pathlib import Path

from pydocs.ingestion.download import archive_url, download_archive, extract_archive

EXPECTED_URL = "https://docs.python.org/3.15/archives/python-3.15-docs-html.zip"


def test_archive() -> None:
    assert EXPECTED_URL == archive_url("3.15")


def test_download_skips_existing_file(tmp_path: Path) -> None:
    # zip is "already downloaded"
    dest = tmp_path / "docs.zip"
    dest.write_bytes(b"already here")

    # bad URL on purpose: only passes if we never hit the network
    download_archive("http://invalid.invalid/docs.zip", dest)

    assert dest.read_bytes() == b"already here"


def test_extract_archive_creates_folder_then_skips(tmp_path: Path) -> None:
    # same layout as the real archive: <stem>/library/...
    zip_path = tmp_path / "docs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("docs/library/page.html", "<html></html>")

    extract_archive(zip_path, tmp_path)
    page = tmp_path / "docs" / "library" / "page.html"
    assert page.exists()

    # second run should skip, so the marker must survive
    page.write_text("marker")
    extract_archive(zip_path, tmp_path)
    assert page.read_text() == "marker"
