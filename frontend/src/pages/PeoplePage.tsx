import PeopleGrid from "../components/PeopleGrid";
import { useSearchParams } from "react-router-dom";

export default function PeoplePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawGender = searchParams.get("gender");
  const gender =
    rawGender === "female" || rawGender === "male" ? rawGender : undefined;

  const handleGenderChange = (value?: "female" | "male") => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (value) next.set("gender", value);
      else next.delete("gender");
      return next;
    });
  };

  return (
    <PeopleGrid
      title="People"
      listKey={`people-grid-${gender ?? "all"}`}
      gender={gender}
      onGenderChange={handleGenderChange}
    />
  );
}
