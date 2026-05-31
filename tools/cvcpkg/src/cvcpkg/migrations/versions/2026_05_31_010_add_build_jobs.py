"""Add build_jobs and build_job_deps tables for job queue and DAG scheduling.

Revision ID: 010
Revises: 009
Create Date: 2026-05-31

Adds the ``build_jobs`` table for tracking build job requests and
the ``build_job_deps`` table for encoding DAG edges between jobs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists("build_jobs"):
        op.create_table(
            "build_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("dag_id", sa.String(64), nullable=True),
            sa.Column("org_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("recipe_name", sa.String(255), nullable=False),
            sa.Column("recipe_version", sa.String(128), nullable=False, server_default=""),
            sa.Column("recipe_hash", sa.String(128), nullable=False, server_default=""),
            sa.Column("platform", sa.String(64), nullable=False),
            sa.Column("arch", sa.String(64), nullable=False),
            sa.Column("config", sa.String(32), nullable=False, server_default="release"),
            sa.Column("link", sa.String(32), nullable=False, server_default="shared"),
            sa.Column(
                "builder_id",
                sa.Integer(),
                sa.ForeignKey("builders.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("timeout_seconds", sa.Integer(), nullable=True),
            sa.Column("submitted_by", sa.String(255), nullable=False),
            sa.Column(
                "submitted_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("log_url", sa.Text(), nullable=True),
            sa.Column("log_size_bytes", sa.BigInteger(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("result_archive_url", sa.Text(), nullable=True),
        )
        op.create_index("ix_build_jobs_dag_id", "build_jobs", ["dag_id"])
        op.create_index("ix_build_jobs_org_slug", "build_jobs", ["org_slug"])
        op.create_index("ix_build_jobs_status", "build_jobs", ["status"])
        op.create_index(
            "ix_build_jobs_platform_arch", "build_jobs", ["platform", "arch"]
        )
        op.create_index("ix_build_jobs_builder_id", "build_jobs", ["builder_id"])
        op.create_index("ix_build_jobs_recipe_name", "build_jobs", ["recipe_name"])

    if not _table_exists("build_job_deps"):
        op.create_table(
            "build_job_deps",
            sa.Column(
                "job_id",
                sa.Integer(),
                sa.ForeignKey("build_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "depends_on_job_id",
                sa.Integer(),
                sa.ForeignKey("build_jobs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "job_id", "depends_on_job_id", name="uq_build_job_dep"
            ),
        )
        op.create_index(
            "ix_build_job_deps_job_id", "build_job_deps", ["job_id"]
        )
        op.create_index(
            "ix_build_job_deps_depends_on", "build_job_deps", ["depends_on_job_id"]
        )


def downgrade() -> None:
    op.drop_index("ix_build_job_deps_depends_on", table_name="build_job_deps")
    op.drop_index("ix_build_job_deps_job_id", table_name="build_job_deps")
    op.drop_table("build_job_deps")
    op.drop_index("ix_build_jobs_recipe_name", table_name="build_jobs")
    op.drop_index("ix_build_jobs_builder_id", table_name="build_jobs")
    op.drop_index("ix_build_jobs_platform_arch", table_name="build_jobs")
    op.drop_index("ix_build_jobs_status", table_name="build_jobs")
    op.drop_index("ix_build_jobs_org_slug", table_name="build_jobs")
    op.drop_index("ix_build_jobs_dag_id", table_name="build_jobs")
    op.drop_table("build_jobs")
