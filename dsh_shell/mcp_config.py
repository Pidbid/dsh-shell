"""Read and edit deepseek-harness MCP entries in ``cordis.patch.yml``.

The official MCP surface of DeepSeek Harness is the profile patch layer:
``$DSH_HOME/profiles/<profile>/cordis.patch.yml`` is a top-level YAML list of
loader entries.  One entry per MCP server uses ``@deepseek-ai/dsh-mcp-client``::

    - id: mcp-github
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: github
        transport: stdio
        command: npx
        args: ['-y', '@modelcontextprotocol/server-github']

The file is hot-reloaded by the ``web`` profile (``patchReload: live``), so
writes take effect without restarting the harness.  Comments and unknown keys
are preserved via ruamel.yaml round-trip mode.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from loguru import logger
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar

from .paths import dsh_home, profile_patch_file

MCP_PLUGIN_NAME = "@deepseek-ai/dsh-mcp-client"
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
ENTRY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
TRANSPORTS = ("stdio", "streamable-http")

_HEADER = """\
# dsh-shell — MCP servers for this DeepSeek Harness profile.
# Each entry is an official @deepseek-ai/dsh-mcp-client instance.
[]
"""


class McpConfigError(RuntimeError):
    """Invalid MCP configuration or unsupported patch file state."""


def _new_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=2, offset=0)
    return yaml


def _plain(value: Any) -> Any:
    """Convert ruamel containers to plain Python for the JSON API."""
    if isinstance(value, TaggedScalar):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _as_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    return {}


def _entry_is_mcp(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    name = str(entry.get("name") or "")
    return name == MCP_PLUGIN_NAME or name.endswith("/dsh-mcp-client")


def _entry_config(entry: dict[str, Any]) -> dict[str, Any]:
    config = entry.get("config")
    return config if isinstance(config, dict) else {}


def _tagged_keys(mapping: Any) -> list[str]:
    if not isinstance(mapping, dict):
        return []
    return [str(key) for key, value in mapping.items() if isinstance(value, TaggedScalar)]


def _set_scalar(mapping: CommentedMap, key: str, value: Any) -> None:
    """Set a value while retaining an untouched ``!!js`` tagged scalar."""
    if value in (None, ""):
        if key in mapping:
            del mapping[key]
        return
    old = mapping.get(key)
    if isinstance(old, TaggedScalar) and str(old) == str(value):
        return
    mapping[key] = value


def _set_mapping(mapping: CommentedMap, key: str, values: dict[str, str]) -> None:
    if not values:
        if key in mapping:
            del mapping[key]
        return
    current = mapping.get(key)
    if not isinstance(current, CommentedMap):
        current = CommentedMap()
        mapping[key] = current
    for existing_key in list(current.keys()):
        if str(existing_key) not in values:
            del current[existing_key]
    for item_key, item_value in values.items():
        _set_scalar(current, item_key, item_value)


class McpConfigManager:
    """Thread-safe CRUD over the active profile's patch layer."""

    def __init__(self, profile: str | None = None, path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path_override = Path(path).expanduser() if path else None
        self.profile = profile or self.default_profile()

    @staticmethod
    def profiles() -> list[str]:
        root = dsh_home() / "profiles"
        profiles: list[str] = []
        try:
            for child in root.iterdir():
                if not child.is_dir() or child.name in ("node_modules", ".bin"):
                    continue
                if (child / "cordis.yml").is_file() or (child / "cordis.patch.yml").is_file():
                    profiles.append(child.name)
        except OSError:
            return []
        return sorted(profiles)

    @classmethod
    def default_profile(cls) -> str:
        profiles = cls.profiles()
        if "web" in profiles:
            return "web"
        return profiles[0] if profiles else "web"

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        return profile_patch_file(self.profile)

    def set_profile(self, profile: str) -> None:
        if not PROFILE_RE.match(profile):
            raise McpConfigError(f"非法 profile 名称: {profile}")
        with self._lock:
            if profile not in self.profiles():
                raise McpConfigError(f"未找到 profile: {profile}")
            self.profile = profile

    def ensure_file(self) -> Path:
        path = self.path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_HEADER, encoding="utf-8")
            if os.name == "posix":
                os.chmod(path, 0o600)
        return path

    # ------------------------------------------------------------------ reading
    def list(self) -> dict[str, Any]:
        with self._lock:
            path = self.ensure_file()
            raw = path.read_text(encoding="utf-8", errors="replace")
            data = self._load(raw)
            entries: list[dict[str, Any]] = []
            for index, entry in enumerate(data):
                if not _entry_is_mcp(entry):
                    continue
                entries.append(self._entry_to_dict(index, entry))
            return {
                "profile": self.profile,
                "profiles": self.profiles(),
                "path": str(path),
                "raw": raw,
                "entries": entries,
            }

    def raw(self) -> str:
        with self._lock:
            return self.ensure_file().read_text(encoding="utf-8", errors="replace")

    def _load(self, raw: str) -> CommentedSeq:
        yaml = _new_yaml()
        try:
            data = yaml.load(raw) if raw.strip() else None
        except Exception as error:  # ruamel raises a family of parser errors
            raise McpConfigError(f"YAML 解析失败: {error}") from error
        if data is None:
            data = CommentedSeq()
        if not isinstance(data, list):
            raise McpConfigError("cordis.patch.yml 顶层必须是 YAML 列表。")
        return data

    def _entry_to_dict(self, index: int, entry: dict[str, Any]) -> dict[str, Any]:
        config = _entry_config(entry)
        transport = str(config.get("transport") or "").strip()
        reconnect = config.get("reconnect")
        return {
            "index": index,
            "id": str(entry.get("id") or ""),
            "name": str(entry.get("name") or ""),
            "enabled": not _as_bool(entry.get("disabled"), False),
            "serverName": str(config.get("serverName") or ""),
            "transport": transport,
            "command": str(config.get("command") or ""),
            "args": _as_str_list(config.get("args")),
            "env": _as_mapping(config.get("env")),
            "cwd": str(config.get("cwd") or ""),
            "url": str(config.get("url") or ""),
            "headers": _as_mapping(config.get("headers")),
            "toolCallTimeoutMs": _plain(config.get("toolCallTimeoutMs")),
            "maxInstructionBytes": _plain(config.get("maxInstructionBytes")),
            "failOnStartupError": _as_bool(config.get("failOnStartupError"), False),
            "reconnect": _plain(reconnect) if isinstance(reconnect, dict) else {},
            "taggedEnvKeys": _tagged_keys(config.get("env")),
            "taggedHeaderKeys": _tagged_keys(config.get("headers")),
            "unknownConfigKeys": sorted(
                str(key)
                for key in config
                if key
                not in {
                    "serverName",
                    "transport",
                    "command",
                    "args",
                    "env",
                    "cwd",
                    "url",
                    "headers",
                    "toolCallTimeoutMs",
                    "maxInstructionBytes",
                    "failOnStartupError",
                    "reconnect",
                }
            ),
        }

    # ------------------------------------------------------------------ writing
    def save_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self._load(self.raw())
            entry_id = str(payload.get("id") or "").strip()
            existing_index = self._find_entry_index(data, entry_id)

            target_id = entry_id or self._unique_entry_id(
                data, str(payload.get("serverName") or "mcp")
            )
            if not ENTRY_ID_RE.match(target_id):
                raise McpConfigError("id 只能包含字母、数字、_、-、.、:。")
            if existing_index is None and any(
                str(item.get("id") or "") == target_id for item in data if isinstance(item, dict)
            ):
                raise McpConfigError(f"id 已存在: {target_id}")

            config = self._validated_config(payload)
            self._validate_unique_server_name(data, config["serverName"], existing_index)

            if existing_index is None:
                entry = CommentedMap()
                entry["id"] = target_id
                entry["name"] = MCP_PLUGIN_NAME
                entry["config"] = CommentedMap()
                data.append(entry)
            else:
                entry = data[existing_index]
                if not isinstance(entry, CommentedMap):
                    entry = CommentedMap(entry)
                    data[existing_index] = entry
                entry["id"] = target_id
                entry["name"] = MCP_PLUGIN_NAME

            if _as_bool(payload.get("enabled"), True):
                entry.pop("disabled", None)
            else:
                entry["disabled"] = True

            entry_config = entry.get("config")
            if not isinstance(entry_config, CommentedMap):
                entry_config = CommentedMap()
                entry["config"] = entry_config
            known = {
                "serverName",
                "transport",
                "command",
                "args",
                "env",
                "cwd",
                "url",
                "headers",
                "toolCallTimeoutMs",
                "maxInstructionBytes",
                "failOnStartupError",
                "reconnect",
            }
            old_env = entry_config.get("env")
            old_headers = entry_config.get("headers")
            for key in list(entry_config.keys()):
                if str(key) in known:
                    del entry_config[key]
            # Keep unknown keys untouched; they may belong to a newer harness.
            entry_config["serverName"] = config["serverName"]
            entry_config["transport"] = config["transport"]
            if config["transport"] == "stdio":
                entry_config["command"] = config["command"]
                if config["args"]:
                    entry_config["args"] = config["args"]
                if isinstance(old_env, CommentedMap):
                    entry_config["env"] = old_env
                _set_mapping(entry_config, "env", config["env"])
                if config["cwd"]:
                    entry_config["cwd"] = config["cwd"]
            else:
                entry_config["url"] = config["url"]
                if isinstance(old_headers, CommentedMap):
                    entry_config["headers"] = old_headers
                _set_mapping(entry_config, "headers", config["headers"])
            if config["toolCallTimeoutMs"] is not None:
                entry_config["toolCallTimeoutMs"] = config["toolCallTimeoutMs"]
            if config["maxInstructionBytes"] is not None:
                entry_config["maxInstructionBytes"] = config["maxInstructionBytes"]
            if config["failOnStartupError"]:
                entry_config["failOnStartupError"] = True
            if config["reconnect"]:
                reconnect = CommentedMap()
                for key, value in config["reconnect"].items():
                    reconnect[key] = value
                entry_config["reconnect"] = reconnect
            self._write(data)
            return {"id": target_id, "path": str(self.path), "profile": self.profile}

    def delete_entry(self, entry_id: str) -> dict[str, Any]:
        with self._lock:
            data = self._load(self.raw())
            index = self._find_entry_index(data, entry_id)
            if index is None:
                raise McpConfigError(f"未找到 MCP 条目: {entry_id}")
            del data[index]
            self._write(data)
            return {"id": entry_id, "path": str(self.path)}

    def set_enabled(self, entry_id: str, enabled: bool) -> dict[str, Any]:
        with self._lock:
            data = self._load(self.raw())
            index = self._find_entry_index(data, entry_id)
            if index is None:
                raise McpConfigError(f"未找到 MCP 条目: {entry_id}")
            entry = data[index]
            if enabled:
                entry.pop("disabled", None)
            else:
                entry["disabled"] = True
            self._write(data)
            return {"id": entry_id, "enabled": enabled, "path": str(self.path)}

    def save_raw(self, text: str) -> dict[str, Any]:
        with self._lock:
            data = self._load(text)
            seen_ids: set[str] = set()
            seen_names: dict[str, str] = {}
            for entry in data:
                if not _entry_is_mcp(entry):
                    continue
                entry_id = str(entry.get("id") or "")
                config = _entry_config(entry)
                server_name = str(config.get("serverName") or "")
                if not entry_id:
                    raise McpConfigError("MCP 条目缺少 id。")
                if entry_id in seen_ids:
                    raise McpConfigError(f"重复的 MCP id: {entry_id}")
                seen_ids.add(entry_id)
                if not SERVER_NAME_RE.match(server_name):
                    raise McpConfigError(f"{entry_id}: serverName 不合法（{SERVER_NAME_RE.pattern}）。")
                if not _as_bool(entry.get("disabled"), False):
                    if server_name in seen_names:
                        raise McpConfigError(
                            f"启用的 MCP 条目 serverName 重复: {server_name} "
                            f"({seen_names[server_name]} / {entry_id})"
                        )
                    seen_names[server_name] = entry_id
                transport = str(config.get("transport") or "")
                if transport not in TRANSPORTS:
                    raise McpConfigError(f"{entry_id}: transport 必须是 stdio 或 streamable-http。")
                if transport == "stdio" and not config.get("command"):
                    raise McpConfigError(f"{entry_id}: stdio 需要 command。")
                if transport == "streamable-http" and not config.get("url"):
                    raise McpConfigError(f"{entry_id}: streamable-http 需要 url。")
            self._write_text(text if text.endswith("\n") else f"{text}\n")
            return {"path": str(self.path), "profile": self.profile}

    # ------------------------------------------------------------------ helpers
    def _validated_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        server_name = str(payload.get("serverName") or "").strip()
        if not SERVER_NAME_RE.match(server_name):
            raise McpConfigError("serverName 必须匹配 [A-Za-z0-9_-]{1,32}。")
        transport = str(payload.get("transport") or "").strip()
        if transport not in TRANSPORTS:
            raise McpConfigError("transport 必须是 stdio 或 streamable-http。")
        command = str(payload.get("command") or "").strip()
        url = str(payload.get("url") or "").strip()
        if transport == "stdio" and not command:
            raise McpConfigError("stdio 模式必须填写 command。")
        if transport == "streamable-http" and not url:
            raise McpConfigError("streamable-http 模式必须填写 url。")

        timeout = payload.get("toolCallTimeoutMs")
        if timeout in ("", None):
            timeout = None
        else:
            try:
                timeout = int(timeout)
            except (TypeError, ValueError) as error:
                raise McpConfigError("toolCallTimeoutMs 必须是整数。") from error
            if timeout <= 0:
                raise McpConfigError("toolCallTimeoutMs 必须大于 0。")

        max_instruction = payload.get("maxInstructionBytes")
        if max_instruction in ("", None):
            max_instruction = None
        else:
            try:
                max_instruction = int(max_instruction)
            except (TypeError, ValueError) as error:
                raise McpConfigError("maxInstructionBytes 必须是整数。") from error
            if max_instruction <= 0:
                raise McpConfigError("maxInstructionBytes 必须大于 0。")

        reconnect_payload = payload.get("reconnect")
        reconnect: dict[str, Any] = {}
        if isinstance(reconnect_payload, dict):
            for key in ("enabled", "initialDelayMs", "maxDelayMs", "maxAttempts"):
                if reconnect_payload.get(key) in ("", None):
                    continue
                if key == "enabled":
                    reconnect[key] = bool(reconnect_payload[key])
                else:
                    try:
                        value = int(reconnect_payload[key])
                    except (TypeError, ValueError) as error:
                        raise McpConfigError(f"reconnect.{key} 必须是整数。") from error
                    if value <= 0:
                        raise McpConfigError(f"reconnect.{key} 必须大于 0。")
                    reconnect[key] = value

        return {
            "serverName": server_name,
            "transport": transport,
            "command": command,
            "args": _as_str_list(payload.get("args")),
            "env": _as_mapping(payload.get("env")),
            "cwd": str(payload.get("cwd") or "").strip(),
            "url": url,
            "headers": _as_mapping(payload.get("headers")),
            "toolCallTimeoutMs": timeout,
            "maxInstructionBytes": max_instruction,
            "failOnStartupError": bool(payload.get("failOnStartupError")),
            "reconnect": reconnect,
        }

    def _validate_unique_server_name(
        self,
        data: CommentedSeq,
        server_name: str,
        exclude_index: int | None,
    ) -> None:
        for index, entry in enumerate(data):
            if index == exclude_index or not _entry_is_mcp(entry):
                continue
            if _as_bool(entry.get("disabled"), False):
                continue
            if str(_entry_config(entry).get("serverName") or "") == server_name:
                raise McpConfigError(
                    f"已启用的 MCP 条目已使用 serverName: {server_name} "
                    f"(id={entry.get('id')})"
                )

    def _find_entry_index(self, data: CommentedSeq, entry_id: str) -> int | None:
        if not entry_id:
            return None
        for index, entry in enumerate(data):
            if _entry_is_mcp(entry) and str(entry.get("id") or "") == entry_id:
                return index
        return None

    def _unique_entry_id(self, data: CommentedSeq, server_name: str) -> str:
        base = "mcp-" + re.sub(r"[^A-Za-z0-9_-]+", "-", server_name).strip("-").lower()
        if not base or base == "mcp-":
            base = "mcp-server"
        existing = {str(entry.get("id") or "") for entry in data if isinstance(entry, dict)}
        candidate = base
        suffix = 2
        while candidate in existing:
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def _write(self, data: CommentedSeq) -> None:
        yaml = _new_yaml()
        import io

        stream = io.StringIO()
        yaml.dump(data, stream)
        self._write_text(stream.getvalue())

    def _write_text(self, text: str) -> None:
        path = self.ensure_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            except OSError as error:
                logger.warning("Could not back up {}: {}", path, error)
        temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            temp.write_text(text, encoding="utf-8")
            if os.name == "posix":
                os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass
        logger.info("Updated MCP patch file {} ({} bytes)", path, len(text))
