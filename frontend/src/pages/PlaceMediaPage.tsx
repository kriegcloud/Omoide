import GalleryPage from "../components/GalleryPage";
import React, { useCallback, useState } from "react";
import PublicIcon from "@mui/icons-material/Public";
import { useSearchParams } from "react-router-dom";
import { CursorMediaGrid } from "../components/CursorMediaGrid";
import { EmptyState } from "../components/EmptyState";
import { getPlaceMedia } from "../services/features";

export default function PlaceMediaPage() {
  const [searchParams] = useSearchParams();
  const [loadedCount, setLoadedCount] = useState(0);
  const city = searchParams.get("city") ?? "";
  const country = searchParams.get("country");

  const fetcher = useCallback(
    (cursor: string | null, signal: AbortSignal) =>
      getPlaceMedia(city, country, cursor, signal),
    [city, country]
  );

  return (
    <GalleryPage title={`${city}${country ? `, ${country}` : ""}`} count={loadedCount} countLabel="loaded">
      <CursorMediaGrid
        listKey={`place-${country ?? ""}-${city}`}
        fetcher={fetcher}
        onItemsChange={items => setLoadedCount(items.length)}
        empty={
          <EmptyState
            icon={<PublicIcon />}
            title="No media"
            description="No media found for this place."
          />
        }
      />
    </GalleryPage>
  );
}
