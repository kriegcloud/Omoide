import type { ReactNode } from "react";
import { Alert, Box, Button, Skeleton, Stack, Typography } from "@mui/material";

export interface ListStateProps {
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  filtered?: boolean;
  onRetry?: () => void;
  onClearFilters?: () => void;
  emptyMessage?: string;
  action?: ReactNode;
}
/** Error wins over empty: a failed request says nothing about list membership. */
export default function ListState({ loading, error, empty, filtered, onRetry, onClearFilters, emptyMessage, action }: ListStateProps) {
  if (error) return <Alert severity="error" sx={{ my: 2 }} action={onRetry && <Button color="inherit" onClick={onRetry}>Retry</Button>}>{error}</Alert>;
  if (loading) return <Box role="status" aria-label="Loading items" sx={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 2, py: 2 }}>{Array.from({ length: 6 }, (_, index) => <Skeleton key={index} variant="rounded" height={200} />)}</Box>;
  if (!empty) return null;
  return <Stack role="status" alignItems="center" spacing={2} sx={{ py: 5 }}><Typography color="text.secondary">{emptyMessage ?? (filtered ? "No items match these filters." : "Nothing here yet.")}</Typography>{filtered && onClearFilters ? <Button onClick={onClearFilters}>Clear filters</Button> : action}</Stack>;
}
