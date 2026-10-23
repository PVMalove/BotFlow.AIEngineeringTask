"""Voice jobs: limit data, ports and the 0004 schema (SQL runs in the smoke)."""

import importlib.util
import io
from pathlib import Path
from types import ModuleType

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import CheckConstraint

from domain.entities.voice_message import VoiceMessage
from domain.job_limits import JobLimits
from domain.value_objects.job_status import JobKind, VoiceStage
from infrastructure.db.models import Base

jobs = Base.metadata.tables["jobs"]
voice_jobs = Base.metadata.tables["voice_jobs"]
VERSIONS = Path(__file__).parents[2] / "src/infrastructure/db/alembic/versions"


def migration() -> ModuleType:
    path = VERSIONS / "0004_voice_jobs.py"
    spec = importlib.util.spec_from_file_location("migration_0004", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def upgrade_sql() -> str:
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        migration().upgrade()
    return " ".join(buffer.getvalue().split())


def downgrade_sql() -> str:
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        migration().downgrade()
    return " ".join(buffer.getvalue().split())


def test_the_common_limits_stay_and_voice_has_its_own_ten_and_two() -> None:
    limits = JobLimits()

    assert (limits.total, limits.per_author) == (100, 10)
    assert (limits.voice_total, limits.voice_per_author) == (10, 2)


def test_a_job_is_text_or_voice_and_a_voice_job_moves_through_four_stages() -> None:
    assert [kind.value for kind in JobKind] == ["text", "voice"]
    assert [stage.value for stage in VoiceStage] == [
        "queued",
        "stored",
        "done",
        "failed",
    ]


def test_a_voice_message_carries_only_the_telegram_file_identity() -> None:
    names = set(VoiceMessage.__dataclass_fields__)

    assert {"file_id", "file_unique_id", "duration_seconds"} <= names
    assert "text" not in names
    assert not {n for n in names if "url" in n or "path" in n or "token" in n}


def test_the_job_table_allows_a_missing_text_only_for_voice() -> None:
    assert jobs.c.kind.nullable is False
    assert jobs.c.text.nullable is True
    checks = {
        c.name: str(c.sqltext)
        for c in jobs.constraints
        if isinstance(c, CheckConstraint)
    }
    assert "voice" in checks["jobs_text_present"]
    assert "text IS NOT NULL" in checks["jobs_text_present"]


def test_the_voice_table_keeps_the_latency_marks_and_the_stage() -> None:
    names = set(voice_jobs.c.keys())

    assert {
        "job_id",
        "file_id",
        "file_unique_id",
        "duration_seconds",
        "file_size_bytes",
        "mime_type",
        "stage",
        "s3_key",
        "audio_bytes",
        "audio_sha256",
        "language",
        "error_code",
        "attempts",
        "cold_start",
        "started_at",
        "stored_at",
        "stt_started_at",
        "stt_finished_at",
    } <= names
    assert voice_jobs.c.job_id.primary_key
    assert {fk.column.table.name for fk in voice_jobs.c.job_id.foreign_keys} == {"jobs"}


def test_migration_0004_follows_0003() -> None:
    module = migration()

    assert (module.revision, module.down_revision) == ("0004", "0003")


def test_migration_0004_creates_the_voice_schema() -> None:
    sql = upgrade_sql()

    assert "ALTER TABLE jobs ADD COLUMN kind TEXT DEFAULT 'text' NOT NULL" in sql
    assert "ALTER TABLE jobs ALTER COLUMN text DROP NOT NULL" in sql
    assert "CHECK (kind = 'voice' OR text IS NOT NULL)" in sql
    assert "CREATE TABLE voice_jobs" in sql
    assert "CREATE INDEX voice_jobs_unfinished ON voice_jobs" in sql
    assert "stage IN ('queued', 'stored', 'done', 'failed')" in sql


def test_migration_0004_downgrade_preserves_voice_job_data() -> None:
    sql = downgrade_sql()

    assert "DROP TABLE voice_jobs" in sql
    assert "ALTER TABLE jobs DROP CONSTRAINT jobs_text_present" in sql
    assert "ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check" in sql
    assert "ALTER TABLE jobs ALTER COLUMN text SET NOT NULL" in sql
    assert "ALTER TABLE jobs DROP COLUMN kind" in sql
    assert "UPDATE jobs SET text = COALESCE(" in sql
    assert "[Голосовое сообщение: '" in sql
