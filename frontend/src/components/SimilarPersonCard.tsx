import type { SimilarPerson } from "../types";
import PersonCard from "./PersonCard";

interface SimilarPersonCardProps extends SimilarPerson {
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: () => void;
}

export default function SimilarPersonCard({ id, name, similarity, thumbnail, profile_face, selectable = false, selected = false, onToggleSelect }: SimilarPersonCardProps) {
  return (
    <PersonCard
      person={{ id, name, profile_face: profile_face ?? (thumbnail ? { thumbnail_path: thumbnail } : null) }}
      secondaryLabel={similarity != null ? `${similarity.toFixed(1)}% match` : ""}
      showFavorite={false}
      selecting={selectable}
      selected={selected}
      onSelectionClick={selectable && onToggleSelect ? () => { onToggleSelect(); return true; } : undefined}
    />
  );
}
