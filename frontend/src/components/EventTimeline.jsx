import React from "react";
import { fmtTime } from "../format.js";

const KIND_LABEL = {
  open: "创建 / 开始计时",
  pause: "暂停（等待客户）",
  resume: "恢复计时",
  reopen: "重新打开",
  resolve: "标记解决",
};

export default function EventTimeline({ events }) {
  return (
    <section className="block">
      <h3>状态事件日志（append-only，裁决即重放此日志）</h3>
      <ol className="timeline">
        {events.map((e) => (
          <li key={e.seq} className={`ev ev-${e.kind}`}>
            <span className="ev-seq">#{e.seq}</span>
            <span className="ev-kind">{KIND_LABEL[e.kind] || e.kind}</span>
            <span className="ev-at">{fmtTime(e.at)}</span>
            {e.note && <span className="ev-note">“{e.note}”</span>}
          </li>
        ))}
      </ol>
    </section>
  );
}
