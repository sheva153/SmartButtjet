import subprocess
import sys
from pathlib import Path


def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
bot:
  timezone: Europe/Kyiv
storage:
  records_file: {tmp_path / "records.csv"}
  notes_file: {tmp_path / "notes.csv"}
  chat_settings_file: {tmp_path / "chat-settings.csv"}
  export_directory: {tmp_path / "exports"}
income:
  categories:
    other: []
""".strip(),
        encoding="utf-8",
    )
    return config


def _run_cli(config: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "main.py", "--config", str(config), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def test_check_config_and_storage_do_not_need_token(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    configured = _run_cli(config, "check-config")
    storage = _run_cli(config, "check-storage")

    assert configured.returncode == 0, configured.stderr
    assert "Europe/Kyiv" in configured.stdout
    assert storage.returncode == 0, storage.stderr
    assert "records=0" in storage.stdout
    assert "notes=0" in storage.stdout
    assert "chat_settings=0" in storage.stdout


def test_parse_prints_multi_label_result(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    result = _run_cli(config, "parse", "отримав 500 грн")

    assert result.returncode == 0, result.stderr
    assert '"amount":"500.00"' in result.stdout
    assert '"categories":["other"]' in result.stdout
    assert '"tags":[]' in result.stdout


def test_analytics_and_export_require_chat_id(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    analytics = _run_cli(config, "analytics")
    export = _run_cli(config, "export")

    assert analytics.returncode == 2
    assert "--chat-id" in analytics.stderr
    assert export.returncode == 2
    assert "--chat-id" in export.stderr
