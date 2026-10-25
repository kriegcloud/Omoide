"""Fence the pinned AdaFace vector space and admit genuinely empty catalogs."""

from sqlalchemy.engine import Engine

from app.services.face_inference import FACE_MODEL_FINGERPRINT

ADA_FACE_MODEL_NAME = "cvlface_adaface_vit_base_kprpe_webface12m"
_MIGRATION_REQUIRED = (
    "AdaFace cannot process media until the existing face "
    "embeddings have completed the pinned model migration."
)


def ensure_pinned_face_model(engine: Engine, *, bootstrap_empty: bool = False) -> None:
    """Require ready provenance, or initialize an empty space after model setup.

    A missing marker is admissible only if both entity tables and both vector
    tables are empty. Any marker, including a same-model migration in progress,
    remains authoritative. The final empty check and marker insertion share a
    SQLite writer transaction so another writer cannot race between them.
    Callers check once before expensive model setup and again with
    ``bootstrap_empty=True`` only after all initialization has succeeded.
    """
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        tables = {
            row[0] for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        state = None
        if "model_fingerprints" in tables:
            state = connection.exec_driver_sql(
                "SELECT fingerprint, status FROM model_fingerprints "
                "WHERE component = 'face_embeddings'"
            ).first()
        if state is not None:
            if tuple(state) != (FACE_MODEL_FINGERPRINT, "ready"):
                raise RuntimeError(_MIGRATION_REQUIRED)
            return

        # Missing schema is not evidence of an empty, initialized vector space.
        for table in ("face", "person", "face_embeddings", "person_embeddings"):
            if table not in tables or connection.exec_driver_sql(
                f"SELECT 1 FROM {table} LIMIT 1"
            ).first() is not None:
                raise RuntimeError(_MIGRATION_REQUIRED)
        if "face_embedding_migration" in tables and connection.exec_driver_sql(
            "SELECT 1 FROM face_embedding_migration LIMIT 1"
        ).first() is not None:
            raise RuntimeError(_MIGRATION_REQUIRED)
        if not bootstrap_empty:
            return

        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS model_fingerprints ("
            "component TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO model_fingerprints(component, fingerprint, status) "
            "VALUES ('face_embeddings', ?, 'ready')",
            (FACE_MODEL_FINGERPRINT,),
        )
