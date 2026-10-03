import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import TicketList from "./components/TicketList.jsx";
import TicketDetail from "./components/TicketDetail.jsx";
import PolicyPanel from "./components/PolicyPanel.jsx";

export default function App() {
  const [tickets, setTickets] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [policies, setPolicies] = useState([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refreshList = useCallback(async () => {
    try {
      setTickets(await api.listTickets());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const refreshDetail = useCallback(async (id) => {
    if (!id) return;
    try {
      setDetail(await api.getTicket(id));
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const refreshPolicies = useCallback(async () => {
    try {
      setPolicies(await api.listPolicies());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refreshList();
    refreshPolicies();
  }, [refreshList, refreshPolicies]);

  useEffect(() => {
    if (selectedId != null) refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  // Background polling mirrors the server scanner so the console is live.
  useEffect(() => {
    const h = setInterval(() => {
      refreshList();
      if (selectedId != null) refreshDetail(selectedId);
    }, 5000);
    return () => clearInterval(h);
  }, [selectedId, refreshList, refreshDetail]);

  const flash = (msg) => {
    setNotice(msg);
    setTimeout(() => setNotice(""), 4000);
  };

  const onCreated = async (payload) => {
    try {
      const t = await api.createTicket(payload);
      await refreshList();
      setSelectedId(t.id);
      flash(`工单 #${t.id} 已创建，固定策略 v${t.policy.version}`);
    } catch (e) {
      setError(e.message);
    }
  };

  const onStatus = async (action, note) => {
    if (!detail) return;
    try {
      await api.setStatus(detail.id, {
        action,
        revision: detail.revision,
        note,
      });
      await refreshDetail(detail.id);
      await refreshList();
      flash(`操作成功：${action}（修订号已推进）`);
    } catch (e) {
      if (e.status === 409) {
        setError(`提交冲突（修订过期/非法状态）：${e.message}。数据已刷新。`);
        await refreshDetail(detail.id);
        await refreshList();
      } else {
        setError(e.message);
      }
    }
  };

  const onMigrated = async () => {
    if (!detail) return;
    await refreshDetail(detail.id);
    await refreshList();
    await refreshPolicies();
  };

  const runScan = async () => {
    try {
      const r = await api.scan({});
      flash(`扫描完成，新登记 ${r.registered.length} 条事件`);
      await refreshList();
      if (selectedId != null) await refreshDetail(selectedId);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>SLA 工单计时服务</h1>
        <div className="topbar-right">
          <button className="btn" onClick={runScan}>
            立即扫描计时器
          </button>
        </div>
      </header>

      {error && (
        <div className="banner error" onClick={() => setError("")}>
          ⚠ {error}（点击关闭）
        </div>
      )}
      {notice && <div className="banner ok">✓ {notice}</div>}

      <div className="layout">
        <aside className="sidebar">
          <TicketList
            tickets={tickets}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onCreated={onCreated}
          />
        </aside>
        <main className="content">
          {detail ? (
            <TicketDetail
              key={detail.id}
              detail={detail}
              policies={policies}
              onStatus={onStatus}
              onMigrated={onMigrated}
              setError={setError}
              flash={flash}
            />
          ) : (
            <div className="placeholder">
              <p>从左侧选择一个工单，查看截止时刻、已计入区间、暂停区间与升级记录。</p>
              <p className="muted">
                每一条警告/升级都附带裁决依据：固定的策略版本、当时累积的有效工作分钟、
                已计入区间、暂停区间与工作日历切片。
              </p>
            </div>
          )}
        </main>
        <aside className="rightbar">
          <PolicyPanel policies={policies} onChanged={refreshPolicies}
            setError={setError} flash={flash} />
        </aside>
      </div>
    </div>
  );
}
