import { mutationBus } from "../stores/mutationBus";
import {
  CursorPage,
  MissingGeoFolders,
  MissingGeoMedia,
  MissingGeoSort,
} from "../types";
import { apiFetch } from "./api";

export const getMissingGeoMedia = async (
  cursor: string | null,
  { folder, sort }: { folder?: string | null; sort?: MissingGeoSort } = {},
  signal?: AbortSignal
): Promise<CursorPage<MissingGeoMedia>> =>
  apiFetch<CursorPage<MissingGeoMedia>>("/api/media/missing-geo", {
    query: { cursor, folder, sort },
    errorMessage: "Failed to fetch media without geolocation",
    signal,
  });

export const getMissingGeoFolders = async (
  signal?: AbortSignal
): Promise<MissingGeoFolders> =>
  apiFetch<MissingGeoFolders>("/api/media/missing-geo/folders", {
    errorMessage: "Failed to fetch folders",
    signal,
  });

const saveMediaGeolocation = async (
  mediaId: number,
  latitude: number,
  longitude: number
) =>
  apiFetch<void>(`/api/media/${mediaId}/geolocation`, {
    method: "PATCH",
    body: {
      latitude: latitude,
      longitude: longitude,
    },
    errorMessage: "Failed to update media geolocation",
  });

export const updateMediaGeolocation = async (...args: Parameters<typeof saveMediaGeolocation>) => {
 const result = await saveMediaGeolocation(...args);
 mutationBus.emit({type: "media:updated", items: [{id: args[0], latitude: args[1], longitude: args[2]}]});
 mutationBus.emit({type: "list:invalidate", prefix: "media-missing-geolocation"});
 return result;
};
