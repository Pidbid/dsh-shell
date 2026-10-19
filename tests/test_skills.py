from __future__ import annotations

from pathlib import Path

import pytest

from dsh_shell.skills import SkillError, SkillsManager


def make_manager(tmp_path: Path) -> SkillsManager:
    root = tmp_path / "skills"
    disabled = tmp_path / "skills-disabled"
    root.mkdir()
    return SkillsManager(root, disabled, trash_root=tmp_path / "trash")


def write_skill(root: Path, name: str, body: str = "# Demo\n") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Description for {name}\nwhenToUse: testing\nlicense: MIT\n---\n\n{body}",
        encoding="utf-8",
    )
    return folder


def test_list_directory_and_flat_skills(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    write_skill(manager.root, "demo")
    (manager.root / "flat.md").write_text(
        "---\nname: flat\ndescription: Flat skill\n---\nbody\n", encoding="utf-8"
    )
    data = manager.list()
    assert data["counts"]["enabled"] == 2
    names = [item["name"] for item in data["skills"]]
    assert names == ["demo", "flat"]


def test_create_edit_preserves_extra_frontmatter(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    created = manager.save(
        {
            "name": "new-skill",
            "description": "A new skill",
            "whenToUse": "when testing",
            "body": "# New\n",
            "modelInvocable": False,
            "userInvocable": False,
        }
    )
    assert created["id"] == "enabled:new-skill"
    text = (manager.root / "new-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
    assert "user-invocable: false" in text
    assert "whenToUse: when testing" in text

    write_skill(manager.root, "demo")
    manager.save({"id": "enabled:demo", "name": "demo", "description": "Changed", "body": "# Changed"})
    edited = (manager.root / "demo" / "SKILL.md").read_text(encoding="utf-8")
    assert "description: Changed" in edited
    assert "whenToUse: testing" in edited
    assert "license: MIT" in edited


def test_toggle_moves_between_roots(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    write_skill(manager.root, "demo")
    result = manager.set_enabled("enabled:demo", False)
    assert result["id"] == "disabled:demo"
    assert not (manager.root / "demo").exists()
    assert (manager.disabled_root / "demo" / "SKILL.md").is_file()
    assert manager.list()["skills"][0]["enabled"] is False

    manager.set_enabled("disabled:demo", True)
    assert (manager.root / "demo" / "SKILL.md").is_file()
    assert manager.list()["skills"][0]["enabled"] is True


def test_delete_moves_to_trash(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    write_skill(manager.root, "demo")
    result = manager.delete("enabled:demo")
    assert not (manager.root / "demo").exists()
    trash = Path(result["trash"])
    assert trash.is_dir()
    assert (trash / "SKILL.md").is_file()


def test_invalid_skills_are_reported_not_hidden(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    (manager.root / "broken").mkdir()
    (manager.root / "broken" / "SKILL.md").write_text("---\nname: broken\n---\n", encoding="utf-8")
    data = manager.list()
    assert data["counts"]["invalid"] == 1
    skill = data["skills"][0]
    assert skill["valid"] is False
    assert "description" in skill["error"]


@pytest.mark.parametrize("name", ["Bad Name", "UPPER", "-bad", "bad-", "bad--name", "../escape"])
def test_invalid_names_rejected(tmp_path: Path, name: str) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(SkillError):
        manager.save({"name": name, "description": "x", "body": ""})


def test_missing_description_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(SkillError):
        manager.save({"name": "ok", "description": "", "body": ""})


def test_unknown_id_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    with pytest.raises(SkillError):
        manager.read("enabled:missing")
