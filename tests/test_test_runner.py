from pathlib import Path

from specialist_workflow.test_runner import detect_test_command, run_tests


def test_detects_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    assert detect_test_command(tmp_path) == "python -m pytest"


def test_run_tests_reports_failure(tmp_path: Path) -> None:
    report = run_tests(tmp_path, 'python -c "raise SystemExit(3)"', timeout=10)
    assert report["passed"] is False
    assert report["exit_code"] == 3

