import importlib
import os
import tempfile
import unittest


_CONFIG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CONFIG_HOME.name

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app.models import (  # noqa: E402
    Face,
    Media,
    MediaTagLink,
    Person,
    PersonTagLink,
    Tag,
)
from app.schemas.person import PersonUpdate  # noqa: E402
from app.utils import update_person_demographics  # noqa: E402

person_api = importlib.import_module("app.api.person")


class PersonDemographicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(
            self.engine,
            tables=[
                Media.__table__,
                Person.__table__,
                Face.__table__,
                Tag.__table__,
                MediaTagLink.__table__,
                PersonTagLink.__table__,
            ],
        )
        self.session = Session(self.engine)
        self.person = Person(name="Test", appearance_count=0)
        self.media = Media(path="/media/test.jpg", filename="test.jpg", size=1)
        self.session.add_all([self.person, self.media])
        self.session.commit()
        self.session.refresh(self.person)
        self.session.refresh(self.media)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def add_face(
        self, sex: str, det_score: float | None, age: int | None = None
    ) -> Face:
        face = Face(
            media_id=self.media.id,
            person_id=self.person.id,
            bbox=[0, 0, 10, 10],
            sex=sex,
            det_score=det_score,
            age=age,
        )
        self.session.add(face)
        self.session.flush()
        return face

    def tag_names(self) -> set[str]:
        return set(
            self.session.exec(
                select(Tag.name)
                .join(PersonTagLink, PersonTagLink.tag_id == Tag.id)
                .where(PersonTagLink.person_id == self.person.id)
            ).all()
        )

    def test_weighted_vote_confidence_and_median_age(self) -> None:
        self.add_face("F", 0.9, 20)
        self.add_face("M", 0.3, 30)

        update_person_demographics(self.session, [self.person.id])
        self.session.flush()

        self.assertEqual(self.person.gender, "female")
        self.assertAlmostEqual(self.person.gender_confidence, 0.75)
        self.assertEqual(self.person.age, 25)
        self.assertEqual(self.tag_names(), {"Female"})

    def test_missing_detection_score_uses_unit_weight(self) -> None:
        self.add_face("M", None)
        self.add_face("F", 0.5)

        update_person_demographics(self.session, [self.person.id])

        self.assertEqual(self.person.gender, "male")
        self.assertAlmostEqual(self.person.gender_confidence, 2 / 3)

    def test_manual_override_wins_and_mirrors_tag(self) -> None:
        self.add_face("F", 1.0, 32)
        person_api.update_person(
            self.person.id,
            PersonUpdate(gender="male"),
            self.session,
        )

        update_person_demographics(self.session, [self.person.id])
        self.session.flush()

        self.assertTrue(self.person.gender_manual)
        self.assertEqual(self.person.gender, "male")
        self.assertEqual(self.person.age, 32)
        self.assertEqual(self.tag_names(), {"Male"})

    def test_tag_removed_below_threshold_and_restored_above_it(self) -> None:
        female = self.add_face("F", 0.7)
        male = self.add_face("M", 0.3)
        update_person_demographics(self.session, [self.person.id])
        self.session.flush()
        self.assertEqual(self.tag_names(), {"Female"})

        female.det_score = 0.6
        male.det_score = 0.4
        self.session.add_all([female, male])
        update_person_demographics(self.session, [self.person.id])
        self.session.flush()

        self.assertAlmostEqual(self.person.gender_confidence, 0.6)
        self.assertEqual(self.tag_names(), set())

    def test_clearing_override_reaggregates_faces(self) -> None:
        self.add_face("F", 1.0)
        person_api.update_person(
            self.person.id,
            PersonUpdate(gender="male"),
            self.session,
        )
        person_api.update_person(
            self.person.id,
            PersonUpdate(gender=None),
            self.session,
        )

        self.assertFalse(self.person.gender_manual)
        self.assertEqual(self.person.gender, "female")
        self.assertEqual(self.tag_names(), {"Female"})

    def test_list_gender_filter(self) -> None:
        female = Person(
            name="Female", appearance_count=2, gender="female"
        )
        male = Person(name="Male", appearance_count=1, gender="male")
        self.session.add_all([female, male])
        self.session.commit()

        result = person_api.list_persons(
            name=None,
            cursor=None,
            limit=50,
            hidden=False,
            gender="female",
            session=self.session,
        )

        self.assertEqual([item.id for item in result.items], [female.id])


if __name__ == "__main__":
    unittest.main()
