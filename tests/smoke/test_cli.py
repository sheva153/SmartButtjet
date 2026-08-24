import subprocess
import sys
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from income_stats.config import load_config
from income_stats.models import IncomeRecord, RecordNote
from income_stats.repositories import CsvRecordsRepository
from income_stats.services import AnalyticsService


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


def _seed_scoped_records(config_path: Path) -> CsvRecordsRepository:
    repository = CsvRecordsRepository(load_config(config_path).storage)
    for record in (
        IncomeRecord(
            id="included-record",
            telegram_message_id=1,
            chat_id=-100,
            user_id=7,
            original_text="included income",
            amount=Decimal("500"),
            currency="UAH",
            income_date=date(2026, 8, 1),
            updated_by=7,
        ),
        IncomeRecord(
            id="excluded-record",
            telegram_message_id=2,
            chat_id=-200,
            user_id=8,
            original_text="excluded income",
            amount=Decimal("9999"),
            currency="UAH",
            income_date=date(2026, 8, 1),
            updated_by=8,
        ),
    ):
        repository.create_record_sync(record)
    return repository


def test_check_config_and_storage_do_not_need_token(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    configured = _run_cli(config, "check-config")
    storage = _run_cli(config, "check-storage")

    assert configured.returncode == 0, configured.stderr
    assert configured.stdout.strip() == "Configuration is valid."
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


def test_analytics_is_scoped_to_explicit_chat(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    _seed_scoped_records(config)

    result = _run_cli(config, "analytics", "--chat-id", "-100", "--period", "all")

    assert result.returncode == 0, result.stderr
    assert "Записів: 1" in result.stdout
    assert "UAH: Дохід 500.00 · Витрати 0.00 · Чистими 500.00" in result.stdout
    assert "9,999.00" not in result.stdout


def test_analytics_accepts_last_month_period(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    _seed_scoped_records(config)

    result = _run_cli(
        config, "analytics", "--chat-id", "-100", "--period", "last_month"
    )

    assert result.returncode == 0, result.stderr


def test_export_creates_chat_scoped_zip(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    repository = _seed_scoped_records(config)
    repository.add_note_sync(
        RecordNote(
            id="included-note",
            record_id="included-record",
            user_id=7,
            text="included note",
        )
    )
    repository.add_note_sync(
        RecordNote(
            id="excluded-note",
            record_id="excluded-record",
            user_id=8,
            text="excluded note",
        )
    )

    result = _run_cli(config, "export", "--chat-id", "-100")

    assert result.returncode == 0, result.stderr
    archive = Path(result.stdout.strip())
    assert archive.exists()
    assert archive.parent == tmp_path / "exports"
    assert archive.suffix == ".zip"
    with zipfile.ZipFile(archive) as bundle:
        records = bundle.read("records.csv").decode()
        notes = bundle.read("record_notes.csv").decode()
    assert "included-record" in records
    assert "excluded-record" not in records
    assert "included note" in notes
    assert "excluded note" not in notes


async def test_chart_generates_self_contained_html(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    config.analytics.static_preview = False
    repository = _seed_scoped_records(config_path)
    service = AnalyticsService(
        repository,
        config.analytics,
        config.storage,
        timezone=config.bot.timezone,
        fun_summary=config.fun_summary,
    )

    artifacts = await service.build_chart_artifacts(-100, "all")

    try:
        assert artifacts.png is None
        assert artifacts.html is not None
        html = artifacts.html.read_text(encoding="utf-8").casefold()
        assert "<html" in html
        assert "plotly.js" in html
    finally:
        if artifacts.png is not None:
            artifacts.png.unlink(missing_ok=True)
        if artifacts.html is not None:
            artifacts.html.unlink(missing_ok=True)


async def test_chart_generates_png_report(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    config.analytics.interactive_html = False
    repository = _seed_scoped_records(config_path)
    service = AnalyticsService(
        repository,
        config.analytics,
        config.storage,
        timezone=config.bot.timezone,
        fun_summary=config.fun_summary,
    )

    artifacts = await service.build_chart_artifacts(
        -100, "month", today=date(2026, 8, 15)
    )

    try:
        assert artifacts.html is None
        assert artifacts.png is not None
        assert artifacts.png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        if artifacts.png is not None:
            artifacts.png.unlink(missing_ok=True)


def test_records_and_retag_return_zero_on_empty_store(tmp_path: Path) -> None:
    config = _write_config(tmp_path)

    records = _run_cli(config, "records")
    retag = _run_cli(config, "retag")

    assert records.returncode == 0, records.stderr
    assert records.stdout == ""
    assert retag.returncode == 0, retag.stderr
    assert retag.stdout.strip() == "оновлено 0 з 0"


def test_records_and_retag_are_scoped_to_chat_id(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    _seed_scoped_records(config)

    records = _run_cli(config, "records", "--chat-id", "-100")
    retag = _run_cli(config, "retag", "--chat-id", "-100")

    assert records.returncode == 0, records.stderr
    assert "included" in records.stdout
    assert "excluded" not in records.stdout
    assert retag.returncode == 0, retag.stderr
    assert retag.stdout.strip() == "оновлено 0 з 1"


def test_retag_reports_added_tags_per_record(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8") + "\n  tags:\n    card: [картку]\n",
        encoding="utf-8",
    )
    repository = CsvRecordsRepository(load_config(config).storage)
    repository.create_record_sync(
        IncomeRecord(
            id="untagged-record",
            telegram_message_id=1,
            chat_id=-100,
            user_id=7,
            original_text="оплата на картку",
            amount=Decimal("500"),
            currency="UAH",
            income_date=date(2026, 8, 1),
            updated_by=7,
        )
    )

    result = _run_cli(config, "retag")

    assert result.returncode == 0, result.stderr
    assert "untagged" in result.stdout
    assert "+[card]" in result.stdout
    assert "оновлено 1 з 1" in result.stdout


def test_chats_lists_distinct_chat_ids_with_summary(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    _seed_scoped_records(config)

    result = _run_cli(config, "chats")

    assert result.returncode == 0, result.stderr
    assert "-100" in result.stdout
    assert "-200" in result.stdout
    assert "labels: other" in result.stdout


def test_justfile_invokes_uv_directly() -> None:
    contents = Path("justfile").read_text(encoding="utf-8")

    assert "python -m uv" not in contents
    assert "uv sync" in contents
    assert "uv run pytest" in contents
