import React, { useState } from "react";
import { api } from "../api.js";
import { fmtMinutes } from "../format.js";

// Admin panel: publishing a new policy version never mutates old versions.
export default function PolicyPanel({ policies, onChanged, setError, flash }) {
  const [warn, setWarn] = useState(90);
  const [l2, setL2] = useState(240);
  const [l3, setL3] = useState("");

  const grouped = policies.reduce((acc, p) => {
    (acc[p.policy_key] ||= []).push(p);
    return acc;
  }, {});

  const publish = async () => {
    const escalations = [{ level: "L2", minutes: Number(l2),
                           action: "notify-manager" }];
    if (l3 !== "") {
      escalations.push({ level: "L3", minutes: Number(l3),
                         action: "page-director" });
    }
    try {
      const r = await api.createPolicy({
        policy_key: "standard",
        name: "Standard SLA",
        description: "published from console",
        spec: { warn_minutes: Number(warn), escalations },
      });
      flash(`已发布不可变新版本 v${r.version}；旧工单不受影响，需显式迁移`);
      await onChanged();
    } catch (e) {
      setError(`发布失败：${e.message}`);
    }
  };

  return (
    <div className="panel">
      <h2>策略版本</h2>
      {Object.entries(grouped).map(([key, vers]) => (
        <div key={key} className="policy-group">
          <div className="policy-key">
            <code>{key}</code>
          </div>
          {vers.map((p) => (
            <div key={p.version}
                 className={p.is_latest ? "policy-version latest"
                                        : "policy-version"}>
              <div className="pv-head">
                <strong>v{p.version}</strong>
                {p.is_latest && <span className="badge latest">最新</span>}
              </div>
              <div className="muted small">
                警告 {fmtMinutes(p.spec.warn_minutes)} ·{" "}
                {(p.spec.escalations || [])
                  .map((e) => `${e.level} ${fmtMinutes(e.minutes)}`)
                  .join("，") || "无升级"}
              </div>
            </div>
          ))}
        </div>
      ))}

      <div className="publish">
        <h3>发布新版本</h3>
        <label>
          警告（工作分钟）
          <input type="number" value={warn}
                 onChange={(e) => setWarn(e.target.value)} />
        </label>
        <label>
          L2 升级（工作分钟）
          <input type="number" value={l2}
                 onChange={(e) => setL2(e.target.value)} />
        </label>
        <label>
          L3 升级（可空）
          <input type="number" value={l3} placeholder="留空则无 L3"
                 onChange={(e) => setL3(e.target.value)} />
        </label>
        <button className="btn primary" onClick={publish}>发布新版本</button>
      </div>
    </div>
  );
}
