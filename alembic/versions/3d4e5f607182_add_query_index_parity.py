"""Add browsing indexes and stored media folder/dimension query keys.

Revision ID: 3d4e5f607182
Revises: 2c3d4e5f6071
"""

import posixpath

import sqlalchemy as sa
from alembic import op

revision = "3d4e5f607182"
down_revision = "2c3d4e5f6071"
branch_labels = None
depends_on = None


# Keep this list fixed: migrations must not depend on future model metadata.
INDEXES = (
    ("face", "ix_face_person_id", ("person_id",)),
    ("face", "ix_face_person_id_media_id", ("person_id", "media_id")),
    ("face", "ix_face_person_id_det_score", ("person_id", "det_score")),
    ("face", "ix_face_assigned_at", ("assigned_at",)),
    ("timelineevent", "ix_timelineevent_person_id", ("person_id",)),
    ("personmedialink", "ix_personmedialink_person_id", ("person_id",)),
    ("mediataglink", "ix_mediataglink_media_id", ("media_id",)),
    ("mediataglink", "ix_mediataglink_tag_id", ("tag_id",)),
    ("mediataglink", "ix_mediataglink_tag_id_media_id", ("tag_id", "media_id")),
    ("persontaglink", "ix_persontaglink_tag_id", ("tag_id",)),
    ("persontaglink", "ix_persontaglink_tag_id_person_id", ("tag_id", "person_id")),
    ("media", "ix_media_processing_error", ("processing_error",)),
    ("media", "ix_media_duration", ("duration",)),
    ("media", "ix_media_is_favorite_created_at", ("is_favorite", "created_at")),
    ("media", "ix_media_missing_confirmed_missing_since", ("missing_confirmed", "missing_since")),
    ("media", "ix_media_missing_since_created_at", ("missing_since", "created_at")),
    ("media", "ix_media_folder", ("folder",)),
    ("media", "ix_media_pixel_count", ("pixel_count",)),
    ("duplicatemedia", "ix_duplicatemedia_media_id_group_id", ("media_id", "group_id")),
    ("duplicatemedia", "ix_duplicatemedia_group_id", ("group_id",)),
    ("duplicatemedia", "ix_duplicatemedia_media_id", ("media_id",)),
    ("person_relationship", "ix_person_relationship_person_b_id", ("person_b_id",)),
    ("albummedialink", "ix_albummedialink_media_id", ("media_id",)),
    ("eventmedialink", "ix_eventmedialink_media_id", ("media_id",)),
    ("datasetitem", "ix_datasetitem_dataset_id_position_id", ("dataset_id", "position", "id")),
    ("processingtask", "ix_processingtask_task_type_status", ("task_type", "status")),
)

# Earlier revisions already own these indexes, but some installations predate
# their creation guards. Repair them without removing them during downgrade.
LEGACY_INDEXES = (
    ("personmedialink", "ix_personmedialink_media_id", ("media_id",)),
    ("exifdata", "ix_exifdata_city", ("city",)),
    ("exifdata", "ix_exifdata_country", ("country",)),
)


def upgrade() -> None:
    connection = op.get_bind()
    with op.batch_alter_table("media") as batch:
        batch.add_column(sa.Column("folder", sa.String(), nullable=True))
        batch.add_column(sa.Column("pixel_count", sa.Integer(), nullable=True))

    media = sa.table(
        "media", sa.column("id", sa.Integer()), sa.column("path", sa.String()),
        sa.column("folder", sa.String()),
    )
    # Backfill in bounded batches, preserving literal percent/underscore names
    # and normalizing Windows separators just like the model write hook.
    last_id = None
    while True:
        statement = sa.select(media.c.id, media.c.path).order_by(media.c.id).limit(1000)
        if last_id is not None:
            statement = statement.where(media.c.id > last_id)
        rows = connection.execute(statement).all()
        if not rows:
            break
        connection.execute(
            media.update().where(media.c.id == sa.bindparam("record_id"))
            .values(folder=sa.bindparam("parent_folder")),
            [{"record_id": row.id, "parent_folder": posixpath.dirname(row.path.replace("\\", "/"))}
             for row in rows],
        )
        last_id = rows[-1].id
    connection.execute(sa.text("UPDATE media SET pixel_count = width * height"))

    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    for table, name, columns in (*INDEXES, *LEGACY_INDEXES):
        if table not in tables:
            continue
        existing = {index["name"] for index in sa.inspect(connection).get_indexes(table)}
        if name not in existing:
            with op.batch_alter_table(table) as batch:
                batch.create_index(name, list(columns), unique=False)

    if "scene" in tables and "ix_scene_embedding" in {
        index["name"] for index in sa.inspect(connection).get_indexes("scene")
    }:
        with op.batch_alter_table("scene") as batch:
            batch.drop_index("ix_scene_embedding")


def downgrade() -> None:
    connection = op.get_bind()
    tables = set(sa.inspect(connection).get_table_names())
    for table, name, _ in reversed(INDEXES):
        if table not in tables:
            continue
        if name in {index["name"] for index in sa.inspect(connection).get_indexes(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_index(name)

    with op.batch_alter_table("media") as batch:
        batch.drop_column("pixel_count")
        batch.drop_column("folder")

    # The initial migration owns this index and expects it on downgrade.
    if "scene" in tables and "embedding" in {
        column["name"] for column in sa.inspect(connection).get_columns("scene")
    }:
        if "ix_scene_embedding" not in {
            index["name"] for index in sa.inspect(connection).get_indexes("scene")
        }:
            with op.batch_alter_table("scene") as batch:
                batch.create_index("ix_scene_embedding", ["embedding"], unique=False)
