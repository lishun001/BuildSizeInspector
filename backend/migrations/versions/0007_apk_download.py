"""存放待分析任务的临时 APK 下载凭据。"""

import sqlalchemy as sa
from alembic import op

revision = "0007_apk_download"
down_revision = "0006_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("apk_download_secret", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "apk_download_secret")
