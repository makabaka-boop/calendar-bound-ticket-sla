import React, { useState } from "react";
import { fmtMinutes, fmtTime } from "../format.js";

const STATUS_LABEL = {
  open: "计时中",
  paused: "等待客户（暂停）",
  resolved: "已解决",
};

export default function TicketList({ tickets, selectedId, onSelect,
                                      onCreated }) {
  const [title, setTitle] = useState("");

  const submit = (e) => {
    e.preventDefault();
    if (!title.trim()) return;
    onCreated({ title: title.trim() });
    setTitle("");
  };

  return (
    <div className="panel">
      <h2>工单</h2>
      <form className="create-form" onSubmit={submit}>
        <input
          value={title}
          placeholder="新工单标题（创建时固定当前策略版本）"
          onChange={(e) => setTitle(e.target.value)}
        />
        <button className="btn primary" type="submit">
          创建
        </button>
      </form>
      <ul className="ticket-list">
        {tickets.map((t) => (
          <li
            key={t.id}
            className={t.id === selectedId ? "ticket selected" : "ticket"}
            onClick={() => onSelect(t.id)}
          >
            <div className="ticket-title">#{t.id} {t.title}</div>
            <div className="ticket-meta">
              <span className={`badge status-${t.status}`}>
                {STATUS_LABEL[t.status] || t.status}
              </span>
              <span className="muted">策略 v{t.policy_version}</span>
              <span className="muted">{fmtMinutes(t.accrued_work_minutes)}</span>
            </div>
          </li>
        ))}
        {tickets.length === 0 && (
          <li className="muted">暂无工单</li>
        )}
      </ul>
    </div>
  );
}
