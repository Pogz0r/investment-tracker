import os

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import JSON

from research import config


metadata = MetaData()
json_type = JSON().with_variant(postgresql.JSONB, "postgresql")

podcasts = Table(
    "podcasts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("youtube_url", Text, nullable=False, unique=True),
    Column("video_id", Text, nullable=False, unique=True),
    Column("title", Text),
    Column("transcript", Text, nullable=False),
    Column("transcript_hash", Text, nullable=False, unique=True),
    Column("transcript_source", Text),
    Column("word_count", Integer),
    Column("fetched_at", DateTime(timezone=True), server_default=func.now()),
    Index("podcasts_hash_idx", "transcript_hash"),
)

pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("podcast_id", Integer, ForeignKey("podcasts.id")),
    Column("youtube_url", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="queued"),
    Column("current_stage", Text),
    Column("progress", json_type, server_default="{}"),
    Column("stage1_output", Text),
    Column("stage2_output", Text),
    Column("stage3_plan_output", Text),
    Column("stage3_research", json_type),
    Column("stage4_output", Text),
    Column("stage5_output", Text),
    Column("live_market_data", json_type),
    Column("portfolio_snapshot", json_type),
    Column("error_message", Text),
    Column("error_stage", Text),
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
    Column("completed_at", DateTime(timezone=True)),
    Index("pipeline_runs_status_idx", "status"),
    Index("pipeline_runs_started_idx", "started_at"),
)

_engine = None


def _normalize_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_engine():
    global _engine
    if _engine is None:
        url = _normalize_url(config.RESEARCH_DATABASE_URL)
        if url.startswith("sqlite:///"):
            db_path = url.removeprefix("sqlite:///")
            if db_path and db_path != ":memory:":
                os.makedirs(os.path.dirname(db_path), exist_ok=True)
        engine_kwargs = {"pool_pre_ping": True, "future": True}
        if url == "sqlite:///:memory:":
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            engine_kwargs["poolclass"] = StaticPool
        _engine = create_engine(url, **engine_kwargs)
    return _engine


def run_migrations():
    engine = get_engine()
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.exec_driver_sql(
                """
                CREATE OR REPLACE FUNCTION update_modified_column()
                RETURNS TRIGGER AS $$
                BEGIN
                    NEW.updated_at = NOW();
                    RETURN NEW;
                END;
                $$ language 'plpgsql';

                DROP TRIGGER IF EXISTS update_pipeline_runs_modtime ON pipeline_runs;
                CREATE TRIGGER update_pipeline_runs_modtime
                    BEFORE UPDATE ON pipeline_runs
                    FOR EACH ROW EXECUTE FUNCTION update_modified_column();
                """
            )


def reset_engine_for_tests():
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


if __name__ == "__main__":
    run_migrations()
    print("research database ready")
