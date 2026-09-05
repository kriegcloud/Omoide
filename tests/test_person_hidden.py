import importlib
import os
import tempfile
import unittest
from datetime import UTC, datetime


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.models import (  # noqa: E402
    Face,
    Media,
    Person,
    PersonMediaLink,
    PersonSocialLink,
    PersonTagLink,
    Tag,
)
from app.schemas.person import PersonBulkHideRequest  # noqa: E402

person_api = importlib.import_module("app.api.person")


class PersonHiddenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Media.__table__,
                Person.__table__,
                Face.__table__,
                Tag.__table__,
                PersonMediaLink.__table__,
                PersonSocialLink.__table__,
                PersonTagLink.__table__,
            ],
        )
        self.session = Session(self.engine)
        self.visible = Person(name="Visible", appearance_count=2)
        self.hidden = Person(
            name="Hidden",
            appearance_count=1,
            hidden_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.session.add_all([self.visible, self.hidden])
        self.session.commit()
        self.session.refresh(self.visible)
        self.session.refresh(self.hidden)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def list_people(self, *, hidden: bool = False):
        return person_api.list_persons(
            name=None,
            cursor=None,
            limit=50,
            hidden=hidden,
            session=self.session,
        )

    def test_list_excludes_hidden_by_default(self) -> None:
        result = self.list_people()
        self.assertEqual([person.id for person in result.items], [self.visible.id])

    def test_hidden_true_returns_only_hidden(self) -> None:
        result = self.list_people(hidden=True)
        self.assertEqual([person.id for person in result.items], [self.hidden.id])

    def test_hide_and_unhide_toggle(self) -> None:
        hidden_result = person_api.hide_person(self.visible.id, self.session)
        self.assertIsNotNone(hidden_result.hidden_at)
        self.assertEqual(self.list_people().items, [])

        unhidden_result = person_api.unhide_person(self.visible.id, self.session)
        self.assertIsNone(unhidden_result.hidden_at)
        self.assertEqual(
            [person.id for person in self.list_people().items],
            [self.visible.id],
        )

    def test_bulk_hide_skips_unknown_ids(self) -> None:
        unknown_id = max(self.visible.id, self.hidden.id) + 100
        result = person_api.hide_persons_bulk(
            PersonBulkHideRequest(person_ids=[self.visible.id, unknown_id]),
            self.session,
        )
        self.assertEqual(result.hidden_ids, [self.visible.id])
        self.assertEqual(result.skipped_ids, [unknown_id])


if __name__ == "__main__":
    unittest.main()
