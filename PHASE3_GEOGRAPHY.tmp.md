# Geography / timeline / scenes coverage

| Method / endpoint | Export and caller | Baseline / classification | Result |
|---|---|---|---|
| POST /api/events/bulk-delete | deleteEventsBulk → EventsPage | (b) awaited removal; confirmation open | Snapshot targets at open; immediate core removal + close; successful subset receipt; targeted collection revalidation |
| PATCH /api/events/{id} | updateEvent → EventDetailPage | (b) waited with title disabled | immediate title overlay shared with events collection; editor closes; global errors |
| POST /api/events/{id}/convert-to-album | convertEventToAlbum → EventDetailPage | (b) confirmation held until response | confirmation closes; derived pending operation; success navigation guarded by route; uncertain collection read, no replay |
| POST /api/person/{id}/events | createTimelineEvent → TimelineTab | (b) await form, clear entire list | provisional event row; dismiss form synchronously; refresh without clearing |
| PUT /api/person/{id}/events/{event} | updateTimelineEvent → TimelineTab | (b) await form, clear entire list | immediate nested event fields/date overlay; dismiss form |
| DELETE /api/person/{id}/events/{event} | deleteTimelineEvent → TimelineTab | (b) await confirmation, console error | snapshot target; immediate event overlay removal and close; global errors |
| PATCH /api/media/{id}/geolocation | updateMediaGeolocation → MapEditorPage | (b) await save, remove list afterward | immediate location fields + scoped missing-geo removal; clear selection; targeted list/folder reads even after navigation |
| POST /api/media/{id}/scenes | createScene → SceneManager | (b) dialog spinner until response | immediate provisional scene + close; core pending/global errors |
| DELETE /api/media/{id}/scenes/{scene} | deleteScene → SceneManager | (b) await removal, page error | immediate scoped removal; rollback or targeted scene reread |
| POST /api/tasks/build_events | startBuildEvents → EventsPage | (c) no submitting state | submitJob + submitting/accepted feedback; actual task rows from useTaskEvents |
| POST /api/tasks/geocode_places | startGeocodePlaces → PlacesPage | (c) no submitting state | submitJob + submitting/accepted feedback; actual task rows from useTaskEvents |

MapPage, PlaceMediaPage, EventDetail media loading, and location search have only reads; no metadata writes omitted there. MapEditor external Nominatim search is GET.

Files: new services/geographyOperations.ts; components/TimelineTab.tsx, SceneManager.tsx; pages/EventsPage.tsx, EventDetailPage.tsx, MapEditorPage.tsx, PlacesPage.tsx; services/events.ts; tests/phase3-geography.test.cjs; additive useTaskEvents stub in tests/datasetRuntime.cjs; tests/detailRuntime.cjs geography/core/task stubs.

Tests: failing first (missing adapter + scene dialog still open), then 9 passing tests: four surface immediate/definitive/unknown/no-replay fixtures, real SceneManager dismiss-on-confirm fixture, bulk skipped IDs, failed unknown refresh retaining intent, real EventDetail rename/rollback, audited scene400/GPS403 classification. Focused ESLint clean. Final tsc and full suite handled by parent. No Python files touched.

Core extension: parent adds explicit event/scene EntityKind values. Adapter uses existing command API only; no shared core behavior changes.

Decisions: audited event/timeline/scene 400/404 validation errors are definitive. Geolocation403 may occur during filesystem updates and remains unknown; other unproven HTTP/transport failures revalidate. Conversion uses pending derived metadata rather than fabricating an album ID. Mixed timeline rows receive a local adapter with event-only IDs so media/event ID collisions cannot delete media rows.

Browser follow-ups: slowed network immediate tile/chip/title response, scene/timeline dialog dismissal and focus, definitive rejection rollback/global alert, uncertain outcome navigation, actual running task rows, retained list and map position, light/dark. No deployment or personal-library writes performed.
