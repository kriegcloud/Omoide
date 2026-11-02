import { useState, type SyntheticEvent } from "react";
import { API } from "../config";
import type { FaceRead } from "../types";
import { encodeFilePath } from "../urlUtils";
import { uprightFaceDimensions } from "../utils/faceBox";

export type FaceThumbnailData = Partial<Pick<FaceRead, "id" | "thumbnail_path" | "timestamp" | "bbox" | "media_duration" | "media_thumbnail_path" | "media_width" | "media_height">>;

/** Resolve a parent photo or exact video frame, falling back to the detected crop. */
export function useFaceThumbnail(face?: FaceThumbnailData | null) {
  const [natural, setNatural] = useState<{ path: string; size: [number, number] } | null>(null);
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const frameUrl = face?.id != null && face.timestamp != null && face.media_duration != null
    ? `${API}/api/faces/${face.id}/frame` : null;
  const photoPath = face?.media_duration == null && face?.timestamp == null ? face?.media_thumbnail_path : null;
  const parentPath = frameUrl ?? photoPath;
  const sourcePath = parentPath && parentPath !== failedSource ? parentPath : face?.thumbnail_path;
  const showsParent = Boolean(parentPath && sourcePath === parentPath);
  const size = natural && natural.path === sourcePath ? natural.size : null;
  const dimensions = size && showsParent
    ? frameUrl ? size : face?.media_width && face.media_height
      ? uprightFaceDimensions(face.media_width, face.media_height, ...size) : null : null;
  const aspectRatio = size ? size[0] / size[1]
    : showsParent && face?.media_width && face.media_height ? face.media_width / face.media_height : 1;
  return {
    src: sourcePath ? sourcePath === frameUrl ? frameUrl : `${API}/thumbnails/${encodeFilePath(sourcePath)}` : undefined,
    showsParent,
    dimensions,
    aspectRatio,
    onLoad: (event: SyntheticEvent<HTMLImageElement>) => {
      const image = event.currentTarget;
      if (sourcePath && image.naturalWidth && image.naturalHeight) setNatural({ path: sourcePath, size: [image.naturalWidth, image.naturalHeight] });
    },
    onError: () => { if (showsParent) setFailedSource(sourcePath ?? null); setNatural(null); },
  };
}
