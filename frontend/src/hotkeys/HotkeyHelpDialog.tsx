import { Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, List, ListItem, Typography } from "@mui/material";
import type { HelpBinding } from "./HotkeyContext";
import { formatBinding, type HotkeyScope } from "./keymap";
import { useDialogHotkeyScope, useHotkey } from "./useHotkey";

export default function HotkeyHelpDialog({ open, bindings, onClose }: { open: boolean; bindings: HelpBinding[]; onClose: () => void }) {
  const dialogRef = useDialogHotkeyScope(open);
  useHotkey({ key: "?" }, onClose, { scope: "dialog", dialogRef, enabled: open, description: "Close keyboard shortcuts" });
  return <Dialog ref={dialogRef} open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="hotkey-help-title">
    <DialogTitle id="hotkey-help-title">Keyboard shortcuts</DialogTitle>
    <DialogContent>
      <Typography color="text.secondary" variant="body2">Shortcuts pause while typing or using a menu. A dialog takes priority over the page. Destructive actions open a confirmation.</Typography>
      {(["dialog", "page", "global"] as HotkeyScope[]).map(scope => {
        const entries = bindings.filter(binding => binding.scope === scope);
        if (!entries.length) return null;
        return <Box key={scope} sx={{ mt: 2 }}>
          <Typography component="h3" variant="subtitle1" sx={{ textTransform: "capitalize" }}>{scope}</Typography>
          <List dense disablePadding>{entries.map((binding, index) => <ListItem key={`${formatBinding(binding)}-${index}`} sx={{ px: 0, gap: 2, justifyContent: "space-between" }}>
            <Typography variant="body2">{binding.description}</Typography>
            <Box component="kbd" sx={{ whiteSpace: "nowrap", border: "1px solid", borderColor: "divider", borderRadius: 1, px: 1 }}>{formatBinding(binding)}</Box>
          </ListItem>)}</List>
        </Box>;
      })}
    </DialogContent>
    <DialogActions><Button autoFocus onClick={onClose}>Close</Button></DialogActions>
  </Dialog>;
}
