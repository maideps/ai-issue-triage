"""Where the system may post, decided in one place (D-003, D-014).

Posting is allowed only to repositories on an explicit allowlist, and never to the
repository the system learns and is evaluated on, even if someone adds it to the list.
"""

import hashlib
import hmac
from collections.abc import Collection
from typing import Protocol


class PostingRefused(PermissionError):
    """The target repository is not allowed to receive posts."""


def check_allowed(repository: str, allowlist: Collection[str], protected: Collection[str]) -> None:
    target = repository.lower()
    if target in {p.lower() for p in protected}:
        raise PostingRefused(f"{repository} is a protected evaluation target: never post there")
    if target not in {a.lower() for a in allowlist}:
        raise PostingRefused(f"{repository} is not on the posting allowlist")


def valid_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """GitHub's X-Hub-Signature-256: ``sha256=<hex HMAC of the raw body>``."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


class CommentPoster(Protocol):
    def post_comment(self, repository: str, number: int, body: str) -> str:
        """Post and return the comment's URL."""
        ...


class SafePublisher:
    """Wraps a poster so every call path goes through the allowlist."""

    def __init__(
        self, poster: CommentPoster, allowlist: Collection[str], protected: Collection[str]
    ) -> None:
        self._poster = poster
        self._allowlist = tuple(allowlist)
        self._protected = tuple(protected)

    def allowed(self, repository: str) -> bool:
        try:
            check_allowed(repository, self._allowlist, self._protected)
        except PostingRefused:
            return False
        return True

    def post(self, repository: str, number: int, body: str) -> str:
        check_allowed(repository, self._allowlist, self._protected)
        return self._poster.post_comment(repository, number, body)
