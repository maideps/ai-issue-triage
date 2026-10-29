"""Read and write evaluation cases as JSON Lines."""

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def _default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def write_jsonl(path: Path, records: Iterable[Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            data = (
                asdict(record) if is_dataclass(record) and not isinstance(record, type) else record
            )
            handle.write(json.dumps(data, ensure_ascii=False, default=_default) + "\n")
            count += 1
    return count


def read_cases(path: Path, case_type: type[T]) -> list[T]:
    """Read dataclass cases back; ``as_of`` is restored as a datetime."""
    cases: list[T] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                data = json.loads(line)
                data["as_of"] = datetime.fromisoformat(data["as_of"])
                cases.append(case_type(**data))
    return cases


def write_json(path: Path, data: dict[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")  # LF on Windows too
