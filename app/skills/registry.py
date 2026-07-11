from __future__ import annotations

import logging
from typing import Any

from app.skills.base import Skill, SkillContext
from app.skills.builtin import builtin_skills
from app.skills.executor import execute_skill_by_type, _safe_int

logger = logging.getLogger(__name__)


class DBSkillWrapper:
    """Wraps a DB-stored skill row to implement the Skill Protocol."""

    def __init__(self, skill_row: dict[str, Any]):
        self.code: str = skill_row.get("code", "")
        self.name: str = skill_row.get("name", "")
        self.description: str = skill_row.get("description", "")
        self._execution: dict[str, Any] = skill_row.get("execution", {})

    def execute(self, context: SkillContext, input_data: dict[str, Any]) -> dict[str, Any]:
        return execute_skill_by_type(
            skill_code=self.code,
            execution=self._execution,
            input_data=input_data,
            goal=input_data.get("goal", ""),
            project_path=input_data.get("project_path"),
            max_files=_safe_int(input_data.get("max_files"), 200),
        )


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills: dict[str, Skill | DBSkillWrapper] = {
            skill.code: skill for skill in (skills or builtin_skills())
        }
        self._load_db_skills()

    def _load_db_skills(self):
        try:
            from app.persistence.sqlite_store import task_store
            for skill_row in task_store.list_skills():
                code = skill_row.get("code")
                if code and code not in self._skills:
                    self._skills[code] = DBSkillWrapper(skill_row)
        except Exception as exc:
            logger.warning("Failed to load DB skills into registry: %s", exc)

    def refresh(self):
        self._skills = {skill.code: skill for skill in builtin_skills()}
        self._load_db_skills()

    def list_skills(self) -> list[dict[str, str]]:
        return [
            {
                "code": skill.code,
                "name": skill.name,
                "description": skill.description,
            }
            for skill in self._skills.values()
        ]

    def get(self, code: str) -> Skill | DBSkillWrapper:
        if code not in self._skills:
            raise KeyError(f"Skill not found: {code}")
        return self._skills[code]

    def execute(self, code: str, context: SkillContext, input_data: dict) -> dict:
        return self.get(code).execute(context, input_data)


skill_registry = SkillRegistry()
