import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { fmtMinutes, fmtTime } from "../format.js";

const CHANGE_LABEL = {
  unchanged: "不变",
  tightened: "收紧 ↓",
  relaxed: "放宽 ↑",
  removed: "移除",
  added: "新增",
};

export default function MigrationPanel({ detail, policies, onMigrated,
                                         setError, flash }) {
  const [targetVersion, setTargetVersion] = useState("");
  const [preview, setPreview] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  // Candidate versions of the same policy key, excluding the pinned one.
  const candidates = useMemo(
    () =>
      policies
        .filter(
          (p) =>
            p.policy_key === detail.policy.key &&
            p.version !== detail.policy.version
        )
        .sort((a, b) => a.version - b.version),
    [policies, detail.policy.key, detail.policy.version]
  );

  const doPreview = async () => {
    if (!targetVersion) return;
    setBusy(true);
    try {
      const d = await api.previewMigration(detail.id, {
        policy_key: detail.policy.key,
        policy_version: Number(targetVersion),
      });
      setPreview(d);
      setError("");
    } catch (e) {
      setError(`预览失败：${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  const doApply = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      await api.applyMigration(detail.id, {
        policy_key: preview.new_policy.key,
        policy_version: preview.new_policy.version,
        revision: detail.revision,
        reason,
        actor: "console",
      });
      flash(
        `已迁移到策略 v${preview.new_policy.version}，旧→新计时差异已存档为证据`
      );
      setPreview(null);
      setReason("");
      setTargetVersion("");
      await onMigrated();
    } catch (e) {
      if (e.status === 409) {
        setError(`迁移被拒绝（修订过期）：${e.message}。请刷新后重试。`);
        await onMigrated();
      } else {
        setError(`迁移失败：${e.message}`);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="block migration">
      <h3>策略迁移（显式操作，展示旧/新计时差异并保存证据）</h3>
      <div className="muted small">
        修改策略不会悄悄改变旧工单：本工单固定在 v{detail.policy.version}。
        迁移会记录每个阈值旧/新的分钟数、跨越时刻差异，并补登新版本下已到期的事件。
      </div>
      <div className="migration-controls">
        <select
          value={targetVersion}
          onChange={(e) => setTargetVersion(e.target.value)}
        >
          <option value="">选择目标版本…</option>
          {candidates.map((p) => (
            <option key={p.version} value={p.version}>
              v{p.version}
              {p.is_latest ? "（最新）" : ""}
            </option>
          ))}
        </select>
        <button className="btn" onClick={doPreview} disabled={busy || !targetVersion}>
          预览旧/新差异
        </button>
        <input
          placeholder="迁移原因（写入证据）"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>

      {preview && (
        <div className="diff-box">
          <div className="diff-title">
            v{preview.old_policy.version} → v{preview.new_policy.version}
            （当前累积 {fmtMinutes(preview.accrued_work_minutes)}，
            预览于 {fmtTime(preview.decided_at)}）
          </div>
          <table className="data">
            <thead>
              <tr>
                <th>类型</th><th>级别</th><th>旧阈值</th><th>新阈值</th>
                <th>变化</th><th>旧跨越时刻</th><th>新跨越时刻</th>
                <th>迁移时补登</th>
              </tr>
            </thead>
            <tbody>
              {preview.thresholds.map((x) => (
                <tr key={`${x.kind}-${x.ref}`}
                    className={`change-${x.change}`}>
                  <td>{x.kind === "warn" ? "警告" : "升级"}</td>
                  <td>{x.ref}</td>
                  <td>{x.old_minutes == null ? "—" : fmtMinutes(x.old_minutes)}</td>
                  <td>{x.new_minutes == null ? "—" : fmtMinutes(x.new_minutes)}</td>
                  <td>
                    <span className={`change-badge ${x.change}`}>
                      {CHANGE_LABEL[x.change]}
                      {x.delta_minutes != null && x.change !== "unchanged"
                        ? ` (${x.delta_minutes > 0 ? "+" : ""}${x.delta_minutes}m)`
                        : ""}
                    </span>
                  </td>
                  <td>{x.old_crossed_at ? fmtTime(x.old_crossed_at) : "未到期"}</td>
                  <td>{x.new_crossed_at ? fmtTime(x.new_crossed_at) : "未到期"}</td>
                  <td>{x.new_already_crossed ? "是" : "否"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="diff-actions">
            <button className="btn primary" onClick={doApply} disabled={busy}>
              确认迁移（基于 revision {detail.revision}）
            </button>
            <button className="btn" onClick={() => setPreview(null)}>
              取消
            </button>
          </div>
        </div>
      )}

      {detail.migrations.length > 0 && (
        <div className="migration-history">
          <h4>迁移历史（证据）</h4>
          {detail.migrations.map((m) => (
            <details key={m.id} className="migration-record">
              <summary>
                #{m.id}：v{m.old_policy_version} → v{m.new_policy_version}
                ，{fmtTime(m.applied_at)}
                {m.reason ? `，原因：${m.reason}` : ""}
              </summary>
              <table className="data small">
                <thead>
                  <tr>
                    <th>阈值</th><th>旧</th><th>新</th><th>变化</th>
                    <th>旧跨越</th><th>新跨越</th>
                  </tr>
                </thead>
                <tbody>
                  {m.diff.thresholds.map((x) => (
                    <tr key={`${x.kind}-${x.ref}`}>
                      <td>{x.kind}/{x.ref}</td>
                      <td>{x.old_minutes ?? "—"}</td>
                      <td>{x.new_minutes ?? "—"}</td>
                      <td>{CHANGE_LABEL[x.change]}</td>
                      <td>{x.old_crossed_at ? fmtTime(x.old_crossed_at) : "—"}</td>
                      <td>{x.new_crossed_at ? fmtTime(x.new_crossed_at) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="muted small">
                revision {m.revision_from} → {m.revision_to}，
                操作人：{m.actor || "—"}
              </div>
            </details>
          ))}
        </div>
      )}
    </section>
  );
}
