import React, { useState } from "react";
import { fmtMinutes, fmtTime, intervalLenMin } from "../format.js";
import IntervalList from "./IntervalList.jsx";
import EventTimeline from "./EventTimeline.jsx";
import SlaEvents from "./SlaEvents.jsx";
import MigrationPanel from "./MigrationPanel.jsx";

const STATUS_LABEL = {
  open: "计时中",
  paused: "等待客户（暂停）",
  resolved: "已解决",
};

export default function TicketDetail({
  detail, policies, onStatus, onMigrated, setError, flash,
}) {
  const [note, setNote] = useState("");
  const t = detail;

  const act = async (action) => {
    try {
      await onStatus(action, note);
      setNote("");
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="panel detail">
      <div className="detail-head">
        <h2>
          #{t.id} {t.title}
        </h2>
        <span className={`badge status-${t.status}`}>
          {STATUS_LABEL[t.status] || t.status}
        </span>
      </div>
      <div className="meta-grid">
        <div>
          <div className="muted">创建时刻 (UTC)</div>
          <div>{fmtTime(t.created_at)}</div>
        </div>
        <div>
          <div className="muted">固定策略</div>
          <div>
            {t.policy.name} · key=<code>{t.policy.key}</code> ·{" "}
            <strong>v{t.policy.version}</strong>
          </div>
        </div>
        <div>
          <div className="muted">修订号 revision</div>
          <div>{t.revision}</div>
        </div>
        <div>
          <div className="muted">视图时间 (UTC)</div>
          <div>{fmtTime(t.now)}</div>
        </div>
      </div>

      <section className="block">
        <h3>有效工作分钟</h3>
        <div className="accrual">
          <span className="accrual-num">
            {fmtMinutes(t.accrued_work_minutes)}
          </span>
          <span className="muted">
            （仅统计计时区间落在工作日历内的部分，假日已切口剔除）
          </span>
          {t.clock_running ? (
            <span className="badge status-open">运行中</span>
          ) : (
            <span className="badge status-paused">
              暂停自 {fmtTime(t.open_pause_since)}
            </span>
          )}
        </div>
      </section>

      <section className="block">
        <h3>各阈值的截止时刻（假设继续计时）</h3>
        <table className="data">
          <thead>
            <tr>
              <th>类型</th><th>级别</th><th>阈值</th><th>截止时刻 (UTC)</th>
            </tr>
          </thead>
          <tbody>
            {t.deadlines.map((d) => (
              <tr key={`${d.kind}-${d.ref}`}>
                <td>{d.kind === "warn" ? "警告" : "升级"}</td>
                <td>{d.ref}</td>
                <td>{fmtMinutes(d.threshold_minutes)}</td>
                <td>
                  {d.suspended ? (
                    <em className="muted">暂停中（恢复后顺延）</em>
                  ) : (
                    fmtTime(d.deadline_at)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="block">
        <h3>状态操作（同事务裁决，带 revision 乐观锁）</h3>
        <div className="actions">
          {t.status === "open" && (
            <>
              <button className="btn" onClick={() => act("pause")}>
                暂停（等待客户）
              </button>
              <button className="btn warn" onClick={() => act("resolve")}>
                标记已解决
              </button>
            </>
          )}
          {t.status === "paused" && (
            <button className="btn primary" onClick={() => act("resume")}>
              客户回复 / 恢复计时
            </button>
          )}
          {t.status === "resolved" && (
            <button className="btn primary" onClick={() => act("reopen")}>
              重新打开（保留既有工作分钟）
            </button>
          )}
          <input
            className="note-input"
            value={note}
            placeholder="操作备注（可选）"
            onChange={(e) => setNote(e.target.value)}
          />
        </div>
        <div className="muted small">
          基于当前修订号 <code>{t.revision}</code> 提交；若期间数据已变更，
          服务端返回 409 冲突，界面会刷新到最新修订。
        </div>
      </section>

      <section className="block">
        <h3>已计入区间（active）与暂停区间（pause）</h3>
        <IntervalList
          title="已计入区间"
          intervals={t.counted_intervals}
          tone="active"
        />
        <IntervalList
          title="工作日历切片（假日切口后真正累积的部分）"
          intervals={t.working_slices}
          tone="slice"
        />
        <IntervalList
          title="暂停区间（等待客户，不累积）"
          intervals={t.pause_intervals}
          tone="pause"
        />
      </section>

      <MigrationPanel
        detail={t}
        policies={policies}
        onMigrated={onMigrated}
        setError={setError}
        flash={flash}
      />

      <SlaEvents events={t.sla_events} />

      <EventTimeline events={t.state_events} />

      <details className="block raw">
        <summary className="muted">原始 JSON（含每次裁决的完整依据 basis）</summary>
        <pre>{JSON.stringify(t, null, 2)}</pre>
      </details>
    </div>
  );
}
