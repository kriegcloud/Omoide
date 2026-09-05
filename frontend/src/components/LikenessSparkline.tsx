import { Box } from "@mui/material";
import type { LikenessStep } from "../types";

export interface LikenessSeries {
  id: number;
  color: string;
  points: LikenessStep[];
}

interface LikenessSparklineProps {
  series: LikenessSeries[];
  height?: number;
  label?: string;
}

export default function LikenessSparkline({ series, height = 44, label = "Likeness by training step" }: LikenessSparklineProps) {
  const width = 180;
  const padding = 3;
  const points = series.flatMap((entry) => entry.points);
  const minStep = Math.min(...points.map((point) => point.step), 0);
  const maxStep = Math.max(...points.map((point) => point.step), 1);
  const x = (step: number) => padding + ((step - minStep) / Math.max(1, maxStep - minStep)) * (width - padding * 2);
  const y = (value: number) => padding + ((1 - Math.max(-1, Math.min(1, value))) / 2) * (height - padding * 2);

  return (
    <Box
      component="svg"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      sx={{ display: "block", width: "100%", height }}
    >
      <line x1={padding} x2={width - padding} y1={y(0)} y2={y(0)} stroke="currentColor" opacity="0.12" />
      {series.map((entry) => entry.points.length > 0 && (
        <g key={entry.id}>
          <polyline
            points={entry.points.map((point) => `${x(point.step)},${y(point.mean)}`).join(" ")}
            fill="none"
            stroke={entry.color}
            strokeWidth="2.25"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
          {entry.points.length === 1 && (
            <circle cx={x(entry.points[0].step)} cy={y(entry.points[0].mean)} r="2.5" fill={entry.color} />
          )}
        </g>
      ))}
    </Box>
  );
}
