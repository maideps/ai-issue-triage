"""Fetch a repository's Markdown docs as they were at a given date (D-004).

The commit is resolved through the GitHub API and its tarball is downloaded once into a
cache under ``data/repos``. Only ``<docs_dir>/**/*.md`` members are extracted, with path
checks, so nothing else from the archive lands on disk.
"""

import json
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TypeVar

from issue_triage.domain.docs import DocPage, page_title

API = "https://api.github.com"
T = TypeVar("T")


class GitHubDocsSource:
    def __init__(
        self,
        cache_dir: Path,
        token: str,
        *,
        docs_dir: str = "docs",
        retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cache_dir = cache_dir
        self._token = token
        self._docs_dir = docs_dir
        self._retries = retries
        self._sleep = sleep

    def _request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "Authorization": f"bearer {self._token}",
                "Accept": "application/vnd.github+json",
            },
        )

    def _with_retries(self, action: Callable[[], T]) -> T:
        for attempt in range(self._retries + 1):
            try:
                return action()
            except (OSError, urllib.error.URLError):  # resets mid-download included
                if attempt == self._retries:
                    raise
                self._sleep(min(2.0**attempt, 30.0))
        raise AssertionError("unreachable")

    def commit_before(self, repository: str, before: datetime) -> str:
        url = f"{API}/repos/{repository}/commits?until={before.isoformat()}&per_page=1"

        def fetch() -> bytes:
            with urllib.request.urlopen(self._request(url), timeout=60) as response:
                data: bytes = response.read()
                return data

        commits = json.loads(self._with_retries(fetch))
        if not commits:
            raise RuntimeError(f"no commit before {before} in {repository}")
        return str(commits[0]["sha"])

    def _download_docs(self, repository: str, sha: str, target: Path) -> None:
        url = f"{API}/repos/{repository}/tarball/{sha}"
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "repo.tar.gz"

            def fetch() -> None:
                with (
                    urllib.request.urlopen(self._request(url), timeout=300) as response,
                    archive.open("wb") as handle,
                ):
                    shutil.copyfileobj(response, handle)

            self._with_retries(fetch)
            staging = Path(tmp) / "docs"
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar.getmembers():
                    parts = PurePosixPath(member.name).parts
                    # <owner>-<repo>-<sha>/<docs_dir>/.../*.md, regular files only.
                    if (
                        len(parts) < 3
                        or parts[1] != self._docs_dir
                        or not member.isfile()
                        or not member.name.endswith(".md")
                        or any(part in ("..", "") for part in parts)
                    ):
                        continue
                    destination = staging.joinpath(*parts[2:])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    if source is not None:
                        destination.write_bytes(source.read())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging), str(target))

    def snapshot(self, repository: str, before: datetime) -> tuple[str, list[DocPage]]:
        """(commit sha, pages) for the docs at the last commit before ``before``."""
        sha = self.commit_before(repository, before)
        root = self._cache_dir / f"{repository.replace('/', '__')}@{sha[:12]}"
        if not root.exists():
            self._download_docs(repository, sha, root)
        pages = [
            DocPage(
                source=repository,
                path=path.relative_to(root).as_posix(),
                title=page_title(text, path.stem.replace("-", " ")),
                text=text,
            )
            for path in sorted(root.rglob("*.md"))
            if (text := path.read_text(encoding="utf-8"))
        ]
        return sha, pages


def local_docs(root: Path, repository: str) -> list[DocPage]:
    """Docs already on disk (the committed demo snapshot), same shape as a snapshot."""
    return [
        DocPage(
            source=repository,
            path=path.relative_to(root).as_posix(),
            title=page_title(text, path.stem.replace("-", " ")),
            text=text,
        )
        for path in sorted(root.rglob("*.md"))
        if path.name != "NOTICE.md" and (text := path.read_text(encoding="utf-8"))
    ]
