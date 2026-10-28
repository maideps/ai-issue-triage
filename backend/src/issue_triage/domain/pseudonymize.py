"""Replace usernames with stable pseudonyms before anything is stored (D-003)."""

import hashlib
import hmac
import re

# An @handle not preceded by a word character, slash, dot or @ (so emails, URLs and
# "pkg@1.0" stay intact). GitHub logins are 1-39 alphanumerics or hyphens.
MENTION = re.compile(r"(?<![\w/.@])@([A-Za-z0-9][A-Za-z0-9-]{0,38})")
# Fenced blocks and inline code: decorators like @pytest.fixture live there.
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)
GHOST = "ghost"


class Pseudonymizer:
    """Keyed hashing: the same login always maps to the same pseudonym for one salt."""

    def __init__(self, salt: str) -> None:
        self._key = salt.encode("utf-8")

    def user(self, login: str | None) -> str:
        if not login:
            return GHOST
        digest = hmac.new(self._key, login.lower().encode("utf-8"), hashlib.sha256)
        return f"user-{digest.hexdigest()[:10]}"

    def text(self, text: str) -> str:
        """Pseudonymize @mentions outside code."""
        parts: list[str] = []
        position = 0
        for match in CODE.finditer(text):
            parts.append(self._mentions(text[position : match.start()]))
            parts.append(match.group(0))
            position = match.end()
        parts.append(self._mentions(text[position:]))
        return "".join(parts)

    def _mentions(self, text: str) -> str:
        return MENTION.sub(lambda m: "@" + self.user(m.group(1)), text)
