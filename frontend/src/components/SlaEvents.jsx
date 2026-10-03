import React, { useState } from "react";
import { fmtMinutes, fmtTime } from "../format.js";

export default function SlaEvents({ events }) {
  const [openId, setOpenId] = useState(null);

  return (
    <section className="block">
      <h3>警告与升级记录（重启 / 重复扫描只登记一次）</h3>
      {events.length === 0 && (
        <div className="muted">尚未触发任何警告或升级。</div>
      )}
      <ul className="sla-list">
        {events.map((e) => {
          const open = openId === e.id;
          const backfilled = e.basis?.trigger === "migration_backfill";
          return (
            <li key={e.id} className={`sla sla-${e.kind}`}>
              <button
                className="sla-summary"
                onClick={() => setOpenId(open ? null : e.id)}
              >
                <span className="badge sla-kind">
                  {e.kind === "warn" ? "⚠ 警告" : "↑ 升级"}
                </span>
                <span className="sla-ref">{e.ref}</span>
                <span className="muted">
                  阈值 {fmtMinutes(e.threshold_minutes)} ·
                  跨越时刻 {fmtTime(e.crossed_at)}
                </span>
                <span className="muted">依据策略 v{e.policy_version}</span>
                {backfilled && (
                  <span className="badge backfill">迁移补登</span>
                )}
                {e.migration_id && (
                  <span className="badge migrated">
                    关联迁移 #{e.migration_id}
                  </span>
                )}
                <span className="caret">{open ? "▾" : "▸"} 依据</span>
              </button>
              {open && (
                <BasisView basis={e.basis} />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function BasisView({ basis }) {
  if (!basis) return <div className="muted small">无依据快照</div>;
  return (
    <div className="basis">
      <div className="basis-grid">
        <div>
          <div className="muted small">裁决时间</div>
          <div>{fmtTime(basis.decided_at)}</div>
        </div>
        <div>
          <div className="muted small">累积有效工作分钟</div>
          <div>
            <strong>{fmtMinutes(basis.accrued_work_minutes)}</strong>
          </div>
        </div>
        <div>
          <div className="muted small">策略版本</div>
          <div>
            {basis.policy_key} v{basis.policy_version}
          </div>
        </div>
        <div>
          <div className="muted small">触发方式</div>
          <div>{basis.trigger === "timer" ? "后台计时器" : "策略迁移补登"}</div>
        </div>
        <div>
          <div className="muted small">时钟状态</div>
          <div>
            {basis.clock_running
              ? `运行中（自 ${fmtTime(basis.open_since)}）`
              : basis.open_pause_since
                ? `暂停中（自 ${fmtTime(basis.open_pause_since)}）`
                : "停止（已解决）"}
          </div>
        </div>
      </div>
      <EvidenceIntervals title="裁决时已计入区间"
        ivs={basis.counted_intervals} />
      <EvidenceIntervals title="裁决时暂停区间"
        ivs={basis.pause_intervals} />
      <EvidenceIntervals title="工作日历切片（假日切口后）"
        ivs={basis.working_slices} />
    </div>
  );
}

function EvidenceIntervals({ title, ivs }) {
  return (
    <div className="basis-iv">
      <div className="muted small">{title}</div>
      {(!ivs || ivs.length === 0) ? (
        <span className="muted small">无</span>
      ) : (
        <ul>
          {ivs.map(([s, e], i) => (
            <li key={i}>
              {fmtTime(s)} → {fmtTime(e)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
