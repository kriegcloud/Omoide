import type { ReactNode } from "react";
import { Box, Container, Typography } from "@mui/material";
import type { ContainerProps } from "@mui/material";

export interface GalleryPageProps extends Omit<ContainerProps, "title"> {
  title?: ReactNode;
  /** The current result count; omit when the endpoint has no total. */
  count?: number;
  countLabel?: string;
  tabs?: ReactNode;
  filters?: ReactNode;
  sort?: ReactNode;
  actions?: ReactNode;
  selection?: ReactNode;
}

/** Shared full-width gallery chrome. Slots retain each page's own controls. */
export default function GalleryPage({ title, count, countLabel = "items", tabs, filters, sort, actions, selection, children, sx, ...props }: GalleryPageProps) {
  return (
    <Container {...props} maxWidth={false} sx={[
      { width: "100%", minWidth: 0, minHeight: "100vh", bgcolor: "background.default", color: "text.primary", py: 2, px: { xs: 2, sm: 3 }, "& .gallery-controls": { display: "flex", alignItems: "center", flexWrap: "wrap", gap: 1 } },
      ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
    ]}>
      {(title || count !== undefined || sort || actions) && (
        <Box className="gallery-controls" sx={{ mb: 2 }}>
          {title && <Typography component="h1" variant="h5" fontWeight={700}>{title}</Typography>}
          {count !== undefined && <Typography variant="body2" color="text.secondary" role="status" aria-live="polite">{count.toLocaleString()} {countLabel}</Typography>}
          <Box sx={{ flex: 1 }} />
          {sort}{actions}
        </Box>
      )}
      {tabs && <Box sx={{ mb: 2 }}>{tabs}</Box>}
      {filters && <Box className="gallery-controls" sx={{ mb: 2 }}>{filters}</Box>}
      {selection}
      {children}
    </Container>
  );
}
