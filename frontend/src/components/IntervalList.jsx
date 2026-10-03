import React from "react";
import { fmtMinutes, fmtTime, intervalLenMin } from "../format.js";

export default function IntervalList({ title, intervals, tone }) {
  const total = (intervals || []).reduce(
    (sum, iv) => sum + intervalLenMin(iv), 0);
  return (
    <div className={`iv-block iv-${tone}`}>
      <div className="iv-head">
        <strong>{title}</strong>
        <span className="muted">
          {intervals.length} 段 · 共 {fmtMinutes(total)}
        </span>
      </div>
      {intervals.length === 0 ? (
        <div className="muted small">无</div>
      ) : (
        <ul className="iv-list">
          {intervals.map(([s, e], i) => (
            <li key={i}>
              <span className="iv-range">
                {fmtTime(s)} → {fmtTime(e)}
              </span>
              <span className="iv-len">{fmtMinutes(intervalLenMin([s, e]))}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
