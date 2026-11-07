import zipfile
from pathlib import Path

import httpx

DOCS_VERSION = "3.15"
RAW_DIR = Path(__file__).resolve().parents[3].joinpath("data", "raw")
# PurePath.joinpath()


def archive_url(version: str) -> str:
    return f"https://docs.python.org/{version}/archives/python-{version}-docs-html.zip"


def download_archive(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"Already downloaded so skip this step: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for data in r.iter_bytes():
                f.write(data)
    print("Download finished!")


def extract_archive(zip_path: Path, target_dir: Path) -> None:
    extracted_dir = target_dir.joinpath(zip_path.stem)
    if extracted_dir.exists():
        print(f"Already unzipped so skip this step: {extracted_dir}")
        return
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(target_dir)
    print(f"The files were unzipped here: {target_dir}")


def main() -> None:
    url = archive_url(DOCS_VERSION)
    file_name = Path(url).name
    destination_path = RAW_DIR.joinpath(file_name)
    download_archive(url, destination_path)
    extract_archive(destination_path, RAW_DIR)


if __name__ == "__main__":
    main()
# print(RAW_DIR.parent)
