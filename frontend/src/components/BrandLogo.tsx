import { Box, Typography } from "@mui/material";

export default function BrandLogo() {
  const base = import.meta.env.BASE_URL || "/";
  return <Box sx={{ display: "flex", alignItems: "center", gap: 1, color: "text.primary" }}>
    <Box component="img" src={`${base}brand/mark.svg`} alt="" aria-hidden="true"
      sx={{ width: 32, height: 32, p: 0.5, bgcolor: "#111113", borderRadius: 1 }} />
    <Typography component="span" sx={{ fontWeight: 600, fontSize: "1.1rem", letterSpacing: "-0.03em", whiteSpace: "nowrap" }}>
      beep media
    </Typography>
  </Box>;
}
