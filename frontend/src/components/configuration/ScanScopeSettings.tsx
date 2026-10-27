import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Breadcrumbs,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Link,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import FolderIcon from "@mui/icons-material/Folder";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";
import {
  listScanFolders,
  ScanFolderEntry,
  ScanFolderStatus,
  ScanScopeDraft,
} from "../../services/config";
import { ConfigSectionProps } from "./shared";

type PickerMode = "include" | "exclude" | "view";

const NO_ITEMS: string[] = [];

const STATUS_CHIP: Record<
  ScanFolderStatus,
  { label: string; color: "success" | "info" | "default" | "warning" }
> = {
  scanned: { label: "Scanned", color: "success" },
  partial: { label: "Partly scanned", color: "info" },
  outside: { label: "Not scanned", color: "default" },
  excluded: { label: "Skipped", color: "warning" },
};

const parseNameList = (raw: string): string[] => {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of raw.split(",")) {
    const value = item.trim();
    if (!value || seen.has(value.toLowerCase())) continue;
    seen.add(value.toLowerCase());
    out.push(value);
  }
  return out;
};

interface FolderPickerProps {
  open: boolean;
  mode: PickerMode;
  scope: ScanScopeDraft;
  onClose: () => void;
  onChoose: (path: string) => void;
}

function ScanFolderPicker({
  open,
  mode,
  scope,
  onClose,
  onChoose,
}: FolderPickerProps) {
  const [trail, setTrail] = useState<ScanFolderEntry[]>([]);
  const [folders, setFolders] = useState<ScanFolderEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const current = trail.length ? trail[trail.length - 1] : null;
  const parent = current?.path ?? "";
  const scopeKey = JSON.stringify(scope);

  useEffect(() => {
    if (open) setTrail([]);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    listScanFolders(parent, scope, controller.signal)
      .then((listing) => {
        setFolders(listing.folders);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setFolders([]);
        setError(err instanceof Error ? err.message : "Failed to list folders");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
    // scopeKey stands in for the scope object identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, parent, scopeKey]);

  const title =
    mode === "include"
      ? "Choose a folder to scan"
      : mode === "exclude"
        ? "Choose a folder to skip"
        : "Scan scope preview";

  const alreadyListed =
    !!current &&
    (mode === "include"
      ? scope.include_folders.includes(current.path)
      : mode === "exclude"
        ? scope.exclude_folders.includes(current.path)
        : false);

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{title}</DialogTitle>
      <DialogContent dividers sx={{ minHeight: 320 }}>
        <Breadcrumbs sx={{ mb: 1 }} maxItems={5}>
          <Link
            component="button"
            underline="hover"
            onClick={() => setTrail([])}
            color={trail.length ? "inherit" : "text.primary"}
          >
            Media library
          </Link>
          {trail.map((entry, index) => (
            <Link
              key={entry.path}
              component="button"
              underline="hover"
              onClick={() => setTrail((prev) => prev.slice(0, index + 1))}
              color={index === trail.length - 1 ? "text.primary" : "inherit"}
            >
              {entry.name}
            </Link>
          ))}
        </Breadcrumbs>
        {current && (
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
            <Typography variant="body2" color="text.secondary">
              This folder:
            </Typography>
            <Chip
              size="small"
              label={STATUS_CHIP[current.status].label}
              color={STATUS_CHIP[current.status].color}
            />
          </Stack>
        )}
        {error && <Alert severity="error">{error}</Alert>}
        {loading ? (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        ) : (
          !error &&
          (folders.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
              No subfolders here.
            </Typography>
          ) : (
            <List dense disablePadding data-testid="scan-folder-list">
              {folders.map((folder) => (
                <ListItemButton
                  key={folder.path}
                  onClick={() => setTrail((prev) => [...prev, folder])}
                >
                  <ListItemIcon sx={{ minWidth: 36 }}>
                    <FolderIcon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText primary={folder.name} />
                  <Chip
                    size="small"
                    label={STATUS_CHIP[folder.status].label}
                    color={STATUS_CHIP[folder.status].color}
                    sx={{ mr: 1 }}
                  />
                  <ChevronRightIcon
                    fontSize="small"
                    sx={{ visibility: folder.has_subfolders ? "visible" : "hidden" }}
                  />
                </ListItemButton>
              ))}
            </List>
          ))
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>{mode === "view" ? "Close" : "Cancel"}</Button>
        {mode !== "view" && (
          <Button
            variant="contained"
            disabled={!current || alreadyListed}
            onClick={() => current && onChoose(current.path)}
          >
            {!current
              ? "Open a folder to choose it"
              : alreadyListed
                ? "Already listed"
                : mode === "include"
                  ? `Scan “${current.name}”`
                  : `Skip “${current.name}”`}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

interface FolderChipListProps {
  label: string;
  empty: string;
  folders: string[];
  onRemove: (path: string) => void;
  onAdd: () => void;
  testId: string;
}

function FolderChipList({
  label,
  empty,
  folders,
  onRemove,
  onAdd,
  testId,
}: FolderChipListProps) {
  return (
    <Box data-testid={testId}>
      <Typography variant="subtitle2" gutterBottom>
        {label}
      </Typography>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center">
        {folders.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
            {empty}
          </Typography>
        ) : (
          folders.map((path) => (
            <Chip
              key={path}
              icon={<FolderIcon />}
              label={path}
              onDelete={() => onRemove(path)}
              sx={{ maxWidth: "100%" }}
            />
          ))
        )}
        <Button size="small" startIcon={<AddIcon />} onClick={onAdd}>
          Add folder
        </Button>
      </Stack>
    </Box>
  );
}

/**
 * Settings for which folders under the media library a scan walks.
 * The backend rule lives in app/scan_scope.py; the picker previews it live.
 */
export default function ScanScopeSettings({
  config,
  onValueChange,
}: ConfigSectionProps) {
  const [picker, setPicker] = useState<PickerMode | null>(null);
  const [patternsText, setPatternsText] = useState<string | null>(null);

  const include = config.scan.include_folders ?? NO_ITEMS;
  const exclude = config.scan.exclude_folders ?? NO_ITEMS;
  const patterns = config.scan.exclude_folder_patterns ?? NO_ITEMS;
  const scope = useMemo<ScanScopeDraft>(
    () => ({
      include_folders: include,
      exclude_folders: exclude,
      exclude_folder_patterns: patterns,
    }),
    [include, exclude, patterns]
  );

  const choose = (path: string) => {
    if (picker === "include") {
      onValueChange("scan", "include_folders", [...include, path]);
    } else if (picker === "exclude") {
      onValueChange("scan", "exclude_folders", [...exclude, path]);
    }
    setPicker(null);
  };

  return (
    <Stack spacing={2} data-testid="scan-scope-settings">
      <Box>
        <Typography variant="h6">Scan Scope</Typography>
        <Typography variant="body2" color="text.secondary">
          Choose which folders in the media library a scan looks in. Skipped
          folders win over scanned ones. Files already in the library stay
          when you narrow the scope; they are just no longer picked up when
          new.
        </Typography>
      </Box>
      <FolderChipList
        testId="scan-include-folders"
        label="Only scan these folders"
        empty="Entire media library"
        folders={include}
        onRemove={(path) =>
          onValueChange(
            "scan",
            "include_folders",
            include.filter((p) => p !== path)
          )
        }
        onAdd={() => setPicker("include")}
      />
      <FolderChipList
        testId="scan-exclude-folders"
        label="Never scan these folders"
        empty="None"
        folders={exclude}
        onRemove={(path) =>
          onValueChange(
            "scan",
            "exclude_folders",
            exclude.filter((p) => p !== path)
          )
        }
        onAdd={() => setPicker("exclude")}
      />
      <TextField
        label="Skip folders named"
        value={patternsText ?? patterns.join(", ")}
        onChange={(e) => setPatternsText(e.target.value)}
        onBlur={(e) => {
          onValueChange(
            "scan",
            "exclude_folder_patterns",
            parseNameList(e.target.value)
          );
          setPatternsText(null);
        }}
        fullWidth
        helperText="Comma-separated folder names skipped at any depth. Wildcards work and case is ignored, e.g. @eaDir, *.photoslibrary, Backup*"
        inputProps={{ "data-testid": "scan-exclude-patterns" }}
      />
      <Box>
        <Button
          variant="outlined"
          startIcon={<ManageSearchIcon />}
          onClick={() => setPicker("view")}
        >
          Preview scan scope
        </Button>
      </Box>
      <ScanFolderPicker
        open={picker !== null}
        mode={picker ?? "view"}
        scope={scope}
        onClose={() => setPicker(null)}
        onChoose={choose}
      />
    </Stack>
  );
}
