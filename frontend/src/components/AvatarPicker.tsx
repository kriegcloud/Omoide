import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { API } from "../config";
import type { Face, Person } from "../types";
import { useDialogHotkeyScope, useHotkey } from "../hotkeys/useHotkey";
import Gallery from "./Gallery";
import FaceCard from "./FaceCard";

interface Props {
  person: Person;
  open: boolean;
  busy: boolean;
  uncertain?: boolean;
  onCheckStatus: () => Promise<void>;
  onClose: () => void;
  onSelect: (face: Face) => Promise<void>;
  onAutoPick: () => Promise<void>;
}

/** Focused photo selection keeps the original image and detected face visible. */
export default function AvatarPicker({ person, open, busy, uncertain, onCheckStatus, onClose, onSelect, onAutoPick }: Props) {
  const [sort, setSort] = useState<"quality" | "newest">("quality");
  const [faces, setFaces] = useState<Face[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const loadingRef = useRef(false);
  const dialogRef = useDialogHotkeyScope(open);
  useHotkey({ key: "Escape" }, () => { if (!busy) onClose(); }, { scope: "dialog", dialogRef, enabled: open, allowInInput: true, description: "Close profile photo picker" });
  const load = useCallback(async (after: string | null, signal?: AbortSignal) => {
    if (loadingRef.current) return;
    const version = generation.current;
    loadingRef.current = true; setLoading(true); setError("");
    try {
      const params = new URLSearchParams({ sort, limit: "40" });
      if (after) params.set("cursor", after);
      const response = await fetch(`${API}/api/person/${person.id}/avatar-faces?${params}`, { signal });
      if (!response.ok) throw new Error("Could not load profile photos. Try again.");
      const page: { items: Face[]; next_cursor: string | null } = await response.json();
      if (version !== generation.current || signal?.aborted) return;
      setFaces(previous => after ? [...previous, ...page.items.filter(face => !previous.some(item => item.id === face.id))] : page.items);
      setCursor(page.next_cursor);
    } catch (cause) {
      if (version === generation.current && !signal?.aborted) setError(cause instanceof Error ? cause.message : "Could not load profile photos.");
    } finally {
      if (version === generation.current) { loadingRef.current = false; setLoading(false); }
    }
  }, [person.id, sort]);
  useEffect(() => {
    const version = ++generation.current; loadingRef.current = false; setFaces([]); setCursor(null); setError("");
    if (!open) return;
    const controller = new AbortController();
    void load(null, controller.signal);
    return () => { generation.current = version + 1; loadingRef.current = false; controller.abort(); };
  }, [open, load]);
  return <Dialog ref={dialogRef} open={open} onClose={() => { if (!busy) onClose(); }} fullWidth maxWidth="md" aria-labelledby="avatar-picker-title">
    <DialogTitle id="avatar-picker-title">Choose profile photo</DialogTitle>
    <DialogContent>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "center" }} justifyContent="space-between" sx={{ mb: 2 }}>
        <Typography color="text.secondary">Choose a detected face for {person.name || "this person"}.</Typography>
        <ToggleButtonGroup exclusive size="small" value={sort} aria-label="Photo order" onChange={(_, value) => { if (value) setSort(value); }} disabled={busy || uncertain}>
          <ToggleButton value="quality">Best quality</ToggleButton><ToggleButton value="newest">Newest</ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      {uncertain && <Alert severity="warning" action={<Button disabled={busy} onClick={() => void onCheckStatus()}>Check status</Button>} sx={{ mb: 2 }}>The last profile photo update is awaiting confirmation. Check its status before choosing another photo.</Alert>}
      {error && <Alert severity="error" action={<Button onClick={() => void load(cursor)}>Retry</Button>} sx={{ mb: 2 }}>{error}</Alert>}
      <Box aria-busy={loading || busy} sx={{ pointerEvents: busy || uncertain ? "none" : undefined, opacity: busy || uncertain ? 0.6 : 1 }}>
        <Gallery minTileWidth={180}>{faces.map(face => <FaceCard key={face.id} face={face} isProfile={face.id === person.profile_face?.id} onSetProfile={() => void onSelect(face)} onChoose={() => void onSelect(face)} footer={<Button fullWidth disabled={busy || uncertain || face.id === person.profile_face?.id} onClick={() => void onSelect(face)}>{face.id === person.profile_face?.id ? "Current profile photo" : "Use this face"}</Button>} />)}</Gallery>
      </Box>
      {!loading && !error && !faces.length && <Typography sx={{ py: 4 }} color="text.secondary">No detected faces available for this person.</Typography>}
      {loading && <Box role="status" aria-label="Loading profile photos" sx={{ display: "flex", justifyContent: "center", p: 3 }}><CircularProgress size={28} /></Box>}
      {cursor && !loading && <Button fullWidth disabled={busy || uncertain} onClick={() => void load(cursor)} sx={{ mt: 2 }}>Load more photos</Button>}
    </DialogContent>
    <DialogActions><Button disabled={busy || uncertain || loading || !faces.length} onClick={() => void onAutoPick()}>Auto pick</Button><Button disabled={busy} onClick={onClose}>Close</Button></DialogActions>
  </Dialog>;
}
