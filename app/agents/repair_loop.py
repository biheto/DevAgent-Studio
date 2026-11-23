"""Bounded candidate repairs: isolated files, exact edits, validation, explicit application."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.providers.llm_provider import llm_provider


class RepairOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    max_rounds: int = Field(default=2, ge=1, le=3)
    validation: str = Field(default="python_compile", pattern="^(python_compile|pytest)$")


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=300)
    old_text: str = Field(min_length=1, max_length=12000)
    new_text: str = Field(max_length=12000)


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=1000)
    edits: list[Edit] = Field(max_length=8)


EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", ".python", "data", "dist", "build", ".idea", ".vscode"}
EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".css", ".html", ".md", ".json", ".toml", ".txt", ".yaml", ".yml", ".ini", ".cfg"}


def editable_source(name: str) -> bool:
    path = Path(name)
    return (path.suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".css", ".html"}
            and not any(part.lower() in {"tests", "test", "__tests__"} for part in path.parts)
            and path.name not in {"conftest.py", "setup.py"}
            and not path.name.startswith("test_") and not path.stem.endswith("_test")
            and ".test." not in path.name and ".spec." not in path.name)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_file(root: Path, name: str) -> Path:
    if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
        raise ValueError("Linked project roots are not supported")
    relative = Path(name)
    if relative.is_absolute() or relative.drive or ":" in name or ".." in relative.parts or not relative.parts:
        raise ValueError("Repair paths must remain inside the project")
    if any(part in EXCLUDED or part.startswith(".") for part in relative.parts):
        raise ValueError("Hidden and generated files cannot be repaired")
    path = root / relative
    if path.suffix.lower() not in EXTENSIONS or any(x in path.name.lower() for x in ("secret", "credential", "lock")):
        raise ValueError("Sensitive or unsupported repair file")
    current = path
    while current != root:
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Linked repair paths are not supported")
        current = current.parent
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Repair path escaped project")
    return path


def repair_root() -> Path:
    return Path(os.getenv("DEV_AGENT_REPAIR_DIR", "data/repair_runs")).resolve()


def copy_project(source: Path, target: Path):
    files, size = 0, 0
    for directory, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = [name for name in dirs if name not in EXCLUDED and not name.startswith(".") and not (Path(directory) / name).is_symlink() and not (hasattr(Path(directory) / name, "is_junction") and (Path(directory) / name).is_junction())]
        for name in names:
            relative = (Path(directory) / name).relative_to(source)
            try:
                path = safe_file(source, str(relative))
            except ValueError:
                continue
            files += 1
            size += path.stat().st_size
            if files > 3000 or size > 20_000_000:
                raise ValueError("Repair workspace exceeds 3000 files or 20 MB; use a smaller project scope")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)


def validate(project: Path, mode: str) -> dict:
    if mode == "python_compile":
        checked, errors = 0, []
        for path in project.rglob("*.py"):
            try:
                compile(path.read_bytes(), str(path.relative_to(project)), "exec")
                checked += 1
            except (SyntaxError, ValueError) as exc:
                errors.append(str(exc))
        return {"passed": checked > 0 and not errors, "kind": mode, "checked_files": checked,
                "output": "\n".join(errors)[:6000] or ("Python syntax checks passed; no behavioral tests were run." if checked else "No Python files; select a supported project/validator.")}
    env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "COMSPEC"}}
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    try:
        # An explicit preset, never a model-generated command; pytest executes project code.
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=project,
                                env=env, capture_output=True, timeout=60)
        return {"passed": result.returncode == 0, "kind": mode, "exit_code": result.returncode,
                "output": (result.stdout + result.stderr).decode("utf-8", errors="replace")[-6000:]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "kind": mode, "output": "Validation exceeded 60 seconds"}


def attempt_repair(state: dict) -> dict:
    original = Path(state.get("original_project_path") or state["project_path"]).resolve()
    source = Path(state["project_path"]).resolve()
    round_no = state.get("repair_round", 0) + 1
    workspace = repair_root() / state["task_id"] / f"round-{round_no}-{uuid4().hex[:12]}"
    if workspace.is_relative_to(original):
        # data/ is excluded, but reject other nested workspace configurations too.
        if workspace.relative_to(original).parts[0] not in EXCLUDED:
            raise ValueError("Repair workspace must be outside the scanned project")
    workspace.mkdir(parents=True, exist_ok=True)
    copy_project(source, workspace)
    findings = (state.get("code_review") or {}).get("findings", [])
    paths = list(dict.fromkeys(str(item.get("path") or item.get("file") or "") for item in findings))
    previous = state.get("repair_result") or {}
    paths += [name for name in previous.get("changed_files", []) if name not in paths]
    content = {}
    for name in paths[:8]:
        try:
            path = safe_file(workspace, name)
            if editable_source(name) and path.is_file() and path.stat().st_size < 20000:
                content[name] = path.read_bytes().decode("utf-8")
        except (ValueError, UnicodeError, OSError):
            continue
    if not content:
        return {"status": "no_editable_evidence", "round": round_no, "reason": "没有可定位到可编辑文件的证据", "changed_files": []}
    baseline = {name: previous.get("original_hashes", {}).get(name, digest(content[name].encode("utf-8"))) for name in content}
    response = llm_provider.generate_with_status(
        '修复已定位的代码问题。只输出 JSON {"reason":"原因","edits":[{"path":"文件","old_text":"精确原文","new_text":"替换内容"}]}。'
        '每处原文必须唯一匹配，只修改提供的源文件，不能修改测试以掩盖失败。不要输出命令。',
        json.dumps({"goal": state["goal"], "files": content, "findings": findings[:20], "previous_validation": previous.get("validation")}, ensure_ascii=False),
        '{"reason":"未配置可用模型，无法生成真实修复","edits":[]}', agent="repairer", prompt_version="repair.v1")
    try:
        proposal = Proposal.model_validate_json(response["text"])
        updates = dict(content)
        for edit in proposal.edits:
            if edit.path not in content or not editable_source(edit.path):
                raise ValueError("Edits must target supplied source files, not tests")
            newline = "\r\n" if "\r\n" in updates[edit.path] else "\n"
            old_text = edit.old_text.replace("\r\n", "\n").replace("\n", newline)
            new_text = edit.new_text.replace("\r\n", "\n").replace("\n", newline)
            if updates[edit.path].count(old_text) != 1:
                raise ValueError(f"Edit must match exactly once: {edit.path}")
            updates[edit.path] = updates[edit.path].replace(old_text, new_text, 1)
        changed = [name for name in updates if updates[name] != content[name]]
        if not changed:
            return {"status": "no_changes", "round": round_no, "reason": proposal.reason, "changed_files": [], "source": response.get("answer_source")}
        for name in changed:
            safe_file(workspace, name).write_text(updates[name], encoding="utf-8", newline="")
        validation = validate(workspace, state["repair_options"]["validation"])
        all_changed = list(dict.fromkeys([*previous.get("changed_files", []), *changed]))
        edits, diffs = [], []
        for name in all_changed:
            before = safe_file(original, name).read_bytes()
            after = safe_file(workspace, name).read_bytes()
            edits.append({"path": name, "before_hash": baseline.get(name) or previous["original_hashes"][name], "after_hash": digest(after)})
            diffs.extend(difflib.unified_diff(before.decode("utf-8").splitlines(True), after.decode("utf-8").splitlines(True), fromfile=f"a/{name}", tofile=f"b/{name}"))
        manifest = {"original": str(original), "workspace": str(workspace), "edits": edits, "validation": validation, "applied": False}
        (workspace.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        return {"status": "validated" if validation["passed"] else "validation_failed", "round": round_no,
                "reason": proposal.reason, "workspace": str(workspace), "changed_files": all_changed,
                "original_hashes": {item["path"]: item["before_hash"] for item in edits}, "validation": validation,
                "diff": "".join(diffs)[:100000], "source": response.get("answer_source"), "applied": False}
    except (ValueError, KeyError) as exc:
        return {"status": "invalid_patch", "round": round_no, "reason": str(exc), "changed_files": []}


def apply_candidate(task_id: str) -> dict:
    manifest_path = repair_root() / task_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest["validation"]["passed"]:
        raise ValueError("Candidate validation has not passed")
    original, workspace = Path(manifest["original"]), Path(manifest["workspace"])
    writes = []
    for item in manifest["edits"]:
        target = safe_file(original, item["path"])
        candidate = safe_file(workspace, item["path"]).read_bytes()
        if digest(candidate) != item["after_hash"]:
            raise ValueError("Candidate changed since validation; regenerate the repair")
        current = digest(target.read_bytes())
        if current == item["after_hash"]:
            continue
        if current != item["before_hash"]:
            raise ValueError(f"Original file changed; refusing to overwrite: {item['path']}")
        writes.append((target, candidate, item["before_hash"]))
    for target, candidate, expected_hash in writes:
        if digest(target.read_bytes()) != expected_hash:
            raise ValueError(f"Original file changed during application: {target.name}")
        staged = target.with_name(target.name + ".devagent-tmp")
        with staged.open("xb") as stream:
            stream.write(candidate)
        staged.replace(target)
    manifest["applied"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return {"applied": True, "files": [item["path"] for item in manifest["edits"]]}
