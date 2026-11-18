"""评测集的数据结构。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Question:
    id: str
    difficulty: str
    tags: tuple[str, ...]
    question: str
    gold_sql: str
    note: str = ""
    cohort: str = ""


@dataclass(slots=True)
class Dataset:
    version: str
    questions: list[Question] = field(default_factory=list)

    def by_difficulty(self) -> dict[str, list[Question]]:
        out: dict[str, list[Question]] = {}
        for q in self.questions:
            out.setdefault(q.difficulty, []).append(q)
        return out

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for q in self.questions:
            tags.update(q.tags)
        return sorted(tags)


def load_dataset(path: str | Path) -> Dataset:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = [
        Question(
            id=item["id"],
            difficulty=item["difficulty"],
            tags=tuple(item.get("tags", ())),
            question=item["question"],
            gold_sql=item["gold_sql"],
            note=item.get("note", ""),
            cohort=item.get("cohort", ""),
        )
        for item in raw["questions"]
    ]
    ids = [q.id for q in questions]
    duplicated = {i for i in ids if ids.count(i) > 1}
    if duplicated:
        raise ValueError(f"评测集存在重复 id: {sorted(duplicated)}")
    return Dataset(version=raw.get("version", "unknown"), questions=questions)
