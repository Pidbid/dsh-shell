"""Manage user skills under ``~/.agents/skills``.

DeepSeek Harness discovers directory bundles (``<name>/SKILL.md``) and flat
``*.md`` files from ``$DSH_AGENTS_HOME/skills``.  Enabling/disabling here means
moving an entry between that scanned root and the sibling
``skills-disabled`` directory; the content is never deleted.

Only ``SKILL.md`` frontmatter fields understood by the harness are edited:
``name``, ``description``, ``whenToUse``, ``disable-model-invocation`` and
``user-invocable``.  Unknown fields are preserved verbatim.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from ruamel.yaml import YAML

from .paths import agents_home, disabled_skills_root, skills_root, trash_dir

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"^\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)(.*)$", re.DOTALL)
MAX_SKILL_BYTES = 4 * 1024 * 1024
MAX_FILES = 200


class SkillError(RuntimeError):
    """Invalid skill operation."""


@dataclass
class SkillRecord:
    id: str
    name: str
    description: str
    path: Path
    kind: str
    enabled: bool
    rel: str
    when_to_use: str = ""
    model_invocable: bool = True
    user_invocable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    valid: bool = True
    error: str | None = None
    size: int = 0
    modified: float = 0.0
    files: list[str] = field(default_factory=list)
    locked_source: str | None = None
    locked_updated_at: str | None = None

    @property
    def skill_file(self) -> Path:
        return self.path / "SKILL.md" if self.kind == "directory" else self.path

    @property
    def directory(self) -> Path:
        return self.path if self.kind == "directory" else self.path.parent

    def to_dict(self, include_body: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "skillFile": str(self.skill_file),
            "directory": str(self.directory),
            "kind": self.kind,
            "enabled": self.enabled,
            "rel": self.rel,
            "whenToUse": self.when_to_use,
            "modelInvocable": self.model_invocable,
            "userInvocable": self.user_invocable,
            "metadata": self.metadata,
            "extraFrontmatter": {
                key: value
                for key, value in self.frontmatter.items()
                if key
                not in {
                    "name",
                    "description",
                    "whenToUse",
                    "disable-model-invocation",
                    "user-invocable",
                    "metadata",
                }
            },
            "valid": self.valid,
            "error": self.error,
            "size": self.size,
            "modified": self.modified,
            "files": self.files,
            "lockedSource": self.locked_source,
            "lockedUpdatedAt": self.locked_updated_at,
        }
        if include_body:
            payload["body"] = self.body
            payload["content"] = self._content()
        return payload

    def _content(self) -> str:
        try:
            return self.skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _safe_yaml() -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.allow_unicode = True
    yaml.default_flow_style = False
    yaml.sort_keys = False
    yaml.width = 1000
    try:
        yaml.representer.sort_base_mapping_type_on_output = False
    except AttributeError:
        pass
    return yaml


def _bool_value(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    return default


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        data = _safe_yaml().load(match.group(1)) or {}
    except Exception as error:
        raise SkillError(f"YAML frontmatter 解析失败: {error}") from error
    if not isinstance(data, dict):
        raise SkillError("frontmatter 必须是 YAML 映射。")
    return _plain(data), match.group(2).lstrip("\r\n")


def _yaml_frontmatter(data: dict[str, Any]) -> str:
    yaml = _safe_yaml()
    import io

    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue().rstrip("\n")


def _frontmatter_payload(
    *,
    name: str,
    description: str,
    when_to_use: str,
    model_invocable: bool,
    user_invocable: bool,
    extra: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "description": description}
    if when_to_use.strip():
        payload["whenToUse"] = when_to_use.strip()
    if not model_invocable:
        payload["disable-model-invocation"] = True
    if not user_invocable:
        payload["user-invocable"] = False
    for key, value in extra.items():
        if key in payload or key in ("disableModelInvocation", "modelInvocable", "userInvocable"):
            continue
        payload[key] = value
    return payload


def _validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise SkillError("skill 名称不能为空。")
    if len(name) > 64 or not SKILL_NAME_RE.match(name):
        raise SkillError("skill 名称必须是 kebab-case（小写字母、数字、连字符，最长 64）。")
    return name


def _safe_rel(rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise SkillError("非法 skill 路径。")
    return candidate


class SkillsManager:
    """Thread-safe skill discovery and file operations."""

    def __init__(
        self,
        root: str | Path | None = None,
        disabled_root: str | Path | None = None,
        trash_root: str | Path | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self.root = Path(root).expanduser() if root else skills_root()
        self.disabled_root = Path(disabled_root).expanduser() if disabled_root else disabled_skills_root()
        self.trash_root = Path(trash_root).expanduser() if trash_root else trash_dir()

    # ------------------------------------------------------------------ reading
    def list(self, include_disabled: bool = True) -> dict[str, Any]:
        with self._lock:
            lock_data = self._lock_data()
            records = self._scan_root(self.root, "enabled", lock_data)
            if include_disabled:
                records.extend(self._scan_root(self.disabled_root, "disabled", lock_data))
            records.sort(key=lambda item: (not item.enabled, item.name.lower(), item.id))
            return {
                "root": str(self.root),
                "disabledRoot": str(self.disabled_root),
                "agentsHome": str(agents_home()),
                "skills": [record.to_dict() for record in records],
                "counts": {
                    "enabled": sum(1 for record in records if record.enabled),
                    "disabled": sum(1 for record in records if not record.enabled),
                    "invalid": sum(1 for record in records if not record.valid),
                },
            }

    def read(self, skill_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._find(skill_id)
            return {
                "skill": record.to_dict(include_body=True),
                "raw": record._content(),
            }

    def _lock_data(self) -> dict[str, dict[str, Any]]:
        path = agents_home() / ".skill-lock.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        skills = data.get("skills")
        return skills if isinstance(skills, dict) else {}

    def _scan_root(self, root: Path, state: str, lock_data: dict[str, dict[str, Any]]) -> list[SkillRecord]:
        records: list[SkillRecord] = []
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            return records
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    skill_file = entry / "SKILL.md"
                    if not skill_file.is_file():
                        continue
                    kind = "directory"
                    target = skill_file
                elif entry.is_file() and entry.suffix.lower() == ".md":
                    kind = "file"
                    target = entry
                else:
                    continue
                record = self._parse(target, state, entry, kind)
                if record:
                    locked = lock_data.get(record.name) or {}
                    if isinstance(locked, dict):
                        record.locked_source = str(locked.get("source") or "") or None
                        record.locked_updated_at = str(locked.get("updatedAt") or "") or None
                    records.append(record)
            except OSError as error:
                logger.warning("Could not inspect skill {}: {}", entry, error)
        return records

    def _parse(self, target: Path, state: str, entry: Path, kind: str) -> SkillRecord | None:
        try:
            if target.stat().st_size > MAX_SKILL_BYTES:
                raise SkillError("SKILL.md 超过 4 MiB，已拒绝读取。")
            raw = target.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            logger.warning("Could not read skill {}: {}", target, error)
            return None

        rel = entry.relative_to(self.root if state == "enabled" else self.disabled_root).as_posix()
        fallback_name = entry.name if kind == "directory" else entry.stem
        try:
            frontmatter, body = split_frontmatter(raw)
            error: str | None = None
        except SkillError as exc:
            frontmatter, body = {}, raw
            error = str(exc)

        name = str(frontmatter.get("name") or fallback_name).strip()
        description = str(frontmatter.get("description") or "").strip()
        when_to_use = str(frontmatter.get("whenToUse") or "").strip()
        valid = bool(
            name
            and description
            and SKILL_NAME_RE.match(name)
            and error is None
        )
        if error is None and not valid:
            if not description:
                error = "frontmatter 缺少 description。"
            elif not SKILL_NAME_RE.match(name):
                error = "frontmatter name 不是 kebab-case。"
        model_invocable = not _bool_value(frontmatter.get("disable-model-invocation"), False)
        user_invocable = _bool_value(frontmatter.get("user-invocable"), True)
        metadata = frontmatter.get("metadata")
        files: list[str] = []
        if kind == "directory":
            for child in sorted(entry.rglob("*")):
                if child.name.startswith(".") or ".git" in child.parts:
                    continue
                try:
                    files.append(child.relative_to(entry).as_posix())
                except ValueError:
                    continue
                if len(files) >= MAX_FILES:
                    break
        stat = target.stat()
        return SkillRecord(
            id=f"{state}:{rel}",
            name=name or fallback_name,
            description=description,
            path=entry,
            kind=kind,
            enabled=state == "enabled",
            rel=rel,
            when_to_use=when_to_use,
            model_invocable=model_invocable,
            user_invocable=user_invocable,
            metadata=metadata if isinstance(metadata, dict) else {},
            frontmatter=frontmatter,
            body=body.strip(),
            valid=valid,
            error=error,
            size=stat.st_size,
            modified=stat.st_mtime,
            files=files,
        )

    # ------------------------------------------------------------------ writing
    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            name = _validate_name(str(payload.get("name") or ""))
            description = str(payload.get("description") or "").strip()
            if not description:
                raise SkillError("description 不能为空。")
            body = str(payload.get("body") or "").strip()
            skill_id = str(payload.get("id") or "").strip()
            when_to_use = str(payload.get("whenToUse") or "").strip()
            model_invocable = bool(payload.get("modelInvocable", True))
            user_invocable = bool(payload.get("userInvocable", True))

            if skill_id:
                record = self._find(skill_id)
                if not self._writable(record.path):
                    raise SkillError("该 skill 是符号链接或位于受管目录之外，拒绝写入。")
                if payload.get("whenToUse") is None:
                    when_to_use = record.when_to_use
                if payload.get("modelInvocable") is None:
                    model_invocable = record.model_invocable
                if payload.get("userInvocable") is None:
                    user_invocable = record.user_invocable
                target = record.skill_file
                extra = {
                    key: value
                    for key, value in record.frontmatter.items()
                    if key
                    not in {
                        "name",
                        "description",
                        "whenToUse",
                        "disable-model-invocation",
                        "user-invocable",
                        "metadata",
                    }
                }
                metadata = record.metadata
            else:
                state_enabled = bool(payload.get("enabled", True))
                root = self.root if state_enabled else self.disabled_root
                target = root / name / "SKILL.md"
                if target.exists() or (root / name).exists():
                    raise SkillError(f"skill 已存在: {name}")
                extra = {}
                metadata = {}
                state = "enabled" if state_enabled else "disabled"
                skill_id = f"{state}:{name}"

            frontmatter = _frontmatter_payload(
                name=name,
                description=description,
                when_to_use=when_to_use,
                model_invocable=model_invocable,
                user_invocable=user_invocable,
                extra=extra,
            )
            if metadata:
                frontmatter["metadata"] = metadata
            text = f"---\n{_yaml_frontmatter(frontmatter)}\n---\n\n{body}\n"
            target.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(target, text)
            logger.info("Saved skill {} at {}", name, target)
            return {"id": skill_id, "name": name, "path": str(target)}

    def set_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            record = self._find(skill_id)
            if record.enabled == enabled:
                return {"id": record.id, "enabled": enabled}
            if not self._writable(record.path):
                raise SkillError("该 skill 是符号链接或位于受管目录之外，拒绝移动。")
            source_root = self.root if record.enabled else self.disabled_root
            target_root = self.root if enabled else self.disabled_root
            rel = record.path.relative_to(source_root)
            target = target_root / rel
            if target.exists():
                raise SkillError(f"目标位置已存在同名条目: {target}")
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(record.path), str(target))
            state = "enabled" if enabled else "disabled"
            logger.info("Skill {} moved to {}", record.name, target)
            return {
                "id": f"{state}:{rel.as_posix()}",
                "name": record.name,
                "enabled": enabled,
                "path": str(target),
            }

    def delete(self, skill_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._find(skill_id)
            if not self._writable(record.path):
                raise SkillError("该 skill 是符号链接或位于受管目录之外，拒绝删除。")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            destination_root = self.trash_root / "skills" / stamp
            destination_root.mkdir(parents=True, exist_ok=True)
            destination = destination_root / record.path.name
            suffix = 2
            while destination.exists():
                destination = destination_root / f"{record.path.name}-{suffix}"
                suffix += 1
            shutil.move(str(record.path), str(destination))
            logger.info("Skill {} moved to trash {}", record.name, destination)
            return {"id": skill_id, "name": record.name, "trash": str(destination)}

    # ------------------------------------------------------------------ helpers
    def _find(self, skill_id: str) -> SkillRecord:
        state, _, rel = skill_id.partition(":")
        if state not in ("enabled", "disabled") or not rel:
            raise SkillError(f"非法 skill id: {skill_id}")
        root = self.root if state == "enabled" else self.disabled_root
        path = root / _safe_rel(rel)
        if not path.exists():
            raise SkillError(f"未找到 skill: {skill_id}")
        kind = "directory" if path.is_dir() else "file"
        target = path / "SKILL.md" if kind == "directory" else path
        if not target.is_file():
            raise SkillError(f"skill 缺少 SKILL.md: {path}")
        record = self._parse(target, state, path, kind)
        if record is None:
            raise SkillError(f"无法读取 skill: {skill_id}")
        return record

    def _writable(self, path: Path) -> bool:
        if path.is_symlink():
            return False
        try:
            resolved = path.resolve()
            for root in (self.root.resolve(), self.disabled_root.resolve()):
                if resolved == root or resolved.is_relative_to(root):
                    return True
        except OSError:
            return False
        return False

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
        temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            temp.write_text(text, encoding="utf-8")
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass
