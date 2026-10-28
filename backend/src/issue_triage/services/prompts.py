"""Versioned prompt templates: ``prompts/<name>/<version>/{system,user}.txt``.

Templates use ``$placeholders`` (string.Template), so issue text containing braces or
dollar signs is inserted verbatim. Every call records which prompt version it used.
"""

from dataclasses import dataclass
from pathlib import Path
from string import Template

from issue_triage.domain.llm import Message

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user: str

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def render(self, **values: str) -> list[Message]:
        """Fill every placeholder; a missing value raises KeyError."""
        return [
            Message("system", Template(self.system).substitute(values)),
            Message("user", Template(self.user).substitute(values)),
        ]


def load_prompt(name: str, version: str, root: Path = PROMPTS_DIR) -> Prompt:
    directory = root / name / version
    if not directory.is_dir():
        raise FileNotFoundError(f"no prompt {name}@{version} under {root}")
    return Prompt(
        name=name,
        version=version,
        system=(directory / "system.txt").read_text(encoding="utf-8"),
        user=(directory / "user.txt").read_text(encoding="utf-8"),
    )
