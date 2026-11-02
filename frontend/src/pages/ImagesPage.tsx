import React from "react";
import MediaListPage from "../components/MediaListPage";
import { getImages } from "../services/media";

export default function ImagesPage() {
  return (
    <MediaListPage
      title="Images"
      listKeyPrefix="images"
      fetcher={getImages}
      emptyTitle="No images found"
      emptyDescription="Run a scan or add new media to see images here."
    />
  );
}
