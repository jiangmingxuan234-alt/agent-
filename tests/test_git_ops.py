from pathlib import Path

import pytest

from specialist_workflow.git_ops import (
    is_documentation_path,
    safe_workspace_path,
    write_document,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", True),
        ("docs/guide.txt", True),
        ("guide.mdx", True),
        ("src/app.py", False),
        ("package.json", False),
        (".env", False),
    ],
)
def test_documentation_path_policy(path: str, expected: bool) -> None:
    assert is_documentation_path(path) is expected


def test_safe_workspace_path_blocks_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_workspace_path(tmp_path, "../outside.md")


def test_document_writer_blocks_source_code(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_document(tmp_path, "src/app.py", "print('no')")


def test_document_writer_writes_markdown(tmp_path: Path) -> None:
    write_document(tmp_path, "docs/usage.md", "# Usage")
    assert (tmp_path / "docs" / "usage.md").read_text(encoding="utf-8") == "# Usage\n"

