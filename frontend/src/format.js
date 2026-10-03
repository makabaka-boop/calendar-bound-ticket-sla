// Display helpers for UTC timestamps and minute durations.
export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(
    d.getUTCDate()
  )} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}Z`;
}

export function fmtMinutes(min) {
  if (min == null) return "—";
  const m = Math.round(min * 10) / 10;
  if (m < 60) return `${m} 分钟`;
  const h = Math.floor(m / 60);
  const rem = Math.round(m % 60);
  return rem ? `${h} 小时 ${rem} 分` : `${h} 小时`;
}

export function intervalLenMin([s, e]) {
  return (new Date(e) - new Date(s)) / 60000;
}
