import { lazy, Suspense, useEffect, useState } from "react";
import { CircularProgress } from "@mui/material";
import { useLocation } from "react-router-dom";
import { useEditorUrlState } from "../hooks/useEditorUrlState";
import { positiveId } from "../utils/editorUrlState";
import { getMedia } from "../services/media";
import type { Media } from "../types";

const ImageEditorDialog = lazy(() => import("./ImageEditorDialog"));

/** One owner also resolves cards outside the currently loaded grid page. */
export default function GridImageEditor() {
  const location = useLocation();
  const { params, patch, close } = useEditorUrlState();
  const rawId = /^\/medium\//.test(location.pathname) ? null : params.get("edit");
  const id = positiveId(rawId);
  const [media, setMedia] = useState<Media | null>(null);
  useEffect(() => {
    setMedia(null);
    if (rawId === null) return;
    if (id === null) { patch({ edit: null }); return; }
    let active = true;
    void getMedia(String(id)).then(detail => {
      if (!active) return;
      if (typeof detail.media.duration === "number") patch({ edit: null });
      else setMedia(detail.media);
    }).catch(() => { if (active) patch({ edit: null }); });
    return () => { active = false; };
  }, [rawId, id, patch]);
  if (id === null || !media || media.id !== id) return null;
  return <Suspense fallback={<CircularProgress aria-label="Loading image editor" />}>
    <ImageEditorDialog key={id} open media={media}
      mediaListKey={location.state?.editorListKey}
      onClose={() => close({ edit: null })} />
  </Suspense>;
}
