import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  Alert,
  Autocomplete,
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
  Paper,
  type PaperProps,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import ChevronRightIcon from "@mui/icons-material/ChevronRight";
import FolderIcon from "@mui/icons-material/Folder";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";
import SearchIcon from "@mui/icons-material/Search";
import {
  listScanFolders,
  ScanFolderCrumb,
  ScanFolderEntry,
  ScanFolderMatch,
  ScanFolderStatus,
  ScanScopeDraft,
  searchScanFolders,
} from "../../services/config";
import {
  useDialogHotkeyScope,
  useHotkey,
  useHotkeyHelp,
} from "../../hotkeys/useHotkey";
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

const SEARCH_MIN_LENGTH = 2;
const SEARCH_DEBOUNCE_MS = 250;
/** While the server builds its folder index, re-ask this often (bounded). */
const SEARCH_INDEX_RETRY_MS = 2000;
const SEARCH_INDEX_MAX_RETRIES = 30;
const NO_MATCHES: ScanFolderMatch[] = [];

/** A query containing "/" matches relative paths, otherwise folder names. */
const matchesFolderQuery = (folder: ScanFolderCrumb, query: string) => {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return (needle.includes("/") ? folder.path : folder.name)
    .toLowerCase()
    .includes(needle.replace(/^\/+|\/+$/g, ""));
};

const SearchHintContext = createContext<string | null>(null);

/** Autocomplete popup with a footer hint when the backend truncated results. */
function SearchResultsPaper({ children, ...props }: PaperProps) {
  const hint = useContext(SearchHintContext);
  return (
    <Paper {...props}>
      {children}
      {hint && (
        <Typography
          variant="caption"
          color="text.secondary"
          component="div"
          data-testid="scan-folder-search-hint"
          onMouseDown={(event) => event.preventDefault()}
          sx={{ px: 2, py: 1, borderTop: 1, borderColor: "divider" }}
        >
          {hint}
        </Typography>
      )}
    </Paper>
  );
}

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
  const [trail, setTrail] = useState<ScanFolderCrumb[]>([]);
  const [folders, setFolders] = useState<ScanFolderEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<ScanFolderMatch[]>(NO_MATCHES);
  const [truncated, setTruncated] = useState(false);
  const [indexed, setIndexed] = useState(true);
  const [indexRetry, setIndexRetry] = useState(0);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [popupOpen, setPopupOpen] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const dialogRef = useDialogHotkeyScope(open);
  const openHelp = useHotkeyHelp();
  useHotkey({ key: "/" }, () => searchInputRef.current?.focus(), {
    scope: "dialog",
    dialogRef,
    enabled: open,
    description: "Search folders",
  });
  useHotkey({ key: "?" }, openHelp, {
    scope: "dialog",
    dialogRef,
    enabled: open,
    description: "Show keyboard shortcuts",
  });

  const current = trail.length ? trail[trail.length - 1] : null;
  const parent = current?.path ?? "";
  const scopeKey = JSON.stringify(scope);
  const searchable = query.trim().length >= SEARCH_MIN_LENGTH;

  const updateQuery = (value: string) => {
    setQuery(value);
    setIndexRetry(0);
  };

  useEffect(() => {
    if (open) {
      setTrail([]);
      updateQuery("");
    }
  }, [open]);

  useEffect(() => {
    if (!open || !searchable) {
      setMatches(NO_MATCHES);
      setTruncated(false);
      setIndexed(true);
      setSearching(false);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    let retryTimer: number | undefined;
    setSearching(true);
    const timer = window.setTimeout(() => {
      searchScanFolders(query.trim(), scope, controller.signal)
        .then((result) => {
          setMatches(result.folders);
          setTruncated(result.truncated);
          setIndexed(result.indexed);
          setSearchError(null);
          if (!result.indexed && indexRetry < SEARCH_INDEX_MAX_RETRIES) {
            retryTimer = window.setTimeout(
              () => setIndexRetry((n) => n + 1),
              SEARCH_INDEX_RETRY_MS
            );
          }
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setMatches(NO_MATCHES);
          setTruncated(false);
          setSearchError(
            err instanceof Error ? err.message : "Failed to search folders"
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, indexRetry ? 0 : SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      window.clearTimeout(retryTimer);
      controller.abort();
    };
    // scopeKey stands in for the scope object identity; indexRetry re-asks
    // while the server's folder index is still building.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, query, searchable, scopeKey, indexRetry]);

  const visibleFolders = useMemo(
    () => folders.filter((folder) => matchesFolderQuery(folder, query)),
    [folders, query]
  );

  const goToMatch = (match: ScanFolderMatch) => {
    setTrail([...match.trail, match]);
    updateQuery("");
    setPopupOpen(false);
  };

  const onSearchKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement> & { defaultMuiPrevented?: boolean }
  ) => {
    // First Esc clears the field; with the field empty, Esc closes the dialog.
    if (event.key !== "Escape" || !query) return;
    event.defaultMuiPrevented = true;
    event.preventDefault();
    event.stopPropagation();
    updateQuery("");
    setPopupOpen(false);
  };

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
    <Dialog ref={dialogRef} open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle>{title}</DialogTitle>
      <Box sx={{ px: 3, pb: 2 }}>
        <SearchHintContext.Provider
          value={
            !popupOpen || !searchable || searchError
              ? null
              : !indexed
                ? "Still indexing folders, results may be incomplete…"
                : truncated && matches.length
                  ? `Showing first ${matches.length} matches, refine your search`
                  : null
          }
        >
          <Autocomplete<ScanFolderMatch, false, false, false>
            options={matches}
            value={null}
            inputValue={query}
            onInputChange={(_, value, reason) => {
              if (reason === "input" || reason === "clear") updateQuery(value);
            }}
            onChange={(_, match) => match && goToMatch(match)}
            open={popupOpen && searchable}
            onOpen={() => setPopupOpen(true)}
            onClose={() => setPopupOpen(false)}
            onKeyDown={onSearchKeyDown}
            filterOptions={(options) => options}
            getOptionLabel={(option) => option.path}
            isOptionEqualToValue={(option, value) => option.path === value.path}
            loading={searching}
            loadingText="Searching…"
            noOptionsText={
              searchError ?? (indexed ? "No folders match" : "No folders match yet")
            }
            autoHighlight
            blurOnSelect={false}
            slots={{ paper: SearchResultsPaper }}
            renderOption={(props, option) => {
              const { key, ...optionProps } = props as typeof props & {
                key: string;
              };
              return (
                <Box
                  component="li"
                  key={key}
                  {...optionProps}
                  data-testid="scan-folder-search-option"
                  sx={{ display: "flex", alignItems: "center", gap: 1.5 }}
                >
                  <FolderIcon fontSize="small" color="action" />
                  <ListItemText
                    primary={option.name}
                    secondary={option.path}
                    slotProps={{ secondary: { noWrap: true } }}
                    sx={{ minWidth: 0, my: 0 }}
                  />
                  <Chip
                    size="small"
                    label={STATUS_CHIP[option.status].label}
                    color={STATUS_CHIP[option.status].color}
                    sx={{ flexShrink: 0 }}
                  />
                </Box>
              );
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                inputRef={searchInputRef}
                autoFocus
                size="small"
                placeholder="Search folders (press /)"
                slotProps={{
                  input: {
                    ...params.InputProps,
                    startAdornment: (
                      <SearchIcon fontSize="small" color="action" sx={{ mr: 1 }} />
                    ),
                  },
                  htmlInput: {
                    ...params.inputProps,
                    "aria-label": "Search folders",
                    "data-testid": "scan-folder-search",
                  },
                }}
              />
            )}
          />
        </SearchHintContext.Provider>
      </Box>
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
          (visibleFolders.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
              {folders.length === 0
                ? "No subfolders here."
                : "No subfolders here match."}
            </Typography>
          ) : (
            <List dense disablePadding data-testid="scan-folder-list">
              {visibleFolders.map((folder) => (
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
