import React, { lazy, Suspense, useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import {
  Container,
  Box,
  Dialog,
  DialogContent,
  Typography,
  Button,
  CircularProgress,
  Snackbar,
  LinearProgress,
  Alert,
  IconButton,
  useTheme,
  useMediaQuery,
} from "@mui/material";
import ArrowBackIosNew from "@mui/icons-material/ArrowBackIosNew";
import ArrowForwardIos from "@mui/icons-material/ArrowForwardIos";
import CloseIcon from "@mui/icons-material/Close";

import { useListStore } from "../stores/useListStore";
import { mutationBus, runOptimistic } from "../stores/mutationBus";
import { useMutationRefresh, useUndoRefresh } from "../context/UndoContext";
import { useTaskCompletionVersion } from "../TaskEventsContext";

import { ActionDialogs } from "../components/ActionDialogs";
import { MediaDisplay } from "../components/MediaDisplay";
import { MediaHeader } from "../components/MediaHeader";
import { MediaContentTabs } from "../components/MediaContentTabs";
import { SwipeHint } from "../components/SwipeHint";
import BeforeAfterCompare from "../components/BeforeAfterCompare";
import { listRepairs } from "../services/repairs";

import { Media, MediaDetail, Tag, Task } from "../types";
import { getMedia } from "../services/media";
import { getConfig } from "../services/config";
import {
  convertMedia,
  deleteMediaRecord,
  deleteMediaFile,
  openMediaFolder,
  openMediaFile,
  setMediaFavorite,
} from "../services/mediaActions";
import { getTask } from "../services/task";
import config from "../config";
import { useDialogHotkeyScope, useHotkey, useHotkeyHelp } from "../hotkeys/useHotkey";
import { getTopModal } from "../hotkeys/keymap";

const ImageEditorDialog = lazy(() => import("../components/ImageEditorDialog"));

export default function MediaDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const backgroundLocation = location.state?.backgroundLocation;
  const sceneStartTime: number | null = location.state?.sceneStart ?? null;
  const shouldAutoplayVideo = location.state?.autoplayVideo === true;
  const buildNavigationState = useCallback(
    (extra: Record<string, unknown> = {}) => {
      const baseState = location.state ? { ...location.state } : {};
      if (baseState && "sceneStart" in baseState) {
        delete (baseState as Record<string, unknown>).sceneStart;
      }
      return { ...baseState, ...extra };
    },
    [location.state]
  );
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));

  // --- 1. STATE MANAGEMENT ---
  const mediaListKey = location.state?.mediaListKey as string | undefined;
  const listFromStore = useListStore((state) =>
    mediaListKey ? state.lists[mediaListKey] : undefined
  );
  // A. Global state from Zustand for the list context
  const items: unknown[] = listFromStore?.items ?? [];

  const routeId = useRef(id);
  routeId.current = id;
  const detailRequest = useRef(0);

  // B. Local state for this specific modal's content
  const preloadedMedia = location.state?.media as Media | null;
  const [storedDetail, setDetail] = useState<MediaDetail | null>(
    preloadedMedia && String(preloadedMedia.id) === id ? { media: preloadedMedia, persons: [], orphans: [] } : null
  );
  const detail = storedDetail && String(storedDetail.media.id) === id ? storedDetail : null;
  const [isDetailLoading, setIsDetailLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ status?: number; message: string } | null>(null);

  // C. Local state for all other UI and features
  const [task, setTask] = useState<Task | null>(null);
  const [dialogType, setDialogType] = useState<
    "convert" | "deleteRecord" | "deleteFile" | null
  >(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [actionTarget, setActionTarget] = useState<{ id: number; mediaListKey?: string } | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const actionBusyRef = useRef(false);
  const [favoriteBusy, setFavoriteBusy] = useState(false);
  const [infoVisible, setInfoVisible] = useState(true);
  const hotkeyScope = useDialogHotkeyScope(true);
  const openHotkeyHelp = useHotkeyHelp();
  const hasChildDialog = useCallback(() => {
    const topModal = getTopModal();
    return dialogType !== null || editorOpen || !!(topModal && topModal !== hotkeyScope.current);
  }, [dialogType, editorOpen, hotkeyScope]);
  const [snackbar, setSnackbar] = useState<{
    open: boolean;
    message: string;
    severity: "success" | "error";
  }>({ open: false, message: "", severity: "success" });
  // Empty string falls back to the media type's first tab until the user picks one
  const [tabKey, setTabKey] = useState<string>("");
  const [touchStartX, setTouchStartX] = useState<number | null>(null);
  const [seekRequest, setSeekRequest] = useState<{ time: number; seq: number } | null>(null);
  const handleSeekRequest = useCallback((time: number) => {
    setSeekRequest((prev) => ({ time, seq: (prev?.seq ?? 0) + 1 }));
  }, []);
  const videoTimeRef = useRef(0);
  const handleVideoProgress = useCallback((secs: number) => {
    videoTimeRef.current = secs;
  }, []);
  const [showSwipeHint, setShowSwipeHint] = useState(false);
  const [isBinary, setIsBinary] = useState<boolean>(false);
  const [compareSource, setCompareSource] = useState<Media | null>(null);
  const [compareEnabled, setCompareEnabled] = useState(false);

  // --- 2. DERIVED DATA & CONTEXT ---

  const navigationIdsFromState = useMemo(() => {
    const ids = location.state?.navigationContext?.ids;
    if (!Array.isArray(ids)) return [];
    return ids.filter((value): value is number => typeof value === "number");
  }, [location.state]);

  const allMediaIdsInView = useMemo(() => {
    const extractItemId = (item: unknown): number | null => {
      if (typeof item === "number") return item;
      if (item && typeof item === "object") {
        const candidate = item as {
          id?: unknown;
          media?: unknown;
          data?: unknown;
        };

        if (typeof candidate.id === "number") return candidate.id;

        const mediaCandidate = candidate.media as { id?: unknown } | undefined;
        if (mediaCandidate && typeof mediaCandidate.id === "number") {
          return mediaCandidate.id;
        }

        const dataCandidate = candidate.data as { id?: unknown } | undefined;
        if (dataCandidate && typeof dataCandidate.id === "number") {
          return dataCandidate.id;
        }
      }
      return null;
    };

    if (navigationIdsFromState.length > 0) {
      const numericId = id ? Number(id) : NaN;
      if (
        !Number.isNaN(numericId) &&
        !navigationIdsFromState.includes(numericId)
      ) {
        return [...navigationIdsFromState, numericId];
      }
      return navigationIdsFromState;
    }

    const idsFromList = items
      .map(extractItemId)
      .filter((value): value is number => value !== null);

    if (idsFromList.length > 0) {
      return Array.from(new Set(idsFromList));
    }

    if (id) {
      const numericId = Number(id);
      return Number.isNaN(numericId) ? [] : [numericId];
    }

    return [];
  }, [items, navigationIdsFromState, id]);
  const neighbors = useMemo(() => {
    if (!id || !allMediaIdsInView) return { previousId: null, nextId: null };
    const currentIndex = allMediaIdsInView.findIndex(
      (mediaId) => mediaId === Number(id)
    );
    if (currentIndex === -1) return { previousId: null, nextId: null };

    const previousId =
      currentIndex > 0 ? allMediaIdsInView[currentIndex - 1] : null;
    const nextId =
      currentIndex < allMediaIdsInView.length - 1
        ? allMediaIdsInView[currentIndex + 1]
        : null;
    return { previousId, nextId };
  }, [id, allMediaIdsInView]);

  // --- 3. DATA FETCHING & NAVIGATION ---

  const fetchDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!id || routeId.current !== id) return;
      const request = ++detailRequest.current;
      const isCurrent = () => routeId.current === id && request === detailRequest.current && !signal?.aborted;
      setIsDetailLoading(true);
      setLoadError(null);
      try {
        const data = await getMedia(id, signal);
        if (isCurrent()) setDetail(data);
      } catch (err) {
        if (isCurrent()) {
          const status = err && typeof err === "object" && "status" in err ? Number(err.status) : undefined;
          if (status === 404) {
            setDetail(null);
            mutationBus.emit({ type: "media:deleted", ids: [Number(id)] });
          }
          setLoadError({
            status,
            message:
              err instanceof Error ? err.message : "Failed to load media",
          });
        }
      } finally {
        if (isCurrent()) setIsDetailLoading(false);
      }
    },
    [id]
  );

  useUndoRefresh(id ? `media-detail:${id}` : undefined, fetchDetail);
  useMutationRefresh(["face:assigned", "face:detached", "face:deleted", "person:changed", "media:updated"], fetchDetail);

  useEffect(() => {
    const controller = new AbortController();
    const currentPreloaded = location.state?.media as Media | null;
    setDetail(currentPreloaded && String(currentPreloaded.id) === id
      ? { media: currentPreloaded, persons: [], orphans: [] }
      : null);
    setSeekRequest(null);
    videoTimeRef.current = 0;
    setTask(null);
    setTabKey("");
    void fetchDetail(controller.signal);
    return () => {
      controller.abort();
      detailRequest.current += 1;
    };
  }, [id, location.key, location.state?.media, fetchDetail]);

  useEffect(() => {
    let active = true;
    void getConfig()
      .then((cfg) => { if (active) setIsBinary(!!cfg.general.is_binary); })
      .catch(() => { if (active) setIsBinary(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    setCompareSource(null);
    setCompareEnabled(false);
    const media = detail?.media;
    if (!media || !media.filename.includes("_repaired-")) return;
    let cancelled = false;
    void listRepairs({ resultMediaId: media.id, limit: 1 })
      .then((page) => page.items[0] ? getMedia(String(page.items[0].media_id)) : null)
      .then((sourceDetail) => {
        if (!cancelled && sourceDetail) setCompareSource(sourceDetail.media);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [detail?.media.id, detail?.media.filename]);

  const handleNavigate = useCallback(
    (direction: "prev" | "next") => {
      if (hasChildDialog()) return;
      const targetId =
        direction === "prev" ? neighbors.previousId : neighbors.nextId;
      if (!targetId) return;

      navigate(`/medium/${targetId}`, {
        state: buildNavigationState({ media: null, autoplayVideo: true }),
        replace: !!backgroundLocation,
      });
    },
    [navigate, neighbors, buildNavigationState, backgroundLocation, hasChildDialog]
  );

  useEffect(() => {
    if (isMobile && (neighbors.nextId || neighbors.previousId)) {
      const hintShown = sessionStorage.getItem("swipeHintShown");
      if (!hintShown) {
        setShowSwipeHint(true);
        sessionStorage.setItem("swipeHintShown", "true");
      }
    }
  }, [isMobile, neighbors.nextId, neighbors.previousId]);


  useEffect(() => {
    if (!task?.id || ["completed", "cancelled", "failed", "interrupted"].includes(task.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!active || routeId.current !== id) return;
      if (document.hidden) {
        timer = setTimeout(poll, 1500);
        return;
      }
      try {
        const updatedTask = await getTask(task.id);
        if (!active || routeId.current !== id) return;
        setTask(updatedTask);
        if (updatedTask.status === "completed") void fetchDetail();
        else if (!["cancelled", "failed", "interrupted"].includes(updatedTask.status)) timer = setTimeout(poll, 1500);
      } catch {
        if (active) timer = setTimeout(poll, 1500);
      }
    };
    timer = setTimeout(poll, 1500);
    return () => { active = false; clearTimeout(timer); };
  }, [id, task?.id, task?.status, fetchDetail]);

  // Per-media processor runs (started from the "Processors" tab) are fired
  // and forgotten by that tab — reload the detail once one actually
  // completes so newly detected faces/tags/exif show up without a manual
  // refresh. Skip the version seen at mount so this doesn't refetch just
  // because some unrelated processor run finished before this page opened.
  const processorTaskVersion = useTaskCompletionVersion([
    "run_processor_for_media",
  ]);
  const seenProcessorTaskVersionRef = useRef(processorTaskVersion);
  useEffect(() => {
    if (seenProcessorTaskVersionRef.current === processorTaskVersion) return;
    seenProcessorTaskVersionRef.current = processorTaskVersion;
    fetchDetail();
  }, [processorTaskVersion, fetchDetail]);

  const handleTouchStart = (e: React.TouchEvent) =>
    setTouchStartX(e.targetTouches[0].clientX);
  const handleTouchEnd = (e: React.TouchEvent) => {
    const touchEndX = e.changedTouches[0].clientX;
    if (touchStartX === null) return;
    const distance = touchStartX - touchEndX;
    if (distance > 50) handleNavigate("next");
    else if (distance < -50) handleNavigate("prev");
    setTouchStartX(null);
  };
  const handleMediaUpdate = (updatedMedia: Media) => {
    setDetail((prevDetail) => {
      if (!prevDetail || prevDetail.media.id !== updatedMedia.id || String(updatedMedia.id) !== routeId.current) return prevDetail;
      return { ...prevDetail, media: updatedMedia };
    });
  };

  const handleTagAddedToMedia = (newTag: Tag) => {
    setDetail((prevDetail) => {
      if (!prevDetail || String(prevDetail.media.id) !== id || routeId.current !== id) return prevDetail;

      const updatedMedia = {
        ...prevDetail.media,
        tags: [...(prevDetail.media.tags || []), newTag],
      };

      return { ...prevDetail, media: updatedMedia };
    });
  };

  const closeDialog = () => {
    if (actionBusyRef.current) return;
    setDialogType(null);
    setActionTarget(null);
  };
  const openDialog = (type: "convert" | "deleteRecord" | "deleteFile") => {
    if (!detail?.media || String(detail.media.id) !== id || config.PRESENTATION_MODE) return;
    setActionTarget({ id: detail.media.id, mediaListKey });
    setDialogType(type);
  };
  const confirmConvert = async () => {
    if (!actionTarget || actionBusyRef.current) return;
    actionBusyRef.current = true;
    setActionBusy(true);
    try {
      const t = await convertMedia(actionTarget.id);
      setTask(t);
      setSnackbar({
        open: true,
        message: "Conversion started",
        severity: "success",
      });
    } catch {
      setSnackbar({
        open: true,
        message: "Conversion failed",
        severity: "error",
      });
    } finally {
      actionBusyRef.current = false;
      setActionBusy(false);
      closeDialog();
    }
  };
  const navigateAfterDelete = useCallback(() => {
    if (backgroundLocation) {
      navigate(-1);
    } else {
      navigate("/");
    }
  }, [navigate, backgroundLocation]);

  const confirmDeleteRecord = async () => {
    if (!actionTarget || actionBusyRef.current) return;
    const target = actionTarget;
    actionBusyRef.current = true;
    setActionBusy(true);
    try {
      await runOptimistic({
        apply: () => {
          const snapshot = detail;
          if (routeId.current === String(target.id)) {
            setDetail(null);
            setIsDetailLoading(false);
            detailRequest.current += 1;
          }
          return snapshot;
        },
        request: () => deleteMediaRecord(target.id),
        rollback: (snapshot) => {
          if (String(target.id) === routeId.current && snapshot?.media.id === target.id) setDetail(snapshot);
        },
      });
      setSnackbar({
        open: true,
        message: "Record deleted",
        severity: "success",
      });
      if (routeId.current === String(target.id)) navigateAfterDelete();
    } catch {
      setSnackbar({ open: true, message: "Delete failed", severity: "error" });
    } finally {
      actionBusyRef.current = false;
      setActionBusy(false);
      closeDialog();
    }
  };
  const confirmDeleteFile = async () => {
    if (!actionTarget || actionBusyRef.current) return;
    const target = actionTarget;
    actionBusyRef.current = true;
    setActionBusy(true);
    try {
      await runOptimistic({
        apply: () => {
          const snapshot = detail;
          if (routeId.current === String(target.id)) {
            setDetail(null);
            setIsDetailLoading(false);
            detailRequest.current += 1;
          }
          return snapshot;
        },
        request: () => deleteMediaFile(target.id),
        rollback: (snapshot) => {
          if (String(target.id) === routeId.current && snapshot?.media.id === target.id) setDetail(snapshot);
        },
      });
      setSnackbar({ open: true, message: "File deleted", severity: "success" });
      if (routeId.current === String(target.id)) navigateAfterDelete();
    } catch {
      setSnackbar({
        open: true,
        message: "File delete failed",
        severity: "error",
      });
    } finally {
      actionBusyRef.current = false;
      setActionBusy(false);
      closeDialog();
    }
  };

  const handleClose = () => {
    if (hasChildDialog()) return;
    if (backgroundLocation) {
      navigate(-1);
    } else {
      navigate("/");
    }
  };
  const toggleFavorite = async () => {
    if (!detail?.media || favoriteBusy) return;
    const media = detail.media;
    setFavoriteBusy(true);
    try {
      const updatedMedia = await setMediaFavorite(media.id, !media.is_favorite);
      setDetail((current) => current?.media.id === media.id ? { ...current, media: updatedMedia } : current);

    } catch {
      setSnackbar({ open: true, message: "Failed to update favorite", severity: "error" });
    } finally {
      setFavoriteBusy(false);
    }
  };
  const mediaActionsEnabled = !!detail?.media && String(detail.media.id) === id && !isDetailLoading && !config.PRESENTATION_MODE;
  const viewerHotkeys = { scope: "dialog" as const, dialogRef: hotkeyScope };
  useHotkey({ key: "ArrowLeft" }, () => handleNavigate("prev"), {
    ...viewerHotkeys, enabled: !!neighbors.previousId && !isDetailLoading, description: "Previous media",
  });
  useHotkey({ key: "ArrowRight" }, () => handleNavigate("next"), {
    ...viewerHotkeys, enabled: !!neighbors.nextId && !isDetailLoading, description: "Next media",
  });
  useHotkey({ key: "f" }, () => void toggleFavorite(), {
    ...viewerHotkeys, enabled: mediaActionsEnabled && !favoriteBusy, description: "Toggle favorite",
  });
  useHotkey({ key: "e" }, () => setEditorOpen(true), {
    ...viewerHotkeys, enabled: mediaActionsEnabled && typeof detail?.media.duration !== "number", description: "Edit image",
  });
  useHotkey({ key: "Delete" }, () => openDialog("deleteFile"), {
    ...viewerHotkeys, enabled: mediaActionsEnabled, destructive: true, description: "Delete file…",
  });
  useHotkey({ key: "Delete", shift: true }, () => openDialog("deleteRecord"), {
    ...viewerHotkeys, enabled: mediaActionsEnabled, destructive: true, description: "Remove record…",
  });
  useHotkey({ key: "Escape" }, handleClose, { ...viewerHotkeys, description: "Close media viewer" });
  useHotkey({ key: "i" }, () => setInfoVisible((visible) => !visible), {
    ...viewerHotkeys, enabled: !!detail, description: "Toggle media information and tags",
  });
  useHotkey({ key: "?" }, openHotkeyHelp, { ...viewerHotkeys, description: "Show keyboard shortcuts" });
  const isLoading = !detail && (isDetailLoading || actionBusy);

  return (
    <Dialog
      ref={hotkeyScope}
      open={true}
      onClose={handleClose}
      fullWidth
      maxWidth="xl"
      fullScreen={isMobile}
      slotProps={{
        backdrop: { sx: { backgroundColor: (theme) => `rgba(0,0,0,${theme.palette.mode === 'dark' ? 0.85 : 0.8})` } },
        paper: {
          sx: {
            mt: { xs: 0, sm: 4, md: 8 },
            borderRadius: { xs: 0, sm: 2 },
          },
        },
      }}
      sx={{
        "& .MuiDialog-container": {
          alignItems: "flex-start",
        },
      }}
    >
      <IconButton
        aria-label="Close media viewer"
        onClick={handleClose}
        sx={{
          position: "absolute",
          right: 8,
          top: 8,
          zIndex: 1000,
          color: "grey.500",
          bgcolor: { xs: "rgba(0,0,0,0.3)", sm: "transparent" },
          "&:hover": {
             bgcolor: { xs: "rgba(0,0,0,0.5)", sm: "rgba(0,0,0,0.04)" },
          }
        }}
      >
        <CloseIcon />
      </IconButton>
      <DialogContent sx={{ p: { xs: 0, sm: 2, md: 3 } }}>
        {isLoading ? (
          <Box
            sx={{
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              height: "80vh",
            }}
          >
            <CircularProgress />
          </Box>
        ) : loadError ? (
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              gap: 2,
              height: "60vh",
            }}
          >
            <Alert severity="error">{loadError.message}</Alert>
            <Box sx={{ display: "flex", gap: 1 }}>
              <Button variant="contained" onClick={() => fetchDetail()}>
                Retry
              </Button>
              <Button onClick={handleClose}>Close</Button>
            </Box>
          </Box>
        ) : (
          detail && (
            <Container maxWidth="xl" sx={{ pt: { xs: 0, sm: 2 }, pb: { xs: 2, sm: 6 }, px: { xs: 0, sm: 3 } }}>
              {task &&
                (task.status === "running" || task.status === "pending") && (
                  <Box mb={2}>
                    <Typography variant="body2" gutterBottom>
                      Converting… {task.processed}%
                    </Typography>
                    <LinearProgress
                      variant="determinate"
                      value={task.processed}
                      sx={{ height: 8, borderRadius: 1 }}
                    />
                  </Box>
                )}
              <Box
                sx={{
                  position: "relative",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {!isMobile && neighbors.previousId && (
                  <IconButton
                    onClick={() => handleNavigate("prev")}
                    disabled={isDetailLoading || dialogType !== null || editorOpen}
                    sx={{
                      position: "absolute",
                      left: -40,
                      zIndex: 1,
                      "&.Mui-disabled": { opacity: 0.2 },
                    }}
                  >
                    <ArrowBackIosNew fontSize="large" />
                  </IconButton>
                )}
                <Box
                  onTouchStart={handleTouchStart}
                  onTouchEnd={handleTouchEnd}
                  sx={{ width: "90%" }}
                >
                  <MediaHeader
                    media={detail.media}
                    onOpenDialog={openDialog}
                    mediaListKey={mediaListKey}
                    onDeleted={navigateAfterDelete}
                    onEdit={() => setEditorOpen(true)}
                    isBinary={isBinary}
                    onFavoriteChange={handleMediaUpdate}
                    onOpenFolder={async (mediaId) => {
                      try {
                        await openMediaFolder(mediaId);
                      } catch (error: unknown) {
                        const message =
                          error instanceof Error && error.message
                            ? error.message
                            : "Failed to open folder";
                        setSnackbar({ open: true, message, severity: "error" });
                      }
                    }}
                    onOpenFile={async (mediaId) => {
                      try {
                        await openMediaFile(mediaId);
                      } catch (error: unknown) {
                        const message =
                          error instanceof Error && error.message
                            ? error.message
                            : "Failed to open file";
                        setSnackbar({ open: true, message, severity: "error" });
                      }
                    }}
                  />
                  {compareSource && (
                    <Button sx={{ mb: 1 }} variant={compareEnabled ? "contained" : "outlined"} onClick={() => setCompareEnabled((value) => !value)}>
                      Compare
                    </Button>
                  )}
                  {compareEnabled && compareSource ? (
                    <BeforeAfterCompare before={compareSource} after={detail.media} />
                  ) : (
                    <MediaDisplay
                      key={detail.media.id}
                      media={detail.media}
                      initialTime={sceneStartTime ?? undefined}
                      autoplay={shouldAutoplayVideo}
                      seekRequest={seekRequest}
                      onProgress={handleVideoProgress}
                    />
                  )}
                </Box>
                {showSwipeHint && <SwipeHint />}
                {!isMobile && neighbors.nextId && (
                  <IconButton
                    onClick={() => handleNavigate("next")}
                    disabled={isDetailLoading || dialogType !== null || editorOpen}
                    sx={{
                      position: "absolute",
                      right: -40,
                      zIndex: 1,
                      "&.Mui-disabled": { opacity: 0.2 },
                    }}
                  >
                    <ArrowForwardIos fontSize="large" />
                  </IconButton>
                )}
              </Box>
              <ActionDialogs
                dialogType={dialogType}
                loading={actionBusy}
                onClose={closeDialog}
                onConfirmConvert={confirmConvert}
                onConfirmDeleteRecord={confirmDeleteRecord}
                onConfirmDeleteFile={confirmDeleteFile}
              />
              {editorOpen && typeof detail.media.duration !== "number" && (
                <Suspense fallback={<CircularProgress size={24} />}>
                  <ImageEditorDialog
                    open={editorOpen}
                    media={detail.media}
                    mediaListKey={mediaListKey}
                    onClose={() => setEditorOpen(false)}
                    onSaved={(savedDetail, mode) => {
                      if (mode === "overwrite") {
                        const cacheVersion = Date.now();
                        const updatedMedia = {
                          ...savedDetail.media,
                          cache_version: cacheVersion,
                        };
                        handleMediaUpdate(updatedMedia);
                        mutationBus.emit({ type: "media:updated", items: [updatedMedia] });
                        void fetchDetail().then(() => {
                          setDetail((current) =>
                            current?.media.id === savedDetail.media.id && routeId.current === String(savedDetail.media.id)
                              ? {
                                  ...current,
                                  media: { ...current.media, cache_version: cacheVersion },
                                }
                              : current
                          );
                        });
                        setSnackbar({
                          open: true,
                          message: "Original image updated; face processing has started",
                          severity: "success",
                        });
                      }
                    }}
                  />
                </Suspense>
              )}
              {isDetailLoading ? (
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    flexGrow: 1, // Allow the box to grow and fill the minHeight
                  }}
                >
                  <CircularProgress />
                </Box>
              ) : (
                <Box hidden={!infoVisible}>
                  <MediaContentTabs
                    detail={detail}
                    tabKey={tabKey}
                    onTabChange={setTabKey}
                    onTagAdded={handleTagAddedToMedia}
                    onDetailReload={fetchDetail}
                    onTagUpdate={handleMediaUpdate}
                    onSeekRequest={handleSeekRequest}
                    videoTimeRef={videoTimeRef}
                  />
                </Box>
              )}
              <Snackbar
                open={snackbar.open}
                autoHideDuration={3000}
                onClose={() => setSnackbar({ ...snackbar, open: false })}
                anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
              >
                <Alert
                  severity={snackbar.severity}
                  sx={{ width: "100%" }}
                  onClose={() => setSnackbar({ ...snackbar, open: false })}
                >
                  {snackbar.message}
                </Alert>
              </Snackbar>
            </Container>
          )
        )}
      </DialogContent>
    </Dialog>
  );
}
