import { useEffect, useMemo, useState } from "react";
import AddIcon from "@mui/icons-material/Add";
import FacebookIcon from "@mui/icons-material/Facebook";
import InstagramIcon from "@mui/icons-material/Instagram";
import LinkIcon from "@mui/icons-material/Link";
import XIcon from "@mui/icons-material/X";
import YouTubeIcon from "@mui/icons-material/YouTube";
import {
  Box,
  Button,
  Chip,
  FormControl,
  InputLabel,
  MenuItem,
  Popover,
  Select,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import type { SelectChangeEvent } from "@mui/material/Select";
import config from "../config";
import {
  addSocialLink,
  deleteSocialLink,
  getSocialLinks,
  getSocialLinkSuggestions,
} from "../services/personActions";
import type {
  SocialLink,
  SocialLinkSuggestion,
  SocialPlatform,
} from "../types";
import ConfirmDialog from "./ConfirmDialog";

const PLATFORMS: Array<{ value: SocialPlatform; label: string }> = [
  { value: "instagram", label: "Instagram" },
  { value: "tiktok", label: "TikTok" },
  { value: "x", label: "X" },
  { value: "youtube", label: "YouTube" },
  { value: "onlyfans", label: "OnlyFans" },
  { value: "threads", label: "Threads" },
  { value: "facebook", label: "Facebook" },
  { value: "snapchat", label: "Snapchat" },
  { value: "other", label: "Other" },
];

const normalizeHandle = (platform: SocialPlatform, handle: string) => {
  let normalized = handle.trim().replace(/\/+$/, "");
  if (platform === "x") {
    try {
      const candidate = normalized.includes("://")
        ? normalized
        : `https://${normalized}`;
      const parsed = new URL(candidate);
      const hostname = parsed.hostname.replace(/^www\./, "").toLowerCase();
      if (hostname === "x.com" || hostname === "twitter.com") {
        normalized = parsed.pathname.split("/").filter(Boolean)[0] ?? "";
      }
    } catch {
      // A handle is not expected to parse as a URL.
    }
  }
  return normalized.replace(/^@+/, "").trim();
};

const deriveUrl = (platform: SocialPlatform, handle: string) => {
  const normalized = normalizeHandle(platform, handle);
  const templates: Partial<Record<SocialPlatform, string>> = {
    instagram: `https://instagram.com/${normalized}`,
    tiktok: `https://tiktok.com/@${normalized}`,
    x: `https://x.com/${normalized}`,
    youtube: `https://youtube.com/@${normalized}`,
    onlyfans: `https://onlyfans.com/${normalized}`,
    threads: `https://threads.net/@${normalized}`,
    facebook: `https://facebook.com/${normalized}`,
    snapchat: `https://snapchat.com/add/${normalized}`,
  };
  return templates[platform] ?? "";
};

const platformIcon = (platform: SocialPlatform) => {
  switch (platform) {
    case "instagram":
      return <InstagramIcon />;
    case "x":
      return <XIcon />;
    case "youtube":
      return <YouTubeIcon />;
    case "facebook":
      return <FacebookIcon />;
    default:
      return <LinkIcon />;
  }
};

interface PersonSocialLinksProps {
  personId: number;
  initialLinks: SocialLink[];
}

export function PersonSocialLinks({
  personId,
  initialLinks,
}: PersonSocialLinksProps) {
  const [links, setLinks] = useState(initialLinks);
  const [suggestions, setSuggestions] = useState<SocialLinkSuggestion[]>([]);
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const [platform, setPlatform] = useState<SocialPlatform>("instagram");
  const [handle, setHandle] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [pendingDelete, setPendingDelete] = useState<SocialLink | null>(null);

  useEffect(() => {
    setLinks(initialLinks);
  }, [initialLinks]);

  useEffect(() => {
    const controller = new AbortController();
    const requests: Promise<unknown>[] = [
      getSocialLinks(personId, controller.signal).then(setLinks),
    ];
    if (!config.PRESENTATION_MODE) {
      requests.push(
        getSocialLinkSuggestions(personId, controller.signal).then(
          setSuggestions,
        ),
      );
    }
    Promise.all(requests).catch((requestError) => {
      if (requestError.name !== "AbortError") {
        setError(requestError.message || "Failed to load social links");
      }
    });
    return () => controller.abort();
  }, [personId]);

  const previewUrl = useMemo(
    () => (platform === "other" ? customUrl.trim() : deriveUrl(platform, handle)),
    [customUrl, handle, platform],
  );

  const openPopover = (
    target: HTMLElement,
    suggestion?: SocialLinkSuggestion,
  ) => {
    setPlatform(suggestion?.platform ?? "instagram");
    setHandle(suggestion?.handle ?? "");
    setCustomUrl("");
    setError("");
    setAnchorEl(target);
  };

  const closePopover = () => {
    if (!saving) setAnchorEl(null);
  };

  const handleAdd = async () => {
    setSaving(true);
    setError("");
    try {
      const link = await addSocialLink(personId, {
        platform,
        handle,
        ...(platform === "other" ? { url: customUrl.trim() } : {}),
      });
      setLinks((current) => [...current, link]);
      setSuggestions((current) =>
        current.filter(
          (suggestion) =>
            suggestion.handle !== handle ||
            (suggestion.platform ?? "instagram") !== platform,
        ),
      );
      setAnchorEl(null);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to add social link",
      );
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!pendingDelete) return;
    setSaving(true);
    setError("");
    try {
      await deleteSocialLink(personId, pendingDelete.id);
      setLinks((current) =>
        current.filter((link) => link.id !== pendingDelete.id),
      );
      setPendingDelete(null);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Failed to delete social link",
      );
    } finally {
      setSaving(false);
    }
  };

  const visibleSuggestions = suggestions.filter(
    (suggestion) =>
      !links.some(
        (link) =>
          link.handle === suggestion.handle &&
          link.platform === (suggestion.platform ?? "instagram"),
      ),
  );

  return (
    <Box sx={{ mt: 1.5 }}>
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
        {links.map((link) => (
          <Chip
            key={link.id}
            component="a"
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            clickable
            icon={platformIcon(link.platform)}
            label={link.handle}
            onDelete={
              config.PRESENTATION_MODE
                ? undefined
                : (event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setPendingDelete(link);
                  }
            }
          />
        ))}
        {!config.PRESENTATION_MODE && (
          <Button
            size="small"
            startIcon={<AddIcon />}
            onClick={(event) => openPopover(event.currentTarget)}
          >
            Add link
          </Button>
        )}
      </Stack>

      {!config.PRESENTATION_MODE && visibleSuggestions.length > 0 && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary">
            Suggestions
          </Typography>
          <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" mt={0.5}>
            {visibleSuggestions.map((suggestion) => (
              <Chip
                key={`${suggestion.source_folder}-${suggestion.platform ?? "unknown"}`}
                variant="outlined"
                icon={<AddIcon />}
                label={`${suggestion.handle} · ${suggestion.media_count}`}
                onClick={(event) =>
                  openPopover(event.currentTarget, suggestion)
                }
              />
            ))}
          </Stack>
        </Box>
      )}

      {error && (
        <Typography variant="caption" color="error" display="block" mt={1}>
          {error}
        </Typography>
      )}

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={closePopover}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
      >
        <Stack spacing={2} sx={{ p: 2, width: 320 }}>
          <Typography variant="subtitle1">Add social link</Typography>
          <FormControl fullWidth size="small">
            <InputLabel id="social-platform-label">Platform</InputLabel>
            <Select
              labelId="social-platform-label"
              label="Platform"
              value={platform}
              onChange={(event: SelectChangeEvent) =>
                setPlatform(event.target.value as SocialPlatform)
              }
            >
              {PLATFORMS.map((option) => (
                <MenuItem key={option.value} value={option.value}>
                  {option.label}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <TextField
            size="small"
            label="Handle"
            value={handle}
            onChange={(event) => setHandle(event.target.value)}
            autoFocus
            required
          />
          {platform === "other" && (
            <TextField
              size="small"
              label="URL"
              type="url"
              value={customUrl}
              onChange={(event) => setCustomUrl(event.target.value)}
              required
            />
          )}
          {previewUrl && (
            <Typography variant="caption" color="text.secondary" sx={{ wordBreak: "break-all" }}>
              {previewUrl}
            </Typography>
          )}
          <Stack direction="row" justifyContent="flex-end" spacing={1}>
            <Button onClick={closePopover} disabled={saving}>
              Cancel
            </Button>
            <Button
              variant="contained"
              onClick={() => void handleAdd()}
              disabled={
                saving ||
                !normalizeHandle(platform, handle) ||
                (platform === "other" && !customUrl.trim())
              }
            >
              Add
            </Button>
          </Stack>
        </Stack>
      </Popover>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete social link?"
        message={`Remove ${pendingDelete?.handle ?? "this link"} from this person?`}
        confirmLabel="Delete"
        loading={saving}
        onConfirm={() => void handleDelete()}
        onClose={() => setPendingDelete(null)}
      />
    </Box>
  );
}
