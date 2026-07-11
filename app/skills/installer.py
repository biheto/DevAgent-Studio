from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


class SkillInstaller:
    def __init__(self, skills_dir: str | Path = "data/skills"):
        self.skills_dir = Path(skills_dir)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def parse_github_url(self, url: str) -> dict[str, str]:
        url = url.strip().rstrip("/")

        match = re.match(r"^https?://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+)(?:/(.+))?)?$", url)
        if match:
            return {
                "owner": match.group(1),
                "repo": match.group(2),
                "branch": match.group(3) or "main",
                "path": match.group(4) or "",
            }

        match = re.match(r"^git@github\.com:([^/]+)/([^/]+)(?:\.git)?$", url)
        if match:
            return {"owner": match.group(1), "repo": match.group(2), "branch": "main", "path": ""}

        parts = url.split("/")
        if len(parts) >= 2:
            return {"owner": parts[0], "repo": parts[1], "branch": "main", "path": "/".join(parts[2:]) if len(parts) > 2 else ""}

        raise ValueError(f"Cannot parse GitHub URL: {url}")

    def clone_repo(self, owner: str, repo: str, branch: str = "main", dest_dir: str | None = None) -> Path:
        clone_dir = Path(dest_dir or tempfile.mkdtemp(prefix="skill_clone_"))
        url = f"https://github.com/{owner}/{repo}.git"
        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", "-b", branch, url, str(clone_dir)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed: {result.stderr or result.stdout}")
        return clone_dir

    def discover_skill_md(self, repo_dir: Path, subpath: str = "") -> list[Path]:
        search_dir = repo_dir / subpath if subpath else repo_dir
        if not search_dir.exists():
            return []
        return list(search_dir.rglob("SKILL.md"))

    def parse_skill_md(self, skill_md_path: Path) -> dict[str, Any]:
        content = skill_md_path.read_text(encoding="utf-8")
        frontmatter: dict[str, Any] = {}
        body = content

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                import yaml
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()

        return {
            "name": frontmatter.get("name", skill_md_path.parent.name),
            "description": frontmatter.get("description", ""),
            "body": body,
            "version": frontmatter.get("version"),
            "author": frontmatter.get("author"),
            "compatibility": frontmatter.get("compatibility"),
            "execution_type": frontmatter.get("execution_type", "prompt"),
            "agent_type": frontmatter.get("agent_type"),
            "agent_config": frontmatter.get("agent_config"),
            "model_override": frontmatter.get("model_override"),
        }

    def install_skill(self, url: str) -> list[dict[str, Any]]:
        from app.persistence.sqlite_store import task_store

        parsed = self.parse_github_url(url)
        clone_dir = self.clone_repo(parsed["owner"], parsed["repo"], parsed["branch"])

        try:
            skill_mds = self.discover_skill_md(clone_dir, parsed["path"])
            if not skill_mds:
                raise RuntimeError(f"No SKILL.md found in {url}")

            installed = []
            for skill_md in skill_mds:
                skill_data = self.parse_skill_md(skill_md)
                code = re.sub(r"[^a-z0-9]+", "-", skill_data["name"].lower()).strip("-")
                if not code:
                    code = skill_md.parent.name.lower()

                dest = self.skills_dir / code
                if dest.exists():
                    shutil.rmtree(dest)
                dest.mkdir(parents=True)
                shutil.copy2(skill_md, dest / "SKILL.md")
                for extra_file in skill_md.parent.glob("*"):
                    if extra_file.name != "SKILL.md" and extra_file.is_file():
                        shutil.copy2(extra_file, dest / extra_file.name)

                execution_type = skill_data.get("execution_type", "prompt")
                execution: dict[str, Any] = {"type": execution_type}

                if execution_type == "prompt":
                    execution["prompt"] = skill_data["body"]
                    if skill_data.get("model_override"):
                        execution["model_override"] = skill_data["model_override"]
                elif execution_type == "agent":
                    execution["agent_type"] = skill_data.get("agent_type", "")
                    execution["agent_config"] = skill_data.get("agent_config") or {}
                elif execution_type == "workflow":
                    import json
                    import re as _re
                    workflow_match = _re.search(r'```json\s*(\{.*?\})\s*```', skill_data["body"], _re.DOTALL | _re.MULTILINE)
                    if workflow_match:
                        try:
                            workflow = json.loads(workflow_match.group(1))
                            execution["nodes"] = workflow.get("nodes", [])
                            execution["edges"] = workflow.get("edges", [])
                        except json.JSONDecodeError:
                            pass  # Invalid JSON in workflow block, skip silently

                record = task_store.save_skill({
                    "code": code,
                    "name": skill_data["name"],
                    "description": skill_data["description"],
                    "version": skill_data.get("version"),
                    "author": skill_data.get("author"),
                    "source_type": "installed",
                    "source_url": url,
                    "skill_path": str(skill_md.relative_to(clone_dir)),
                    "execution": execution,
                    "is_enabled": True,
                })
                installed.append(record)

            return installed
        finally:
            shutil.rmtree(clone_dir, ignore_errors=True)

    def uninstall_skill(self, code: str) -> bool:
        dest = self.skills_dir / code
        if dest.exists():
            shutil.rmtree(dest)
        return True
