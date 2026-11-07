from pydocs.ingestion.download import archive_url

EXPECTED_URL = "https://docs.python.org/3.15/archives/python-3.15-docs-html.zip"


def test_archive() -> None:
    assert EXPECTED_URL == archive_url("3.15")
