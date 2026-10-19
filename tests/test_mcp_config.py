from __future__ import annotations

from pathlib import Path

import pytest

from dsh_shell.mcp_config import McpConfigError, McpConfigManager

SAMPLE = """\
# keep this comment
- id: mcp-github
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: github
    transport: stdio
    command: npx
    args: ['-y', '@modelcontextprotocol/server-github']
    env:
      GITHUB_TOKEN: !!js process.env.GITHUB_TOKEN
- id: unrelated
  name: some-other-plugin
  config:
    keep: true
"""


def manager(tmp_path: Path) -> tuple[McpConfigManager, Path]:
    path = tmp_path / "cordis.patch.yml"
    path.write_text(SAMPLE, encoding="utf-8")
    return McpConfigManager(profile="web", path=path), path


def test_list_reads_only_mcp_entries(tmp_path: Path) -> None:
    config, _ = manager(tmp_path)
    data = config.list()
    assert data["profile"] == "web"
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["id"] == "mcp-github"
    assert entry["serverName"] == "github"
    assert entry["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert entry["env"] == {"GITHUB_TOKEN": "process.env.GITHUB_TOKEN"}
    assert entry["taggedEnvKeys"] == ["GITHUB_TOKEN"]


def test_edit_preserves_comments_tags_and_unrelated_entries(tmp_path: Path) -> None:
    config, path = manager(tmp_path)
    entry = config.list()["entries"][0]
    config.save_entry({**entry, "args": ["-y", "server-github", "--verbose"]})
    text = path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert "!!js process.env.GITHUB_TOKEN" in text
    assert "some-other-plugin" in text
    assert "id: unrelated" in text
    assert "--verbose" in text


def test_create_toggle_and_delete(tmp_path: Path) -> None:
    config, path = manager(tmp_path)
    result = config.save_entry(
        {
            "serverName": "web",
            "transport": "streamable-http",
            "url": "http://localhost:3000/mcp",
            "headers": {"Authorization": "Bearer abc"},
            "enabled": True,
        }
    )
    assert result["id"] == "mcp-web"
    entries = config.list()["entries"]
    assert [item["serverName"] for item in entries] == ["github", "web"]
    assert "streamable-http" in path.read_text(encoding="utf-8")

    config.set_enabled("mcp-web", False)
    web = next(item for item in config.list()["entries"] if item["id"] == "mcp-web")
    assert web["enabled"] is False
    assert "disabled: true" in path.read_text(encoding="utf-8")

    config.delete_entry("mcp-web")
    assert [item["id"] for item in config.list()["entries"]] == ["mcp-github"]


def test_duplicate_server_name_rejected(tmp_path: Path) -> None:
    config, _ = manager(tmp_path)
    with pytest.raises(McpConfigError, match="serverName"):
        config.save_entry(
            {
                "serverName": "github",
                "transport": "stdio",
                "command": "uvx",
                "enabled": True,
            }
        )


def test_validation_rejects_missing_command_and_bad_name(tmp_path: Path) -> None:
    config, _ = manager(tmp_path)
    with pytest.raises(McpConfigError):
        config.save_entry({"serverName": "bad name", "transport": "stdio", "command": "x"})
    with pytest.raises(McpConfigError):
        config.save_entry({"serverName": "ok", "transport": "stdio", "command": ""})
    with pytest.raises(McpConfigError):
        config.save_entry({"serverName": "ok", "transport": "streamable-http", "url": ""})


def test_raw_save_preserves_text_and_validates(tmp_path: Path) -> None:
    config, path = manager(tmp_path)
    raw = config.raw() + "- id: mcp-extra\n  name: '@deepseek-ai/dsh-mcp-client'\n  config:\n    serverName: extra\n    transport: stdio\n    command: uvx\n"
    config.save_raw(raw)
    assert path.read_text(encoding="utf-8") == raw
    assert len(config.list()["entries"]) == 2

    with pytest.raises(McpConfigError, match="顶层必须是 YAML 列表"):
        config.save_raw("config: {}\n")
    with pytest.raises(McpConfigError, match="重复的 MCP id"):
        config.save_raw(
            "- id: dup\n  name: '@deepseek-ai/dsh-mcp-client'\n  config: {serverName: a, transport: stdio, command: x}\n"
            "- id: dup\n  name: '@deepseek-ai/dsh-mcp-client'\n  config: {serverName: b, transport: stdio, command: x}\n"
        )


def test_missing_file_is_created_with_mcp_header(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "cordis.patch.yml"
    config = McpConfigManager(profile="web", path=path)
    data = config.list()
    assert data["entries"] == []
    assert path.is_file()
    assert path.read_text(encoding="utf-8").lstrip().startswith("# dsh-shell")
