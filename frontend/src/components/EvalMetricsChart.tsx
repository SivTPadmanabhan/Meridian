"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface EvalChartPoint {
  day: string;
  faithfulness: number | null;
  hallucination_rate: number | null;
}

/**
 * Client island (Recharts needs the browser). Data is passed as props from the
 * /eval Server Component — this component never fetches. Reference lines mark
 * the faithfulness ≥ 0.85 and hallucination ≤ 0.10 targets (TODO Phase 4).
 */
export function EvalMetricsChart({ data }: { data: EvalChartPoint[] }) {
  if (data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No eval results in the last 30 days.
      </p>
    );
  }

  return (
    <div className="h-80 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis dataKey="day" tick={{ fontSize: 12 }} />
          <YAxis domain={[0, 1]} tick={{ fontSize: 12 }} />
          <Tooltip />
          <Legend />
          <ReferenceLine
            y={0.85}
            stroke="#16a34a"
            strokeDasharray="4 4"
            label={{ value: "faithfulness 0.85", position: "insideTopRight", fontSize: 11 }}
          />
          <ReferenceLine
            y={0.1}
            stroke="#dc2626"
            strokeDasharray="4 4"
            label={{ value: "hallucination 0.10", position: "insideBottomRight", fontSize: 11 }}
          />
          <Line
            type="monotone"
            dataKey="faithfulness"
            name="Faithfulness"
            stroke="#16a34a"
            strokeWidth={2}
            connectNulls
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="hallucination_rate"
            name="Hallucination rate"
            stroke="#dc2626"
            strokeWidth={2}
            connectNulls
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
