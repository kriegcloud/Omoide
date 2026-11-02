import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Box, Button, Card, CardContent, Dialog, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography } from "@mui/material";
import { useInView } from "react-intersection-observer";
import GalleryPage from "./GalleryPage";
import Gallery from "./Gallery";
import FaceCard from "./FaceCard";
import { FaceGrid } from "./FaceGrid";
import ListStatus from "./ListState";
import PersonPicker from "./PersonPicker";
import { StickySelectionToolbar } from "./BulkResolveToolbar";
import { useUndo, useUndoRefresh } from "../context/UndoContext";
import { useHotkey } from "../hotkeys/useHotkey";
import { defaultListState, useOptimisticCachedList, refreshCachedList, useListInvalidation, useListStore } from "../stores/useListStore";
import { appliedFaceIds, faceGroupsKey, getFaceGroups, getIgnoredFaces, ignoredFacesKey, remainingGroupFaces, startFaceReview, type FaceReviewGroup } from "../services/faceReview";
import type { FaceRead, Person } from "../types";
import config from "../config";

export function IgnoredFaces({ tabs }: { tabs: ReactNode }) {
  const listKey = ignoredFacesKey;
  const { items, hasMore, isLoading, error } = useOptimisticCachedList<FaceRead>(listKey, "face") ?? defaultListState;
  const [selected, setSelected] = useState<number[]>([]);
  const { push, refreshVisible } = useUndo();
  useListInvalidation(listKey);
  useUndoRefresh(`cache:${listKey}`, () => refreshCachedList(listKey));
  useEffect(() => { void useListStore.getState().fetchInitial(listKey, () => getIgnoredFaces()); }, [listKey]);
  const { ref, inView } = useInView({ skip: isLoading || !hasMore || !!error });
  useEffect(() => { if (inView && hasMore && !isLoading && !error) void useListStore.getState().loadMore(listKey, getIgnoredFaces); }, [inView, hasMore, isLoading, error, listKey]);
  const restore = (ids: number[], all = false) => {
    if (config.PRESENTATION_MODE) return;
    const rows = items.filter(face => ids.includes(face.id));
    const operation = startFaceReview("unignore", ids, all ? { select_all: true } : {}, rows);
    setSelected([]);
    void operation.done.then(record => {
      if (!record.receipt?.applied.length) return;
      push({ label: "Restored ignored faces", undo: async () => {
        await startFaceReview("ignore", appliedFaceIds(record.receipt!), {}, rows).done;
        await refreshVisible();
      } });
    });
  };
  useHotkey({ key: "i" }, () => restore(selected), { scope: "page", enabled: !!selected.length && !config.PRESENTATION_MODE, description: "Restore selected ignored faces" });
  useHotkey({ key: "i", shift: true }, () => restore(items.map(face => face.id), true), { scope: "page", enabled: items.length > 0 && !config.PRESENTATION_MODE, description: "Restore all ignored faces" });
  useHotkey({ key: "Escape" }, () => setSelected([]), { scope: "page", enabled: !!selected.length, description: "Clear ignored face selection" });
  return <GalleryPage title="Ignored Faces" tabs={tabs} count={items.length} countLabel="loaded faces" selection={<StickySelectionToolbar><Stack direction="row" spacing={1} alignItems="center">
    <Typography>{selected.length} selected</Typography>
    <Button onClick={() => setSelected(selected.length < items.length ? items.map(face => face.id) : [])}>Select All</Button>
    <Button disabled={!selected.length || config.PRESENTATION_MODE} onClick={() => restore(selected)}>Restore selected</Button>
    <Button disabled={!items.length || config.PRESENTATION_MODE} onClick={() => restore(items.map(face => face.id), true)}>Restore all ignored</Button>
  </Stack></StickySelectionToolbar>}>
    <ListStatus loading={isLoading && !items.length} error={error} empty={!items.length && !isLoading} emptyMessage="No ignored faces." onRetry={() => { void refreshCachedList(listKey); }} />
    <FaceGrid faces={items} selectedFaceIds={selected} onSelectionChange={setSelected} renderFooter={face => <Button data-tile-control data-no-marquee disabled={config.PRESENTATION_MODE} onClick={() => restore([face.id])}>Restore</Button>} />
    {hasMore && <Box ref={ref} sx={{ height: 50 }} />}
  </GalleryPage>;
}

export function FaceGroups({ tabs }: { tabs: ReactNode }) {
  const { items, hasMore, isLoading, error } = useOptimisticCachedList<FaceReviewGroup>(faceGroupsKey, "face") ?? defaultListState;
  useListInvalidation(faceGroupsKey);
  useUndoRefresh(`cache:${faceGroupsKey}`, () => refreshCachedList(faceGroupsKey));
  useEffect(() => { void useListStore.getState().fetchInitial(faceGroupsKey, () => getFaceGroups()); }, []);
  const { ref, inView } = useInView({ skip: isLoading || !hasMore || !!error });
  useEffect(() => { if (inView && hasMore && !isLoading && !error) void useListStore.getState().loadMore(faceGroupsKey, getFaceGroups); }, [inView, hasMore, isLoading, error]);
  return <GalleryPage title="Look-alike Groups" tabs={tabs} count={items.length} countLabel="loaded groups">
    <Typography color="text.secondary" sx={{ mb: 2 }}>Review similar faces together. Remove unrelated members before assigning the remaining faces.</Typography>
    <ListStatus loading={isLoading && !items.length} error={error} empty={!items.length && !isLoading} emptyMessage="No look-alike groups found." onRetry={() => { void refreshCachedList(faceGroupsKey); }} />
    <Gallery>{items.map(group => <FaceGroupCard key={group.id} group={group} />)}</Gallery>
    {hasMore && <Box ref={ref} sx={{ height: 50 }} />}
  </GalleryPage>;
}

function FaceGroupCard({ group }: { group: FaceReviewGroup }) {
  const [removed, setRemoved] = useState<number[]>([]);
  const [selectedMembers, setSelectedMembers] = useState<number[]>([]);
  const members = group.member_faces;
  const [dialog, setDialog] = useState<"assign" | "create" | null>(null);
  const [name, setName] = useState("");
  const { push, refreshVisible } = useUndo();
  const ids = remainingGroupFaces(group.face_ids, removed);
  const act = (target?: { person_id?: number; new_person_name?: string }) => {
    if (!ids.length || config.PRESENTATION_MODE) return;
    const action = target ? "accept" : "ignore";
    const rows = members.filter(face => ids.includes(face.id));
    const operation = startFaceReview(action, ids, target ?? {}, rows, group.id);
    setDialog(null);
    if (action === "ignore") void operation.done.then(record => {
      if (record.receipt?.applied.length) push({ label: "Ignored group", undo: async () => {
        await startFaceReview("unignore", appliedFaceIds(record.receipt!), {}, rows).done;
        await refreshVisible();
      } });
    });
  };
  // Only the focused group's bindings participate in the page scope.
  const [focused, setFocused] = useState(false);
  const enabled = focused && ids.length > 0 && !config.PRESENTATION_MODE;
  useHotkey({ key: "a" }, () => setDialog("assign"), { scope: "page", enabled, description: "Assign focused face group…" });
  useHotkey({ key: "n" }, () => setDialog("create"), { scope: "page", enabled, description: "Create person from focused group…" });
  useHotkey({ key: "i" }, () => act(), { scope: "page", enabled, description: "Ignore focused face group" });
  useHotkey({ key: "c" }, () => { if (group.candidate) act({ person_id: group.candidate.person_id }); }, { scope: "page", enabled: enabled && !!group.candidate, description: "Assign focused group to candidate" });
  useHotkey({ key: "x" }, () => { setRemoved(previous => [...new Set([...previous, ...selectedMembers])]); setSelectedMembers([]); }, { scope: "page", enabled: focused && selectedMembers.length > 0, description: "Remove selected members from group" });
  useHotkey({ key: "z" }, () => setRemoved([]), { scope: "page", enabled: focused && removed.length > 0, description: "Reset removed group members" });
  return <Card onFocus={() => setFocused(true)} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setFocused(false); }}>
    <FaceCard face={group.representative_face} isProfile={false} />
    <CardContent>
      <Typography variant="subtitle1">{ids.length} of {group.size} faces · {group.media_count} photos</Typography>
      <Typography variant="body2">Mean cosine {group.mean_cos.toFixed(2)} · minimum {group.min_cos.toFixed(2)}</Typography>
      {group.candidate && <Typography variant="body2">{group.candidate.person_name || `Person ${group.candidate.person_id}`} · mean {group.candidate.mean_cos.toFixed(2)} · margin {group.candidate.margin.toFixed(2)}</Typography>}
      <Box sx={{ mt: 1 }}><FaceGrid faces={members.filter(face => !removed.includes(face.id))} selectedFaceIds={selectedMembers} onSelectionChange={setSelectedMembers} renderFooter={face => <Button data-tile-control data-no-marquee onClick={() => { setRemoved(previous => [...previous, face.id]); setSelectedMembers(previous => previous.filter(id => id !== face.id)); }}>Remove member</Button>} /></Box>
      {selectedMembers.length > 0 && <Button onClick={() => { setRemoved(previous => [...new Set([...previous, ...selectedMembers])]); setSelectedMembers([]); }}>Remove selected members</Button>}
      {removed.length > 0 && <Button onClick={() => setRemoved([])}>Reset removed members</Button>}
      <Stack spacing={1} sx={{ mt: 1 }}>
        {group.candidate && <Button disabled={!ids.length || config.PRESENTATION_MODE} onClick={() => act({ person_id: group.candidate!.person_id })}>Assign to {group.candidate.person_name || `Person ${group.candidate.person_id}`}</Button>}
        <Button disabled={!ids.length || config.PRESENTATION_MODE} onClick={() => setDialog("assign")}>Assign to…</Button>
        <Button disabled={!ids.length || config.PRESENTATION_MODE} onClick={() => setDialog("create")}>New person…</Button>
        <Button disabled={!ids.length || config.PRESENTATION_MODE} onClick={() => act()}>Ignore group</Button>
      </Stack>
    </CardContent>
    <Dialog open={dialog !== null} onClose={() => setDialog(null)} fullWidth>
      <DialogTitle>{dialog === "create" ? "New person" : "Assign group"} · {ids.length} faces</DialogTitle>
      <DialogContent>{dialog === "assign" ? <PersonPicker autoFocus onSelect={(person: Person) => act({ person_id: person.id })} /> : <TextField autoFocus fullWidth label="Person name" value={name} onChange={event => setName(event.target.value)} />}</DialogContent>
      <DialogActions><Button onClick={() => setDialog(null)}>Cancel</Button>{dialog === "create" && <Button disabled={!name.trim()} onClick={() => act({ new_person_name: name.trim() })}>Create person</Button>}</DialogActions>
    </Dialog>
  </Card>;
}
