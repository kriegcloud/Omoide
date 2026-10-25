"""Shared media deletion, including curation/dataset FKs and annotation locks."""
from app.utils import delete_record, delete_file

__all__ = ["delete_record", "delete_file"]
