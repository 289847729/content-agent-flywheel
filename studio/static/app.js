/* 飞轮可视化操作台前端逻辑（原生 JS，无构建）。 */
"use strict";

const $ = (id) => document.getElementById(id);

function materialDownloadLinks(material) {
  const paths = [];
  for (const path of [material?.md_path, material?.docx_path, material?.cover_path, ...(material?.image_files || [])]) {
    if (path && !paths.includes(path)) paths.push(path);
  }
  return paths.map((path) => {
    const name = path.split(/[\\\\/]/).pop();
    return `<a class="btn small ghost" href="/api/run-file?path=${encodeURIComponent(path)}" target="_blank" rel="noopener">下载 ${esc(name)}</a>`;
  }).join(" ");
}

const state = {
  topics: [],
  checkedIds: new Set(),
  statusFilter: "",
  directionFilter: "",
  dateFilter: "today",
  keyword: "",
  sort: "default",
  recordId: "",        // 当前写作选题 record_id
  topic: null,         // 当前选题字段
  markdown: "",
  title: "",
  author: "",
  hook: "",
  subtitle: "",
  theme: "moyu-green",
  engine: "builtin",      // builtin 内置主题 | bmmd 引擎
  bmmdStyle: "kami",      // bmmd 排版样式 ID
  codeTheme: "",          // bmmd 代码高亮主题（空 = 随样式）
  previewHtml: "",     // 最新预览/导入的 HTML
  pushSource: "generated", // generated | imported
  generatingTasks: new Set(), // 正在生成文案/封面的任务集合：`${platform}_${runDir || title}`
  batchJobId: "",
  batchPollTimer: null,
  batchActive: false,
  batchPolling: false,
};

/* ---------- 基础工具 ---------- */

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = options.timeout || 180000;
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: controller.signal,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      data = { ok: false, error: `服务器返回格式异常 (HTTP ${response.status} ${response.statusText})` };
    }
    if (!response.ok) {
      let errorMsg = "";
      if (typeof data.detail === "string") {
        errorMsg = data.detail;
      } else if (Array.isArray(data.detail)) {
        errorMsg = data.detail.map((d) => (d.msg || JSON.stringify(d))).join("; ");
      } else if (data.detail) {
        errorMsg = JSON.stringify(data.detail);
      } else if (data.error) {
        errorMsg = data.error;
      }
      if (response.status === 404) {
        errorMsg = "接口不存在 (404 Not Found)：请在终端重新启动 Studio 后台服务（python3 studio/app.py）以加载最新路由。";
      }
      return { ok: false, error: errorMsg || `HTTP ${response.status} 错误` };
    }
    return data;
  } catch (err) {
    if (err && err.name === "AbortError") err.timeoutMs = timeout;
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

let toastTimer = null;
function toast(message, type = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast ${type}`;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, type === "error" ? 6000 : 3200);
}

/* ---------- 结果弹框（所有后端写操作的结果反馈） ---------- */

function showModal({ type, title, summaryHtml, actions = [] }) {
  $("result-modal-title").textContent = title;
  $("result-modal-summary").innerHTML = summaryHtml;
  $("result-modal-box").className = `modal-box ${type === "error" ? "error" : "success"}`;
  const actionsBox = $("result-modal-actions");
  actionsBox.innerHTML = "";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "btn ghost";
  closeBtn.textContent = "知道了";
  closeBtn.addEventListener("click", closeModal);
  actionsBox.appendChild(closeBtn);
  actions.forEach(({ label, primary, onClick }) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = primary ? "btn primary" : "btn ghost";
    btn.textContent = label;
    btn.addEventListener("click", () => { closeModal(); onClick(btn); });
    actionsBox.appendChild(btn);
  });
  $("result-modal").hidden = false;
}

function closeModal() {
  $("result-modal").hidden = true;
}

/**
 * 按钮请求统一包装器 —— 规范：凡触发后端请求的按钮必须走 withBusy。
 * 点击瞬间禁用按钮并显示过程态文案（防重复点击、给出即时反馈）；
 * 失败时默认弹红色结果弹框（onError 只做额外收尾，不拦截弹框）；
 * 成功时由 onSuccess 负责反馈（结果弹框或彩色 toast + 状态区）。
 */
async function withBusy(btn, busyText, statusEl, { request, onSuccess, onError }) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = busyText;
  if (statusEl) statusEl.textContent = "";
  let data;
  try {
    data = await request();
  } catch (err) {
    data = {
      ok: false,
      error: err && err.name === "AbortError" ? `请求超时（${Math.ceil((err.timeoutMs || 180000) / 1000)} 秒），请重试` : `请求异常：${err.message || err}`,
    };
  }
  btn.disabled = false;
  btn.textContent = original;
  if (data.ok) {
    if (onSuccess) await onSuccess(data);
  } else {
    showModal({ type: "error", title: "操作失败", summaryHtml: esc(data.error || "未知错误") });
    if (onError) await onError(data);
    if (statusEl) statusEl.textContent = "";
  }
  return data;
}

function setStep(step) {
  document.querySelectorAll(".step").forEach((node) => {
    node.classList.toggle("active", node.dataset.step === String(step));
  });
  document.querySelectorAll(".panel").forEach((node) => {
    node.classList.toggle("active", node.id === `panel-${step}`);
  });
  if (step === 2) renderWriteQueue();
  if (step === 3) { renderGenQueue(); updateDraftCapacity(); }
  if (step === 5) { renderPushConfig(); loadRuns(); }
}

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function statusTag(status) {
  return `<span class="tag status-${esc(status)}">${esc(status)}</span>`;
}

function trafficTag(level) {
  const cls = level === "高" ? "traffic-high" : level === "中" ? "traffic-mid" : "";
  return `<span class="tag ${cls}">流量预估 ${esc(level) || "未标"}</span>`;
}

function extractFirstRepoUrl(raw) {
  if (!raw) return "";
  const str = String(raw);
  const match = str.match(/https?:\/\/(?:github\.com|gitee\.com|gitlab\.com)[^\s\u4e00-\u9fa5<>"'|\]）】\^]+/i) ||
                str.match(/https?:\/\/[^\s\u4e00-\u9fa5<>"'|\]）】\^]+/i);
  if (!match) return "";
  return match[0].replace(/[.,;!?，。；！？]+$/, "");
}

function renderNoteHtml(raw) {
  if (!raw) return "";
  const str = String(raw);
  const urlRegex = /(https?:\/\/[^\s\u4e00-\u9fa5<>"'|\]）】\^]+)/g;
  let lastIndex = 0;
  let html = "";
  let match;
  while ((match = urlRegex.exec(str)) !== null) {
    html += esc(str.slice(lastIndex, match.index));
    let url = match[0];
    let trailing = "";
    const trailMatch = url.match(/[.,;!?，。；！？]+$/);
    if (trailMatch) {
      trailing = trailMatch[0];
      url = url.slice(0, -trailing.length);
    }
    const cleanUrl = url.trim();
    html += `<a href="${encodeURI(cleanUrl)}" target="_blank" rel="noopener noreferrer" class="ext-link" title="点击访问外部开源/项目地址：${esc(cleanUrl)}" onclick="event.stopPropagation()">🔗 ${esc(cleanUrl)}</a>${esc(trailing)}`;
    lastIndex = match.index + match[0].length;
  }
  html += esc(str.slice(lastIndex));
  return html;
}

/* ---------- 健康检查 ---------- */

async function loadHealth(silent = false) {
  await withBusy($("btn-refresh-health"), "检查中…", null, {
    request: () => api("/api/health"),
    onSuccess: (data) => {
      const set = (id, ready, okText, badText) => {
        const el = $(id);
        el.classList.toggle("ok", !!ready);
        el.classList.toggle("bad", ready === false);
        el.textContent = ready ? okText : badText;
      };
      set("health-feishu", data.feishu_ready, "飞书 已配置", "飞书 未配置");
      set("health-wechat", data.wechat_ready, "微信 已配置", "微信 未配置");
      set("health-llm", data.llm_ready, "LLM 已配置", "LLM 未配置");
      const table = data.table_check;
      if (table && table.error) {
        set("health-table", false, "", `表结构错误：${table.error}`);
      } else if (Array.isArray(table)) {
        set("health-table", table.length === 0, "表结构 正常", `表结构缺字段：${table.join("、")}`);
      } else {
        set("health-table", null, "表结构 未检查", "表结构 未检查");
      }
      if (data.author) state.author = data.author;
      $("pv-author").value = state.author || "";
      $("batch-author-hint").textContent = data.author
        ? `留空使用后端作者配置：${data.author}` : "留空使用后端作者配置；当前未配置，可在这里填写作者。";
      if (!silent) toast("状态已刷新", "success");
    },
  });
}

/* ---------- 阶段 1：自动筛选 ---------- */

/* 静默刷新选题列表：供其他操作成功后内部调用，不动任何按钮；失败时 toast 提示。 */
async function refreshTopics(alertOnError = true) {
  const data = await api("/api/topics");
  if (data.ok) {
    state.topics = data.items;
    renderTopics();
  } else if (alertOnError) {
    toast(`列表刷新失败：${data.error}`, "error");
  }
  return data;
}

async function fetchTopics(silent = false) {
  return await withBusy($("btn-fetch-topics"), "拉取中…", null, {
    request: () => refreshTopics(false),
    onSuccess: (data) => {
      if (!silent) toast(`已拉取 ${data.items.length} 条选题`, "success");
    },
  });
}

function todayIso() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function filteredTopics() {
  let list = state.topics.slice();
  if (state.statusFilter) list = list.filter((t) => t["状态"] === state.statusFilter);
  if (state.directionFilter) list = list.filter((t) => (t["内容方向"] || "").includes(state.directionFilter));
  if (state.dateFilter === "today") {
    const today = todayIso();
    list = list.filter((t) => (t["入库日期"] || "") === today);
  }
  if (state.keyword.trim()) {
    const kw = state.keyword.trim().toLowerCase();
    list = list.filter((t) => (t["选题标题"] || "").toLowerCase().includes(kw));
  }
  if (state.sort === "date-desc") {
    list.sort((a, b) => String(b["入库日期"] || "").localeCompare(String(a["入库日期"] || "")));
  }
  if (state.sort === "traffic") {
    const rank = { 高: 0, 中: 1, 低: 2 };
    list.sort((a, b) => (rank[a["流量预估"]] ?? 3) - (rank[b["流量预估"]] ?? 3));
  }
  return list;
}

function newestImportDate() {
  const dates = state.topics.map((t) => t["入库日期"]).filter(Boolean).sort();
  return dates.length ? dates[dates.length - 1] : "—";
}

function renderTopics() {
  updateBatchSelection();
  const box = $("topic-cards");
  const list = filteredTopics();
  $("topic-count").textContent = `共 ${list.length} 条`;
  if (!list.length) {
    if (state.dateFilter === "today" && state.topics.length) {
      box.innerHTML = `<p class="empty">今天（${todayIso()}）还没有入库的选题，库里最新一批是 ${newestImportDate()} 入库的 ${state.topics.length} 条。<br>可以点「全部日期」查看，或先跑一轮选题自动入库，新选题会自动带上今天的入库日期。</p>`;
    } else {
      box.innerHTML = '<p class="empty">没有符合条件的选题。</p>';
    }
    return;
  }

  const selectable = list.filter((t) => t["状态"] !== "已推送");
  const allChecked = selectable.length > 0 && selectable.every((t) => state.checkedIds.has(t.record_id));
  const someChecked = !allChecked && selectable.some((t) => state.checkedIds.has(t.record_id));

  const rows = list.map((t) => {
    const isPushed = t["状态"] === "已推送";
    if (isPushed && state.checkedIds.has(t.record_id)) {
      state.checkedIds.delete(t.record_id);
    }
    const checked = !isPushed && state.checkedIds.has(t.record_id);
    const keywords = (t["关键词"] || "").split(/[,，、]/).filter(Boolean);
    const isSkill = (t["内容方向"] || "").includes("Skill");
    return `
      <tr class="card topic-row ${checked ? "checked" : ""} ${isPushed ? "disabled-card" : ""}" data-record-id="${esc(t.record_id)}" ${isPushed ? 'data-disabled="true"' : ""}>
        <td style="text-align: center; width: 44px;" onclick="event.stopPropagation()">
          <input type="checkbox" class="card-check" ${checked ? "checked" : ""} ${isPushed ? 'disabled title="已推送，不可重复生成"' : ""}>
        </td>
        <td>
          <div class="td-title ${isPushed ? "td-title-disabled" : ""}">${esc(t["选题标题"] || "（无标题）")}</div>
        </td>
        <td style="white-space: nowrap;">${statusTag(t["状态"])}</td>
        <td style="white-space: nowrap;">${trafficTag(t["流量预估"])}</td>
        <td>${isSkill ? `<span class="tag" style="background:#e0e7ff;color:#3730a3;font-weight:600;">🔧 实用 Skill</span>` : (t["内容方向"] ? `<span class="tag">${esc(t["内容方向"])}</span>` : '<span class="hint">—</span>')}</td>
        <td style="white-space: nowrap;">${t["入库日期"] ? `<span class="tag">${esc(t["入库日期"])}</span>` : '<span class="hint">—</span>'}</td>
        <td>
          ${keywords.length ? `<div class="td-tags">${keywords.map((k) => `<span class="tag">${esc(k)}</span>`).join("")}</div>` : '<span class="hint">—</span>'}
        </td>
        <td>
          ${t["备注"] ? `
            <div class="td-note" onclick="event.stopPropagation()">
              <div class="note-text card-note">${renderNoteHtml(t["备注"])}</div>
              <div class="note-actions">
                <button class="note-toggle" type="button">查看备注</button>
                ${extractFirstRepoUrl(t["备注"]) ? `<a href="${encodeURI(extractFirstRepoUrl(t["备注"]))}" target="_blank" rel="noopener noreferrer" class="btn-repo-jump" title="一键直达开源仓库：${esc(extractFirstRepoUrl(t["备注"]))}" onclick="event.stopPropagation()">📦 开源地址 ↗</a>` : ""}
              </div>
            </div>` : '<span class="hint">—</span>'}
        </td>
      </tr>`;
  }).join("");

  box.innerHTML = `
    <div class="table-responsive">
      <table class="topic-table">
        <thead>
          <tr>
            <th style="width: 44px; text-align: center;" onclick="event.stopPropagation()">
              <input type="checkbox" id="th-check-all" class="th-check-all" title="全选当前列表" ${allChecked ? "checked" : ""}>
            </th>
            <th style="min-width: 240px;">选题标题</th>
            <th style="width: 85px;">状态</th>
            <th style="width: 100px;">流量预估</th>
            <th style="width: 130px;">内容方向</th>
            <th style="width: 110px;">入库日期</th>
            <th style="min-width: 150px;">关键词</th>
            <th style="min-width: 180px;">备注</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    </div>`;

  const thCheck = $("th-check-all");
  if (thCheck) thCheck.indeterminate = someChecked;
}

/* ---------- 批量发布 ---------- */
function updateBatchSelection() {
  for (const id of Array.from(state.checkedIds)) {
    const t = state.topics.find((x) => x.record_id === id);
    if (t && t["状态"] === "已推送") {
      state.checkedIds.delete(id);
    }
  }
  const count = state.checkedIds.size;
  $("batch-selection-count").textContent = `已勾选 ${count} 条（包含当前筛选隐藏的勾选项）`;
  $("btn-submit-batch").disabled = !count || state.batchActive;
  $("select-count").textContent = count ? `已勾选 ${count} 条` : "";
}

function batchStatusLabel(status) {
  return { running: "进行中", success: "成功", partial: "部分完成", failed: "失败", pending: "等待中", blocked: "已阻塞" }[status] || status || "未知";
}
function renderBatchStatus(data) {
  const box = $("batch-job-status");
  if (!box) return;
  box.hidden = false;
  const items = data.items || [];
  const done = items.filter((item) => ["success", "partial", "failed"].includes(item.status)).length;
  const platformNames = { wechat: "公众号草稿", xhs: "小红书本地素材", toutiao: "头条本地素材" };
  const isAllSuccess = data.status === "success";

  box.innerHTML = `<div class="batch-job-summary">
    <b>批量任务 ${esc(data.job_id || state.batchJobId)}</b>
    <span class="tag status-${esc(data.status || "running")}">${esc(batchStatusLabel(data.status))}</span>
    <span class="hint">${done}/${items.length} 条完成</span>
  </div>
  <div class="batch-item-list">${items.map((item) => {
    const platforms = item.platforms || {};
    const isItemSuccess = item.status === "success";

    // 错误收集：重试成功或当前已成功的项，彻底隐藏错误信息框
    let itemErrors = [];
    if (!isItemSuccess) {
      itemErrors = (item.errors || []).filter((err) => {
        const plat = err?.platform;
        if (plat && platforms[plat]?.status === "success") return false;
        const st = err?.step;
        if (st && item.steps?.[st]?.status === "success") return false;
        return true;
      });
      Object.entries(platforms).forEach(([name, value]) => {
        if (value.status === "failed" && value.error) {
          if (!itemErrors.some((e) => e.platform === name)) {
            itemErrors.push({ platform: name, error: value.error });
          }
        }
      });
    }

    return `<div class="batch-item">
      <div class="batch-item-title">
        <b>${esc(item.title || item.record_id || "未命名选题")}</b>
        <span class="tag status-${esc(item.status || "pending")}">${esc(batchStatusLabel(item.status))}</span>
      </div>
      <div class="batch-platforms">${Object.entries(platformNames).map(([name, label]) => {
        const platform = platforms[name] || { status: "pending" };
        const hasError = platform.status !== "success" && Boolean(platform.error);
        return `<span class="batch-platform">
          <b>${esc(label)}</b>：<span class="tag status-${esc(platform.status)}">${esc(batchStatusLabel(platform.status))}</span>
          ${hasError ? `<span class="batch-error">${esc(platform.error)}</span>` : ""}
        </span>`;
      }).join("")}</div>
      ${!isItemSuccess && itemErrors.length ? `<div class="batch-errors">${itemErrors.map((error) => `<div>⚠ ${esc(error.platform ? `${error.platform}：` : "")}${esc(error.error || error)}</div>`).join("")}</div>` : ""}
    </div>`;
  }).join("")}</div>
  ${!isAllSuccess && (data.errors || []).length ? `<div class="batch-errors">${data.errors.map((error) => `<div>⚠ ${esc(error.error || error)}</div>`).join("")}</div>` : ""}
  <div class="row batch-job-actions">
    <span class="hint">${data.status === "running" ? "正在轮询最新进度…" : "任务已停止轮询"}</span>
    ${data.status !== "running" && data.status !== "success" ? `<button class="btn small warn" data-batch-retry="${esc(data.job_id || state.batchJobId)}" type="button">重试失败项</button>` : ""}
  </div>`;
}
async function pollBatchPublish(jobId) {
  if (state.batchPolling || jobId !== state.batchJobId) return;
  clearTimeout(state.batchPollTimer);
  state.batchPolling = true;
  try {
    const data = await api(`/api/batch-publish/${encodeURIComponent(jobId)}`, { timeout: 30000 });
    if (!data.ok) throw new Error(data.error || "无法读取任务状态");
    if (jobId !== state.batchJobId) return;
    renderBatchStatus(data);
    state.batchActive = !["success", "partial", "failed"].includes(data.status);
    $("batch-poll-message").textContent = "";
    $("btn-refresh-batch").hidden = true;

    // 批量执行过程中实时同步已完成项状态至选题卡片
    let anyStateChanged = false;
    (data.items || []).forEach((item) => {
      const isItemDone = item.status === "success" || item.platforms?.wechat?.status === "success";
      if (isItemDone && item.record_id) {
        const topic = state.topics.find((t) => t.record_id === item.record_id);
        if (topic && topic["状态"] !== "已推送") {
          topic["状态"] = "已推送";
          anyStateChanged = true;
        }
        if (state.checkedIds.has(item.record_id)) {
          state.checkedIds.delete(item.record_id);
          anyStateChanged = true;
        }
      }
    });
    if (anyStateChanged) {
      renderTopics();
      updateBatchSelection();
    }

    if (state.batchActive) {
      state.batchPollTimer = setTimeout(() => pollBatchPublish(jobId), 1500);
    } else {
      // 批量任务全部完成：全量拉取后端最新状态同步飞书，并刷新历史记录
      await refreshTopics(false);
      loadRuns();
    }
  } catch (error) {
    $("batch-poll-message").textContent = `进度读取中断：${error.message || error}。任务可能仍在后台运行，请刷新进度。`;
    $("btn-refresh-batch").hidden = false;
  } finally {
    state.batchPolling = false;
    updateBatchSelection();
  }
}
async function submitBatchPublish() {
  const ids = Array.from(state.checkedIds).filter((id) => {
    const t = state.topics.find((x) => x.record_id === id);
    return !t || t["状态"] !== "已推送";
  });
  if (state.batchActive) return;
  if (!ids.length) { toast("请先勾选未推送的选题", "error"); return; }
  state.batchActive = true;
  await withBusy($("btn-submit-batch"), "提交中…", null, {
    request: () => api("/api/batch-publish", { method: "POST", body: { record_ids: ids, template: $("batch-template").value || "auto", theme: $("batch-theme").value || "moyu-green", engine: $("batch-engine").value || "builtin", author: $("batch-author").value.trim() } }),
    onSuccess: async (data) => {
      state.checkedIds.clear();
      $("select-count").textContent = "";
      state.batchJobId = data.job_id;
      try { localStorage.setItem("studio-batch-job-id", data.job_id); } catch (_) { /* 存储不可用不影响任务 */ }
      renderTopics();
      renderBatchStatus({ job_id: data.job_id, status: "running" });
      toast("批量任务已提交，正在生成…", "success");
      await pollBatchPublish(data.job_id);
    },
    onError: () => { state.batchActive = false; },
  });
  updateBatchSelection();
}
async function retryBatchPublish(jobId, btn) {
  if (state.batchActive) return;
  state.batchActive = true;
  updateBatchSelection();
  await withBusy(btn, "重试中…", null, {
    request: () => api(`/api/batch-publish/${encodeURIComponent(jobId)}/retry`, { method: "POST" }),
    onSuccess: () => {
      btn.disabled = true;
      btn.textContent = "失败项重试中…";
      toast("失败项已重新提交，已成功的步骤会保留", "success");
      state.batchPollTimer = setTimeout(() => pollBatchPublish(jobId), 1500);
    },
    onError: () => { state.batchActive = false; },
  });
  updateBatchSelection();
}

/* ---------- 阶段 2：手动挑选 ---------- */

function renderWriteQueue() {
  const box = $("write-queue");
  const queue = state.topics.filter((t) => t["状态"] === "选中");
  if (!queue.length) {
    box.innerHTML = '<p class="empty">暂无选中选题，回到第 1 步勾选并标记。</p>';
    return;
  }
  box.innerHTML = queue.map((t) => `
    <div class="queue-item">
      <span class="queue-title">${esc(t["选题标题"])}</span>
      <span class="queue-meta">${esc(t["流量预估"] ? `流量预估 ${t["流量预估"]}` : "")}${t["文章标题"] ? " · 已有初稿" : ""}</span>
      <button class="btn primary" type="button" data-write="${esc(t.record_id)}">去写稿</button>
    </div>`).join("");
}

async function markSelected() {
  const ids = Array.from(state.checkedIds).filter((id) => {
    const t = state.topics.find((x) => x.record_id === id);
    return !t || t["状态"] !== "已推送";
  });
  if (!ids.length) { toast("没有勾选任何未推送的选题", "error"); return; }
  await withBusy($("btn-mark-selected"), "标记中（约 3~5 秒）…", $("mark-status"), {
    request: () => api("/api/topics/select", { method: "POST", body: { record_ids: ids } }),
    onSuccess: async (data) => {
      state.checkedIds.clear();
      $("select-count").textContent = "";
      await refreshTopics();
      const n = data.updated;
      $("mark-status").textContent = `已把 ${n} 条选题标记为「选中」`;
      showModal({
        type: "success",
        title: "标记成功",
        summaryHtml: `已把 <b>${esc(String(n))}</b> 条选题标记为「选中」，飞书状态已更新，选题已进入写作队列。`,
        actions: [{ label: "去写作队列 →", primary: true, onClick: () => setStep(2) }],
      });
    },
  });
}

/* ---------- 批量入库 ---------- */

async function importTopics() {
  const raw = $("topic-import-json").value.trim();
  if (!raw) { toast("先粘贴选题 JSON", "error"); return; }
  let topics;
  try {
    topics = JSON.parse(raw);
  } catch (err) {
    toast(`JSON 解析失败：${err.message}`, "error");
    return;
  }
  if (!Array.isArray(topics) || !topics.length) { toast("JSON 必须是选题对象数组", "error"); return; }
  await withBusy($("btn-import-topics"), "入库中…", $("topic-import-status"), {
    request: () => api("/api/topics/add", { method: "POST", body: { topics } }),
    onSuccess: async (data) => {
      $("topic-import-json").value = "";
      $("topic-import-box").hidden = true;
      await refreshTopics();
      showModal({
        type: "success",
        title: "入库成功",
        summaryHtml: `已入库 <b>${esc(String(data.created))}</b> 条选题，自动带今天的入库日期，状态为「待定」。点「今天」chip 即可看到。`,
      });
    },
  });
}

async function scanAihotTopics() {
  await withBusy($("btn-scan-aihot"), "抓取 AIHOT 榜单并提炼中…", null, {
    request: () => api("/api/topics/scan-aihot", {
      method: "POST",
      body: { limit: 3, add_feishu: true }
    }),
    onSuccess: async (data) => {
      await refreshTopics(false);
      document.querySelectorAll("#status-chips .chip").forEach((c) => c.classList.remove("active"));
      const pendingChip = document.querySelector("#status-chips .chip[data-status='待定']") || document.querySelector("#status-chips .chip[data-status='']");
      if (pendingChip) {
        pendingChip.classList.add("active");
        state.statusFilter = pendingChip.dataset.status;
      }
      renderTopics();
      const listHtml = (data.topics || []).map((t, idx) => `<li><b>Top ${idx + 1}</b>：${esc(t.选题标题 || "")}</li>`).join("");
      showModal({
        type: "success",
        title: "🔥 AIHOT 热点榜 Top 3 入库成功",
        summaryHtml: `成功从 aihot.news 抓取前三名重磅热点，并已将 <b>${esc(String(data.created || (data.topics || []).length))}</b> 条爆款选题写入系统/飞书（状态为「待定」）。<br><ul style="margin-top:8px;padding-left:18px;line-height:1.6;">${listHtml}</ul><br>页面已自动刷新，可直接勾选启动批量发布。`,
      });
      toast(`已成功入库 ${data.created || (data.topics || []).length} 条 AIHOT 热点`, "success");
    },
  });
}

async function scanHotTopics() {
  const limitVal = parseInt($("radar-limit").value, 10) || 5;
  await withBusy($("btn-scan-hot"), "扫描海外热帖并提炼中…", null, {
    request: () => api("/api/topics/scan-hot", {
      method: "POST",
      body: { hours: 24.0, limit: limitVal, add_feishu: true }
    }),
    onSuccess: async (data) => {
      await refreshTopics(false);
      // 切换过滤到待定或全部，让新录入项直接展现
      document.querySelectorAll("#status-chips .chip").forEach((c) => c.classList.remove("active"));
      const pendingChip = document.querySelector("#status-chips .chip[data-status='待定']") || document.querySelector("#status-chips .chip[data-status='']");
      if (pendingChip) {
        pendingChip.classList.add("active");
        state.statusFilter = pendingChip.dataset.status;
      }
      renderTopics();
      showModal({
        type: "success",
        title: "⚡ 海外一手热点扫描入库成功",
        summaryHtml: `成功从 Hacker News 与 Hugging Face 抓取过去 24 小时高赞一手资讯，已将 <b>${esc(String(data.created))}</b> 条爆款选题写入飞书（状态为「待定」）。<br><br>每条选题均带真实首发时间戳与信源防伪标记，页面已自动刷新呈现。`,
      });
      toast(`已成功入库 ${data.created} 条海外热点`, "success");
    },
  });
}

async function scanSkillsTopics() {
  const limitVal = parseInt($("radar-limit").value, 10) || 5;
  await withBusy($("btn-scan-skills"), "扫描优质 Skill 并提炼中…", null, {
    request: () => api("/api/topics/scan-skills", {
      method: "POST",
      body: { limit: limitVal, add_feishu: true }
    }),
    onSuccess: async (data) => {
      await refreshTopics(false);
      // 切换过滤到实用 Skill 方向
      document.querySelectorAll("#direction-chips .chip").forEach((c) => c.classList.remove("active"));
      const skillChip = document.querySelector("#direction-chips .chip[data-direction='实用Skill/效率神器']");
      if (skillChip) {
        skillChip.classList.add("active");
        state.directionFilter = skillChip.dataset.direction;
      }
      renderTopics();
      showModal({
        type: "success",
        title: "🔧 高热度实用 Skill 扫描入库成功",
        summaryHtml: `成功从 GitHub 高增速仓库与开发者社区抓取优质生产力神器，已将 <b>${esc(String(data.created))}</b> 条爆款选题写入飞书（状态为「待定」）。<br><br>涵盖核心痛点平替、端侧隐私及开箱即用指标，页面已自动切换至「🔧 实用 Skill」筛选视图。`,
      });
      toast(`已成功入库 ${data.created} 条实用 Skill`, "success");
    },
  });
}

/* ---------- 阶段 3：文案生成 ---------- */

function renderGenQueue() {
  const select = $("gen-topic-select");
  const queue = state.topics.filter((t) => t["状态"] === "选中");
  const keep = state.recordId && queue.some((t) => t.record_id === state.recordId)
    ? state.recordId : (queue[0] ? queue[0].record_id : "");
  state.recordId = keep;
  select.innerHTML = '<option value="">— 选择写作队列中的选题 —</option>'
    + queue.map((t) => `<option value="${esc(t.record_id)}" ${t.record_id === keep ? "selected" : ""}>${esc(t["选题标题"])}</option>`).join("");
  showTopicDetail();
}

function showTopicDetail() {
  const topic = state.topics.find((t) => t.record_id === state.recordId);
  if (!topic) {
    $("gen-topic-detail").innerHTML = '<p class="empty">选择选题后显示完整字段与备注约束。</p>';
    state.topic = null;
    return;
  }
  state.topic = topic;
  if (!$("draft-title").value) $("draft-title").value = topic["选题标题"] || "";
  $("gen-topic-detail").innerHTML = `
    <div class="detail-row"><span class="detail-key">关键词</span><span>${esc(topic["关键词"] || "—")}</span></div>
    <div class="detail-row"><span class="detail-key">内容方向</span><span>${esc(topic["内容方向"] || "—")}</span></div>
    <div class="detail-row"><span class="detail-key">流量预估</span><span>${esc(topic["流量预估"] || "—")}</span></div>
    <div class="detail-note">备注（硬性约束）：${renderNoteHtml(topic["备注"] || "无")}</div>`;
}

async function loadStyleRefs() {
  const data = await api("/api/style-refs");
  $("style-refs").textContent = !data.ok ? `读取失败：${data.error}`
    : (data.items.length ? data.items.map((r) => r.name).join("、") : "未提供作者文风参考，将使用所选文案风格（可在 style-refs/ 放入历史文章）");
}

async function loadTemplates() {
  const select = $("gen-template-select");
  if (!select) return;
  const data = await api("/api/templates");
  if (!data.ok || !data.items) return;
  select.innerHTML = data.items.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("");
}

async function generateDraft() {
  if (!state.recordId) { toast("先选择一条选题", "error"); return; }
  const template = $("gen-template-select") ? $("gen-template-select").value : "auto";
  await withBusy($("btn-generate"), "生成完整初稿中（截断时自动重试）…", $("gen-status"), {
    request: () => api("/api/generate", {
      method: "POST",
      body: { record_id: state.recordId, template },
      timeout: 600000,
    }),
    onSuccess: (data) => {
      state.markdown = data.markdown;
      $("draft-markdown").value = data.markdown;
      const tplName = data.template_name || "智能推荐";
      $("gen-status").textContent = `已按【${tplName}】生成初稿，请在下方编辑后保存。`;
      updateDraftCapacity();
      toast("初稿已生成，请核对事实、来源和操作细节后保存", "success");
      $("draft-markdown").scrollIntoView({ behavior: "smooth", block: "start" });
    },
  });
}

function updateDraftCapacity() {
  const text = ($("draft-markdown").value || "").trim();
  const chineseChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
  const totalChars = text.length;
  const counterEl = $("draft-char-count");
  const badgeEl = $("draft-meter-badge");
  const alertEl = $("draft-capacity-alert");
  if (!counterEl || !badgeEl) return;

  counterEl.textContent = `${chineseChars} 汉字 (共 ${totalChars} 字符)`;

  // 微信公众号排版后 HTML 膨胀率约为 2.8~3.2 倍，外加封面卡与尾部样式约 1500 字符
  const estHtml = totalChars > 0 ? Math.round(totalChars * 3.0 + 1500) : 0;

  if (chineseChars > 1400 || estHtml > 19000) {
    badgeEl.className = "draft-meter-badge warning";
    badgeEl.textContent = `微信排版预估超限（~${estHtml} / 20000 字符）`;
    if (alertEl) {
      alertEl.hidden = false;
      $("draft-alert-msg").textContent = `当前初稿篇幅偏长（已达 ${chineseChars} 汉字），微信排版后极大概率超过 20,000 字符上限，建议精简至 1350 字以内。`;
    }
  } else {
    badgeEl.className = "draft-meter-badge";
    badgeEl.textContent = totalChars > 0 ? `微信排版容量安全（预估 ~${estHtml} / 20000 字符）` : "微信排版容量：预估安全";
    if (alertEl) alertEl.hidden = true;
  }
}

async function compressDraftAction() {
  const title = $("draft-title").value.trim() || state.title || "未命名文章";
  const markdown = $("draft-markdown").value.trim();
  if (!markdown) { toast("初稿内容为空，无需精简", "error"); return; }

  await withBusy($("btn-compress-draft"), "正在由资深总编智能精简（压缩至 1200 字）…", null, {
    request: () => api("/api/compress-draft", {
      method: "POST",
      body: { title, markdown },
      timeout: 600000,
    }),
    onSuccess: (data) => {
      $("draft-markdown").value = data.markdown;
      state.markdown = data.markdown;
      updateDraftCapacity();
      toast("初稿已成功精简至安全篇幅，核心观点与干货已完整保留！", "success");
    }
  });
}

async function saveDraft() {
  const title = $("draft-title").value.trim();
  const markdown = $("draft-markdown").value;
  if (!state.recordId) { toast("先选择一条选题", "error"); return; }
  if (!title) { toast("请填写文章标题", "error"); return; }
  if (!markdown.trim()) { toast("初稿为空", "error"); return; }
  await withBusy($("btn-save-draft"), "保存中…", $("save-status"), {
    request: () => api("/api/save-draft", {
      method: "POST",
      body: { record_id: state.recordId, title, markdown },
    }),
    onSuccess: async (data) => {
      state.markdown = markdown;
      state.title = title;
      refreshTopics();
      showModal({
        type: "success",
        title: "初稿已保存",
        summaryHtml: `已写入 <b>${esc(data.path || "")}</b>，飞书已回填「文章标题」「稿件路径」。可以带着初稿去预览了。`,
        actions: [{ label: "去预览 →", primary: true, onClick: gotoPreview }],
      });
    },
  });
}

function gotoPreview() {
  state.markdown = $("draft-markdown").value;
  state.title = $("draft-title").value.trim();
  if (!state.markdown.trim() || !state.title) { toast("请先填写标题并生成/编辑初稿"); return; }
  $("pv-title").value = state.title;
  $("pv-author").value = state.author || $("pv-author").value;
  state.pushSource = "generated";
  setStep(4);
}




async function revealInFinder(path, label, btn) {
  if (!path) { toast(`${label}路径不存在`, "error"); return; }
  await withBusy(btn, "正在打开 Finder…", null, {
    request: () => api("/api/reveal-file", {
      method: "POST",
      body: { path },
      timeout: 20000,
    }),
    onSuccess: (data) => toast(`已在 Finder 中定位${label}：${data.name || "文件"}`, "success"),
  });
}

/* ---------- 阶段 4：文案预览 ---------- */

/* 拉取 bmmd 样式清单填充下拉框；引擎切换时显示/隐藏对应字段。 */
async function loadBmmdStyles() {
  const data = await api("/api/bmmd-styles");
  if (!data.ok) { toast(`bmmd 样式列表加载失败：${data.error}`, "error"); return; }
  const fill = (select, entries, pick) => {
    select.innerHTML = Object.entries(entries).map(
      ([value, label]) => `<option value="${esc(value)}">${esc(label)}</option>`,
    ).join("");
    select.value = pick;
  };
  fill($("pv-style"), data.styles, data.default || "kami");
  fill($("pv-code-theme"), data.code_themes, "");
}

function updateEngineFields() {
  const engine = $("pv-engine").value;
  state.engine = engine;
  $("field-pv-theme").hidden = engine !== "builtin";
  $("field-pv-style").hidden = engine !== "bmmd";
  $("field-pv-code-theme").hidden = engine !== "bmmd";
}

async function buildPreview() {
  state.title = $("pv-title").value.trim();
  state.author = $("pv-author").value.trim();
  state.theme = $("pv-theme").value;
  state.engine = $("pv-engine").value;
  state.bmmdStyle = $("pv-style").value || "kami";
  state.codeTheme = $("pv-code-theme").value || "";
  state.hook = $("pv-hook").value.trim();
  state.subtitle = $("pv-subtitle").value.trim();
  if (!state.title) { toast("请填写标题", "error"); return; }
  if (!state.markdown.trim()) { toast("初稿为空，请回到「文案生成」生成初稿", "error"); return; }
  await withBusy($("btn-preview"), "自动提炼主题、生成封面并排版中…", null, {
    request: () => api("/api/preview", {
      method: "POST",
      timeout: 600000,
      body: {
        markdown: state.markdown, title: state.title, author: state.author,
        theme: state.theme, hook: state.hook, subtitle: state.subtitle,
        engine: state.engine, style: state.bmmdStyle, code_theme: state.codeTheme,
      },
    }),
    onSuccess: (data) => {
      showPreview(data.html, data.chars, data.limit);
      state.previewHtml = data.html;
      state.pushSource = "generated";
      if ($("pv-cover-summary") && data.cover) {
        $("pv-cover-summary").textContent = `AI 封面：${data.cover.core_theme} · ${data.cover.cached ? "已复用当前文章封面" : "已根据正文自动生成"}`;
      }
      toast("主题封面与排版预览已生成", "success");
      $("preview-iframe").scrollIntoView({ behavior: "smooth", block: "start" });
    },
  });
}

function showPreview(html, chars, limit) {
  const frame = $("preview-iframe");
  frame.srcdoc = html;
  frame.classList.add("visible");
  $("preview-placeholder").style.display = "none";
  if (chars !== undefined) {
    const over = limit && chars > limit;
    $("preview-chars").textContent = `正文字符数 ${chars}${limit ? ` / ${limit}${over ? "（超出限制，精简后再推送）" : ""}` : ""}`;
    $("preview-chars").style.color = over ? "#b91c1c" : "";
  }
}

function importHtmlFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    const html = String(reader.result);
    state.importedHtml = html;
    state.previewHtml = html;
    state.pushSource = "imported";
    showPreview(html, html.length, null);
    toast(`已导入：${file.name}（推送时将直接使用该 HTML）`);
  };
  reader.readAsText(file, "utf-8");
}

/* ---------- 阶段 5：发送公众号 ---------- */

function renderPushConfig() {
  const sourceText = state.pushSource === "imported"
    ? "导入的外部排版 HTML"
    : (state.engine === "bmmd"
      ? `bmmd 引擎现场排版（${esc($("pv-style").selectedOptions[0]?.textContent || state.bmmdStyle)}）`
      : `内置主题现场排版（${esc($("pv-theme").selectedOptions[0]?.textContent || state.theme)}）`);
  $("push-config").innerHTML = `
    <div class="item"><span>选题记录</span><b>${state.recordId ? esc((state.topic || {}).选题标题 || state.recordId) : "未选择"}</b></div>
    <div class="item"><span>标题</span><b>${esc(state.title || "—")}</b></div>
    <div class="item"><span>作者</span><b>${esc(state.author || "—")}</b></div>
    <div class="item"><span>内容来源</span><b>${sourceText}</b></div>`;
  updateToutiaoArticleSelect();
}

async function pushDraft() {
  if (!state.recordId) { toast("请先在第 3 步选择一条写作选题", "error"); return; }
  state.title = $("pv-title").value.trim() || state.title;
  state.author = $("pv-author").value.trim() || state.author;
  if (!state.title) { toast("请填写标题", "error"); return; }
  if (!state.previewHtml) {
    toast(state.pushSource === "imported" ? "先导入外部排版 HTML" : "先在第 4 步生成排版预览", "error");
    return;
  }
  const body = {
    record_id: state.recordId, title: state.title, author: state.author, theme: state.theme,
    markdown: state.pushSource === "generated" ? state.markdown : "",
    imported_html: state.pushSource === "imported" ? state.previewHtml : "",
    hook: state.hook, subtitle: state.subtitle,
    engine: state.engine, style: state.bmmdStyle, code_theme: state.codeTheme,
  };
  await withBusy($("btn-push"), "推送中（图片上传 + 回读校验，约 30~60 秒）…", $("push-status"), {
    request: () => api("/api/push", { method: "POST", body, timeout: 600000 }),
    onSuccess: async (data) => {
      const record = data.record;
      const v = record.verification || {};
      const check = (val) => `<span class="pass">${val ? "通过" : "异常"}</span>`;
      const box = $("push-result");
      box.hidden = false;
      box.className = "result success";
      box.innerHTML = `
        <b>推送成功</b>（草稿已保存到公众号后台，不会自动发表）
        <table>
          <tr><td>草稿 media_id</td><td>${esc(record.draft_media_id)}</td></tr>
          <tr><td>操作类型</td><td>${record.action === "update" ? "更新已有草稿" : "新建草稿"}</td></tr>
          <tr><td>标题回读</td><td>${check(v.title_equal)}</td></tr>
          <tr><td>正文文字回读</td><td>${check(v.body_equal)}</td></tr>
          <tr><td>图片数量</td><td>${record.image_count} 张 ${check(v.image_count_equal)}</td></tr>
          <tr><td>图片顺序</td><td>${check(v.image_order_equal)}</td></tr>
          <tr><td>运行目录</td><td>${esc(record.run_dir || "")}</td></tr>
        </table>
        飞书选题状态已回填「已推送」。去公众号后台草稿箱做发布前最后一眼检查。`;
      showModal({
        type: "success",
        title: "推送成功",
        summaryHtml: `草稿已保存到公众号后台（不会自动发表）。<br>标题回读 ${check(v.title_equal)} ｜ 正文回读 ${check(v.body_equal)} ｜ 图片 ${esc(String(record.image_count))} 张 ${check(v.image_count_equal)}。<br>飞书选题状态已回填「已推送」，去草稿箱做发布前最后一眼检查。<br><br>💡 <b>矩阵分发</b>：母稿已就绪，可一键衍生小红书与今日头条文案。`,
        actions: [
          { label: "📕 生成小红书文案", primary: false, onClick: () => generateXhsCopy() },
          { label: "📰 生成今日头条文案", primary: true, onClick: () => generateToutiaoCopy() },
        ],
      });
      toast("推送成功，草稿已进公众号草稿箱", "success");
      refreshTopics();
      loadRuns();
    },
    onError: (data) => {
      const box = $("push-result");
      box.hidden = false;
      box.className = "result error";
      box.innerHTML = `<b>推送失败</b><br>${esc(data.error)}<br>常见原因：IP 未加白名单、AppSecret 错误、正文超 2 万字符。`;
      toast("推送失败，见错误弹框", "error");
    },
  });
}

/* ---------- 公众号文章离线导出包 ---------- */

function renderPackage(pkg) {
  const box = $("pkg-result");
  box.hidden = false;
  box.className = "result success";
  const rows = (pkg.files || []).map((f) => `
    <tr>
      <td><b>${esc(f.name)}</b></td>
      <td>${(f.size / 1024).toFixed(1)} KB</td>
      <td><a href="/api/run-file?path=${encodeURIComponent(f.path)}" target="_blank" rel="noopener">下载/打开</a></td>
    </tr>`).join("");
  box.innerHTML = `
    <b>导出包已生成</b>：<code>${esc(pkg.dir)}</code>
    <table>${rows}</table>
    ${(pkg.warnings || []).length ? `<div class="warn">部分文件生成异常：<br>${pkg.warnings.map(esc).join("<br>")}</div>` : ""}
    包含母稿 Markdown、排版 HTML 与 Word 离线存档，供本地备份使用。`;
}

async function publishPackage() {
  state.title = $("pv-title").value.trim() || state.title || $("draft-title").value.trim();
  state.author = $("pv-author").value.trim() || state.author;
  state.theme = $("pv-theme").value || state.theme;
  state.engine = $("pv-engine").value || state.engine;
  state.bmmdStyle = $("pv-style").value || state.bmmdStyle;
  state.codeTheme = $("pv-code-theme").value || "";
  state.hook = $("pv-hook").value.trim();
  state.subtitle = $("pv-subtitle").value.trim();
  const markdown = state.markdown || $("draft-markdown").value;
  if (!state.title) { toast("请先填写标题", "error"); return; }
  if (!markdown.trim()) { toast("母稿为空，请先在第 3 步生成公众号初稿", "error"); return; }
  const body = {
    record_id: state.recordId, title: state.title, author: state.author, theme: state.theme,
    markdown, hook: state.hook, subtitle: state.subtitle,
    engine: state.engine, style: state.bmmdStyle, code_theme: state.codeTheme,
  };
  await withBusy($("btn-publish-package"), "导出打包中…", $("pkg-status"), {
    request: () => api("/api/publish-package", { method: "POST", body, timeout: 600000 }),
    onSuccess: (data) => {
      renderPackage(data.package);
      $("pkg-status").textContent = `已生成 ${(data.package.files || []).length} 个文件`;
      toast("离线导出包已生成", "success");
      $("pkg-result").scrollIntoView({ behavior: "smooth", block: "start" });
    },
  });
}

/* ---------- 多平台矩阵衍生：小红书 & 今日头条 ---------- */

function renderXhsResult(data) {
  const box = $("xhs-result");
  if (!box) return;
  box.hidden = false;
  box.className = "matrix-result";
  box.innerHTML = `
    <div class="matrix-result-head">
      <b>📕 小红书爆款文案生成成功（对应 /tuiwen-xhs-gen 技能）</b>
      <button class="btn small primary" id="btn-reveal-xhs" type="button">📂 在 Finder 中定位文案</button>
    </div>
    <div class="matrix-result-body">
      <img class="matrix-thumb xhs" src="${esc(data.base64_cover)}" alt="小红书置顶封面图" title="点击查看大图" style="cursor:pointer;" onclick="window.open('${esc(data.base64_cover)}')">
      <div class="matrix-meta">
        <div class="matrix-title">${esc(data.title)}</div>
        <div class="hint">点击「在 Finder 中定位文案」打开本地笔记；封面原图保存在同目录的 images 文件夹，方便上传到小红书。</div>
        <div class="matrix-files">
          <a class="btn small ghost" href="/api/run-file?path=${encodeURIComponent(data.md_path)}" target="_blank" rel="noopener">下载 Markdown 笔记</a>
          <a class="btn small ghost" href="/api/run-file?path=${encodeURIComponent(data.cover_path)}" target="_blank" rel="noopener">下载 3:4 封面原图</a>
        </div>
        <div class="matrix-preview-text">${esc(data.markdown.slice(0, 500))}…</div>
      </div>
    </div>
  `;
  $("btn-reveal-xhs")?.addEventListener("click", (event) => revealInFinder(data.md_path, "小红书 Markdown 文案", event.currentTarget));
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderToutiaoResult(data) {
  const box = $("jrtt-result");
  if (!box) return;
  box.hidden = false;
  box.className = "matrix-result";
  const coverUrl = `/api/run-file?path=${encodeURIComponent(data.cover_path)}`;
  box.innerHTML = `
    <div class="matrix-result-head">
      <b>📰 今日头条长图文与 DOCX 生成成功（对应 /tuiwen-jrtt-gen 技能）</b>
      <button class="btn small primary" id="btn-reveal-jrtt" type="button">📂 在 Finder 中定位文案</button>
    </div>
    <div class="matrix-result-body">
      <img class="matrix-thumb jrtt" src="${esc(coverUrl)}" alt="今日头条 16:9 封面" title="点击查看大图" style="cursor:pointer;" onclick="window.open('${esc(coverUrl)}')">
      <div class="matrix-meta">
        <div class="matrix-title">${esc(data.title)}</div>
        <div class="hint">点击「在 Finder 中定位文案」选中本地 DOCX；Markdown 与封面图保存在同目录，方便上传到今日头条。</div>
        <div class="matrix-files">
          <a class="btn small primary" href="/api/run-file?path=${encodeURIComponent(data.docx_path)}" target="_blank" rel="noopener">📥 下载排版 DOCX</a>
          <a class="btn small ghost" href="/api/run-file?path=${encodeURIComponent(data.md_path)}" target="_blank" rel="noopener">下载 Markdown 原文</a>
          <a class="btn small ghost" href="${esc(coverUrl)}" target="_blank" rel="noopener">下载 16:9 封面图</a>
        </div>
        <div class="matrix-preview-text">${esc(data.markdown.slice(0, 500))}…</div>
      </div>
    </div>
  `;
  $("btn-reveal-jrtt")?.addEventListener("click", (event) => revealInFinder(data.docx_path, "今日头条 DOCX 文案", event.currentTarget));
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function triggerGenerateXhs({ title, markdown = "", runDir = "", recordId = "", btn, statusEl = null }) {
  if (!title) { toast("请先填写或选择文章标题", "error"); return; }
  const taskKey = `xhs_${runDir || title}`;
  if (state.generatingTasks.has(taskKey)) return;
  state.generatingTasks.add(taskKey);

  // 立即刷新，让列表对应的按钮进入生成中状态
  loadRuns();

  try {
    await withBusy(btn, "小红书生成中…", statusEl, {
      request: () => api("/api/generate-xhs", {
        method: "POST",
        body: { title, markdown, record_id: recordId, run_dir: runDir }
      }),
      onSuccess: async (data) => {
        renderXhsResult(data);
        toast(`小红书文案与 handraw 风格主题封面生成成功！${data.cover_plan?.style_id ? `风格 ${data.cover_plan.style_id} · 构图 ${data.cover_plan.layout_id}` : ""}`, "success");
        state.generatingTasks.delete(taskKey);
        await loadRuns();
        showModal({
          type: "success",
          title: "📕 小红书文案生成成功（素材已落盘）",
          summaryHtml: `已根据《${esc(data.title)}》提炼小红书图文，并由 handraw-style 自动匹配风格、构图和配色后生成 3:4 封面。<br><br>点击「在 Finder 中定位文案」打开本地笔记，封面原图位于同目录的 images 文件夹。`,
          actions: [
            { label: "📂 在 Finder 中定位文案", primary: true, onClick: (btn) => revealInFinder(data.md_path, "小红书 Markdown 文案", btn) },
            { label: "关闭", onClick: closeModal }
          ]
        });
      },
      onError: (data) => {
        state.generatingTasks.delete(taskKey);
        loadRuns();
        const box = $("xhs-result");
        if (box) {
          box.hidden = false;
          box.className = "result error";
          box.innerHTML = `<b>小红书生成失败</b><br>${esc(data.error)}`;
        } else {
          toast(`小红书生成失败: ${data.error}`, "error");
        }
      }
    });
  } finally {
    state.generatingTasks.delete(taskKey);
    loadRuns();
  }
}

async function triggerGenerateToutiao({ title, markdown = "", runDir = "", recordId = "", btn, statusEl = null }) {
  if (!title) { toast("请先填写或选择文章标题", "error"); return; }
  const taskKey = `toutiao_${runDir || title}`;
  if (state.generatingTasks.has(taskKey)) return;
  state.generatingTasks.add(taskKey);

  // 立即刷新，让列表对应的按钮进入生成中状态
  loadRuns();

  try {
    await withBusy(btn, "头条生成中…", statusEl, {
      request: () => api("/api/generate-toutiao", {
        method: "POST",
        body: { title, markdown, record_id: recordId, run_dir: runDir }
      }),
      onSuccess: async (data) => {
        renderToutiaoResult(data);
        toast(`今日头条文案与 handraw 风格主题封面生成成功！${data.cover_plan?.style_id ? `风格 ${data.cover_plan.style_id} · 构图 ${data.cover_plan.layout_id}` : ""}`, "success");
        state.generatingTasks.delete(taskKey);
        await loadRuns();
        showModal({
          type: "success",
          title: "📰 今日头条长图文与 DOCX 生成成功（素材已落盘）",
          summaryHtml: `已根据《${esc(data.title)}》生成深度长图文，并由 handraw-style 自动匹配风格、构图和配色后生成 16:9 封面，已排版为 DOCX 文件。<br><br>点击「在 Finder 中定位文案」选中本地 DOCX，Markdown 和封面图也在同一目录。`,
          actions: [
            { label: "📂 在 Finder 中定位文案", primary: true, onClick: (btn) => revealInFinder(data.docx_path, "今日头条 DOCX 文案", btn) },
            { label: "关闭", onClick: closeModal }
          ]
        });
      },
      onError: (data) => {
        state.generatingTasks.delete(taskKey);
        loadRuns();
        const box = $("jrtt-result");
        if (box) {
          box.hidden = false;
          box.className = "result error";
          box.innerHTML = `<b>今日头条生成失败</b><br>${esc(data.error)}`;
        } else {
          toast(`今日头条生成失败: ${data.error}`, "error");
        }
      }
    });
  } finally {
    state.generatingTasks.delete(taskKey);
    loadRuns();
  }
}

async function generateXhsCopy() {
  const title = $("pv-title").value.trim() || state.title || $("draft-title").value.trim();
  const markdown = state.markdown || $("draft-markdown").value || "";
  await triggerGenerateXhs({
    title,
    markdown,
    recordId: state.recordId || "",
    btn: $("btn-gen-xhs"),
    statusEl: $("multiplatform-status")
  });
}

async function generateToutiaoCopy() {
  const title = $("pv-title").value.trim() || state.title || $("draft-title").value.trim();
  const markdown = state.markdown || $("draft-markdown").value || "";
  await triggerGenerateToutiao({
    title,
    markdown,
    recordId: state.recordId || "",
    btn: $("btn-gen-jrtt"),
    statusEl: $("multiplatform-status")
  });
}

/* ---------- 今日头条自动推送（已停用，仅保留旧调用保护） ---------- */

let lastChromeStatus = { available: false, launch_cmd: "" };

async function checkChromeStatus(showToast = false) {
  try {
    const res = await api("/api/chrome-status");
    const badge = $("toutiao-chrome-badge");
    if (res.ok && res.available) {
      lastChromeStatus = res;
      if (badge) {
        badge.textContent = "🟢 Chrome 9222: 已就绪";
        badge.style.background = "#ecfdf5";
        badge.style.color = "#047857";
        badge.style.borderColor = "#a7f3d0";
      }
      if (showToast) toast("Chrome 9222 调试端口已就绪，可直接执行推送！", "success");
      return true;
    } else {
      lastChromeStatus = res.ok ? res : { available: false, launch_cmd: "" };
      if (badge) {
        badge.textContent = "⚠️ Chrome 9222: 未开启 (点击指引)";
        badge.style.background = "#fef2f2";
        badge.style.color = "#b91c1c";
        badge.style.borderColor = "#fecaca";
      }
      if (showToast) {
        showChromeGuideModal();
      }
      return false;
    }
  } catch (e) {
    if (showToast) toast("检查 Chrome 状态失败: " + e.message, "error");
    return false;
  }
}

function showChromeGuideModal() {
  const cmd = lastChromeStatus.launch_cmd || 'open -n -a "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/Library/Application Support/Google/Chrome-Automation" --no-first-run';
  showModal({
    type: "info",
    title: "🔧 启动 Chrome 调试端口指引",
    summaryHtml: `
      今日头条自动化推送通过 <b>CDP 协议直接接管你本地真实的 Chrome 浏览器</b>，以保证 100% 真实环境与登录态，杜绝风控封号风险。<br><br>
      <b>极简操作三步走：</b><br>
      1. 打开系统终端（Terminal），复制并回车运行以下命令启动 Chrome（<b>注：由于 Chrome 安全机制，必须携带独立数据目录，否则系统会静默忽略调试端口</b>）：<br>
      <pre style="background:#1e293b; color:#f8fafc; padding:10px; border-radius:6px; font-size:12px; overflow-x:auto; user-select:all; margin:8px 0;"><code>${esc(cmd)}</code></pre>
      2. 在弹出的 Chrome 中打开 <code>https://mp.toutiao.com</code> 扫码登录头条号（只需登录一次，后续持久保留登录态）；<br>
      3. 本项目已停用自动填报，请下载生成的素材后通过今日头条官方后台人工发布。
    `,
    actions: [
      { label: "我已启动，再次检测", primary: true, onClick: () => checkChromeStatus(true) },
    ]
  });
}

let toutiaoArticleList = [];

function updateToutiaoArticleSelect(preferredTitle = "") {
  const select = $("toutiao-article-select");
  const countEl = $("toutiao-article-count");
  if (!select) return;

  const currentVal = select.value;
  const list = [];

  // 1. 若当前流程有生成或编辑中的文章
  const curTitle = ($("pv-title")?.value.trim() || state.title || $("draft-title")?.value.trim() || "").trim();
  if (curTitle) {
    list.push({
      key: "current",
      title: curTitle,
      runDir: state.runDir || "",
      isCurrent: true,
      label: `[当前正在编辑] ${curTitle}`,
    });
  }

  // 2. 遍历历史 runs
  const runs = state.runs || [];
  runs.forEach((r, idx) => {
    if (!r.title) return;
    if (curTitle && r.title === curTitle && !r.run_dir) return;
    const dateStr = r.pushed_at ? r.pushed_at.slice(5, 10) : "";
    list.push({
      key: `run_${idx}`,
      title: r.title,
      runDir: r.run_dir || "",
      platforms: r.platforms || {},
      isCurrent: false,
      label: `[历史 ${dateStr || '稿件'}] ${r.title}`,
    });
  });

  toutiaoArticleList = list;

  if (list.length === 0) {
    select.innerHTML = '<option value="">暂无可用文章，请先在前置步骤生成稿件</option>';
    if (countEl) countEl.textContent = "无可用文章";
    renderToutiaoMaterialInfo(null);
    return;
  }

  if (countEl) countEl.textContent = `共 ${list.length} 篇可选`;

  select.innerHTML = list.map((item, idx) => `
    <option value="${idx}">${esc(item.label)}</option>
  `).join("");

  let targetIdx = 0;
  if (preferredTitle) {
    const found = list.findIndex(item => item.title === preferredTitle);
    if (found >= 0) targetIdx = found;
  } else if (currentVal !== "" && Number(currentVal) < list.length) {
    targetIdx = Number(currentVal);
  }

  select.value = String(targetIdx);
  renderToutiaoMaterialInfo(list[targetIdx]);
  renderXhsMaterialInfo(list[targetIdx]);
}

function getSelectedToutiaoArticle() {
  const select = $("toutiao-article-select");
  if (!select || !select.value) return null;
  const idx = Number(select.value);
  return toutiaoArticleList[idx] || null;
}

function renderToutiaoMaterialInfo(item) {
  const infoEl = $("toutiao-material-info");
  const btnGen = $("btn-gen-toutiao-material");
  if (!infoEl) return;
  if (!item) {
    infoEl.innerHTML = `<span style="color:#94a3b8;">暂无选中的文章物料信息</span>`;
    return;
  }

  let plat = item.platforms?.toutiao;
  if (!plat && item.title) {
    const matchRun = (state.runs || []).find(r => r.title === item.title || (r.platforms?.toutiao?.title === item.title));
    if (matchRun) plat = matchRun.platforms?.toutiao;
  }

  const jrtt = plat || {};
  const hasJrttTitle = Boolean(jrtt.title);
  const hasCover = Boolean(jrtt.cover_path);
  const isPublished = jrtt.status === "已发布" || jrtt.status === "已推草稿";
  const isSentEmail = jrtt.status === "已发送邮件";
  if (btnGen) {
    btnGen.innerHTML = (hasJrttTitle && hasCover) ? "🔄 重新生成头条文案/封面" : "📰 1. 点击生成今日头条文案";
  }

  let statusHtml = '<span class="badge-status pending" style="font-size:11px;">仅生成素材</span>';
  if (isPublished) {
    statusHtml = `<span class="badge-status sent" style="font-size:11px; background:#ecfdf5; color:#059669; border-color:#a7f3d0;">已发布 (${esc(formatShortSentAt(jrtt.published_at || jrtt.pushed_at) || '已发布')})</span>`;
  } else if (isSentEmail) {
    statusHtml = `<span class="badge-status sent" style="font-size:11px;">已发邮件 (${esc(cleanScheduleText(jrtt.schedule_time) || '')})</span>`;
  }

  infoEl.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; flex-wrap:wrap; gap:6px;">
      <div><b>母稿标题</b>：<span style="color:#0f172a; font-weight:600;">${esc(item.title)}</span></div>
      <div>${statusHtml}</div>
    </div>
    <div style="margin-bottom:3px;">
      <b>头条爆款标题</b>：
      ${hasJrttTitle ? `<span style="color:#e22c38; font-weight:600;">${esc(jrtt.title)}</span> <span class="badge-status ok" style="font-size:11px;">已就绪</span>` : `<span style="color:#b91c1c; font-weight:500;">未生成（请先点击下方「1. 点击生成今日头条文案」）</span>`}
    </div>
    <div style="margin-bottom:4px;">
      <b>16:9 封面物料</b>：
      ${hasCover ? `<span class="badge-status ok" style="font-size:11px;">已生成封面</span> <small style="color:#64748b;">(${esc(jrtt.cover_path.split('/').pop())})</small>` : `<span style="color:#b91c1c; font-weight:500;">未生成</span>`}
    </div>
    <div style="padding-top:4px; font-size:12px; color:#475569; background:#fff; padding:6px 8px; border-radius:4px; border:1px solid #e2e8f0;">
      ✅ <b>交付内容</b>：16:9 封面、Markdown 长图文与排版 DOCX；请人工审核后通过今日头条官方后台发布
    </div>
  `;
}

async function regenerateSelectedToutiaoMaterial() {
  const selected = getSelectedToutiaoArticle();
  if (!selected || !selected.title) {
    toast("请先选择一篇待生成的文章", "error");
    return;
  }
  const btn = $("btn-gen-toutiao-material");
  const statusEl = $("push-toutiao-status");
  await triggerGenerateToutiao({
    title: selected.title,
    markdown: selected.isCurrent ? (state.markdown || $("draft-markdown")?.value || "") : "",
    runDir: selected.runDir || "",
    recordId: "",
    btn,
    statusEl,
  });
  await loadRuns();
  updateToutiaoArticleSelect(selected.title);
  toast("头条文案与 16:9 封面已生成！请下载素材并人工发布。", "success");
}

async function pushToutiaoDraft(opts = {}) {
  toast("今日头条自动推送已停用，请下载素材后通过官方后台人工发布。", "info");
  return;
  const isReady = await checkChromeStatus(false);
  if (!isReady) {
    showChromeGuideModal();
    return;
  }

  let selected = null;
  if (!opts.title) {
    selected = getSelectedToutiaoArticle();
    if (!selected) {
      toast("请先在下拉框中选择一篇待推送的文章", "error");
      return;
    }
  }

  const title = (opts.title || selected?.title || "").trim();
  const runDir = opts.runDir !== undefined ? opts.runDir : (selected?.runDir || "");
  const markdown = opts.markdown || (selected?.isCurrent ? (state.markdown || $("draft-markdown")?.value || "") : "");

  if (!title) {
    toast("文章标题不能为空，请先在下拉框选定文章", "error");
    return;
  }

  const btn = opts.btn || $("btn-push-toutiao");
  const statusEl = opts.statusEl || $("push-toutiao-status");
  const resultBox = $("push-toutiao-result");

  await withBusy(btn, "发布中（连接 Chrome + 注入富文本 + 上传封面 + 选上海 + 勾声明 + 预览并发布）…", statusEl, {
    request: () => api("/api/push-toutiao", {
      method: "POST",
      body: { title, markdown, run_dir: runDir }
    }),
    onSuccess: async (data) => {
      if (resultBox && !opts.btn) {
        resultBox.hidden = false;
        resultBox.className = "result success";
        resultBox.innerHTML = `
          <b>今日头条文章已成功预览并发布！</b><br>
          文章标题：<code>${esc(data.title || title)}</code><br>
          封面图：${data.cover_uploaded ? '<span class="pass">16:9 横版封面上传成功</span>' : '已就绪'}<br>
          发布设置：<span>📍 位置: 上海 ｜ 📝 声明: 个人观点，仅供参考</span><br>
          页面地址：<a href="${esc(data.page_url || 'https://mp.toutiao.com')}" target="_blank">${esc(data.page_url || '已在 Chrome 中完成发布')}</a>
        `;
      }
      showModal({
        type: "success",
        title: "📰 今日头条已成功预览并发布！",
        summaryHtml: `
          已成功通过 Playwright 接管本地 Chrome，将《<b>${esc(data.title || title)}</b>》填入创作者后台并完成发布流程！<br><br>
          ✅ <b>已填入爆款标题</b>：${esc(data.title || title)}<br>
          ✅ <b>已上传 16:9 封面</b>：横版大片封面就绪<br>
          ✅ <b>已自动选定位置</b>：上海<br>
          ✅ <b>已勾选作品声明</b>：个人观点，仅供参考<br>
          🚀 <b>已点击「预览并发布」</b>并完成发布！<br><br>
          👉 现可在 Chrome 中查看刚发布的头条文章。
        `,
        actions: [
          { label: "我知道了", primary: true, onClick: () => {} }
        ]
      });
      toast("今日头条已成功预览并发布！", "success");
      await loadRuns();
      updateToutiaoArticleSelect(title);
    },
    onError: (data) => {
      if (resultBox && !opts.btn) {
        resultBox.hidden = false;
        resultBox.className = "result error";
        resultBox.innerHTML = `<b>头条推送发布失败</b><br>${esc(data.error)}`;
      }
      if (data.error && data.error.includes("未检测到运行中的 Chrome 调试端口")) {
        showChromeGuideModal();
      } else {
        toast(`头条推送发布失败: ${data.error}`, "error");
      }
    }
  });
}

/* ---------- 小红书自动推送（已停用，仅保留旧调用保护） ---------- */

function renderXhsMaterialInfo(item) {
  const infoEl = $("xhs-material-info");
  const btnGen = $("btn-gen-xhs-material");
  if (!infoEl) return;
  if (!item) {
    infoEl.innerHTML = `<span style="color:#94a3b8;">暂无选中的文章物料信息</span>`;
    return;
  }

  let plat = item.platforms?.xhs;
  if (!plat && item.title) {
    const matchRun = (state.runs || []).find(r => r.title === item.title || (r.platforms?.xhs?.title === item.title));
    if (matchRun) plat = matchRun.platforms?.xhs;
  }

  const xhs = plat || {};
  const hasXhsTitle = Boolean(xhs.title);
  const hasCover = Boolean(xhs.cover_path);
  const isDraftSaved = xhs.status === "已存草稿";
  const isPublished = xhs.status === "已发布";
  const isSentEmail = xhs.status === "已发送邮件";
  if (btnGen) {
    btnGen.innerHTML = (hasXhsTitle && hasCover) ? "🔄 重新生成小红书文案/封面" : "📕 1. 点击生成小红书文案与封面";
  }

  let statusHtml = '<span class="badge-status pending" style="font-size:11px;">仅生成素材</span>';
  if (isDraftSaved) {
    statusHtml = `<span class="badge-status sent" style="font-size:11px; background:#ecfdf5; color:#059669; border-color:#a7f3d0;">已存草稿 (${esc(formatShortSentAt(xhs.published_at || xhs.updated_at) || '草稿')})</span>`;
  } else if (isPublished) {
    statusHtml = `<span class="badge-status sent" style="font-size:11px; background:#ecfdf5; color:#059669; border-color:#a7f3d0;">已发布 (${esc(formatShortSentAt(xhs.published_at) || '已发布')})</span>`;
  } else if (isSentEmail) {
    statusHtml = `<span class="badge-status sent" style="font-size:11px;">已发邮件 (${esc(cleanScheduleText(xhs.schedule_time) || '')})</span>`;
  }

  infoEl.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; flex-wrap:wrap; gap:6px;">
      <div><b>母稿标题</b>：<span style="color:#0f172a; font-weight:600;">${esc(item.title)}</span></div>
      <div>${statusHtml}</div>
    </div>
    <div style="margin-bottom:3px;">
      <b>小红书爆款标题</b>：
      ${hasXhsTitle ? `<span style="color:#ff2442; font-weight:600;">${esc(xhs.title)}</span> <span class="badge-status ok" style="font-size:11px;">已就绪</span>` : `<span style="color:#b91c1c; font-weight:500;">未生成（请先点击下方「1. 点击生成小红书文案与封面」）</span>`}
    </div>
    <div style="margin-bottom:4px;">
      <b>3:4 竖版置顶封面</b>：
      ${hasCover ? `<span class="badge-status ok" style="font-size:11px;">已渲染封面</span> <small style="color:#64748b;">(${esc(xhs.cover_path.split('/').pop())})</small>` : `<span style="color:#b91c1c; font-weight:500;">未生成</span>`}
    </div>
    <div style="padding-top:4px; font-size:12px; color:#475569; background:#fff; padding:6px 8px; border-radius:4px; border:1px solid #fed7d7;">
      ✅ <b>交付内容</b>：3:4 置顶封面与 Markdown 笔记；地点、话题和原创声明请人工审核后，通过小红书官方后台发布
    </div>
  `;
}

async function regenerateSelectedXhsMaterial() {
  const selected = getSelectedToutiaoArticle();
  if (!selected || !selected.title) {
    toast("请先选择一篇待生成的文章", "error");
    return;
  }
  const btn = $("btn-gen-xhs-material");
  const statusEl = $("push-xhs-status");
  await triggerGenerateXhs({
    title: selected.title,
    markdown: selected.isCurrent ? (state.markdown || $("draft-markdown")?.value || "") : "",
    runDir: selected.runDir || "",
    recordId: "",
    btn,
    statusEl,
  });
  await loadRuns();
  updateToutiaoArticleSelect(selected.title);
  toast("小红书文案与 3:4 封面已生成！请下载素材并人工发布。", "success");
}

async function pushXhsDraft(opts = {}) {
  toast("小红书自动推送已停用，请下载素材后通过官方后台人工发布。", "info");
  return;
  const isReady = await checkChromeStatus(false);
  if (!isReady) {
    showChromeGuideModal();
    return;
  }

  let selected = null;
  if (!opts.title) {
    selected = getSelectedToutiaoArticle();
    if (!selected) {
      toast("请先在下拉框中选择一篇待推送的文章", "error");
      return;
    }
  }

  const title = (opts.title || selected?.title || "").trim();
  const runDir = opts.runDir !== undefined ? opts.runDir : (selected?.runDir || "");
  const markdown = opts.markdown || (selected?.isCurrent ? (state.markdown || $("draft-markdown")?.value || "") : "");
  const action = opts.action || "draft";

  if (!title) {
    toast("文章标题不能为空，请先在下拉框选定文章", "error");
    return;
  }

  const btn = opts.btn || $("btn-push-xhs");
  const statusEl = opts.statusEl || $("push-xhs-status");
  const resultBox = $("push-xhs-result");

  await withBusy(btn, "自动推送已停用…", statusEl, {
    request: () => api("/api/push-xhs", {
      method: "POST",
      body: { title, markdown, run_dir: runDir, action }
    }),
    onSuccess: async (data) => {
      if (resultBox && !opts.btn) {
        resultBox.hidden = false;
        resultBox.className = "result success";
        resultBox.innerHTML = `
          <b>小红书笔记填报成功（已暂存草稿箱）！</b><br>
          笔记标题：<code>${esc(data.title || title)}</code><br>
          排版范式：<span>${esc(data.style_name || '标准排版')}</span> ｜ 话题数: <span>${data.topics_count || (data.topics || []).length} 个</span><br>
          打卡地点：<span>📍 ${esc(data.location || '已随机选定')}</span> ｜ 原创声明: <span>${data.original_declared ? '✅ 已勾选' : '已就绪'}</span><br>
          页面地址：<a href="${esc(data.page_url || 'https://creator.xiaohongshu.com')}" target="_blank">${esc(data.page_url || '已在 Chrome 小红书后台暂存')}</a>
        `;
      }
      showModal({
        type: "success",
        title: "📕 小红书笔记已成功暂存为草稿！",
        summaryHtml: `
          已成功通过 Playwright 接管本地 Chrome，将《<b>${esc(data.title || title)}</b>》填入小红书创作者后台并暂存草稿！<br><br>
          ✅ <b>已填入 20 字爆款标题</b>：${esc(data.title || title)}<br>
          ✅ <b>已上传 3:4 超清封面</b>：设计置顶封面已上传<br>
          ✅ <b>已应用随机排版</b>：${esc(data.style_name || '精选排版')}<br>
          ✅ <b>已添加热门话题</b>：${(data.topics || []).map(t => `<code style="background:#fee2e2; color:#b91c1c;">#${esc(t.replace(/^#/, ''))}</code>`).join(' ')}（共 ${data.topics_count || (data.topics || []).length} 个）<br>
          ✅ <b>已开启原创声明</b>：已通过须知弹窗确认<br>
          ✅ <b>已随机打卡地点</b>：📍 <b>${esc(data.location || '已随机选定')}</b><br>
          💾 <b>动作执行</b>：已点击「暂存离开（保存草稿）」，供您人工校验稳定性！<br><br>
          👉 现可在本地 Chrome 的小红书后台查看刚刚生成的草稿笔记。
        `,
        actions: [
          { label: "我知道了", primary: true, onClick: () => {} }
        ]
      });
      toast("小红书笔记已成功暂存为草稿！", "success");
      await loadRuns();
      updateToutiaoArticleSelect(title);
    },
    onError: (data) => {
      if (resultBox && !opts.btn) {
        resultBox.hidden = false;
        resultBox.className = "result error";
        resultBox.innerHTML = `<b>小红书推送失败</b><br>${esc(data.error)}`;
      }
      if (data.error && data.error.includes("未检测到运行中的 Chrome 调试端口")) {
        showChromeGuideModal();
      } else {
        toast(`小红书推送失败: ${data.error}`, "error");
      }
    }
  });
}

/* ---------- 邮件定时发送管理 ---------- */

let currentEmailContext = null;

function cleanScheduleText(sched) {
  if (!sched) return "";
  return sched.replace(/^定时：/, "").trim();
}

function formatShortSentAt(timeStr) {
  if (!timeStr) return "";
  const match = timeStr.match(/(\d{2}-\d{2})\s+(\d{2}:\d{2})/);
  if (match) return `${match[1]} ${match[2]}`;
  return timeStr;
}

function getNextScheduleDate() {
  let candidateTime = localStorage.getItem("last_schedule_date") || localStorage.getItem("last_email_schedule_time");

  if (!candidateTime && state.runs && state.runs.length > 0) {
    let latestSentAt = "";
    let latestSched = "";
    for (const r of state.runs) {
      const plats = r.platforms || {};
      for (const key of ["xhs", "toutiao"]) {
        const p = plats[key];
        if (p) {
          const sentAt = p.published_at || p.sent_at || "";
          if (sentAt >= latestSentAt) {
            latestSentAt = sentAt;
            latestSched = p.schedule_time || p.published_at || p.sent_at || "";
          }
        }
      }
    }
    if (latestSched) candidateTime = latestSched;
  }

  const pad = (n) => String(n).padStart(2, "0");
  const formatDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

  if (candidateTime) {
    const match = candidateTime.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
    if (match) {
      const y = parseInt(match[1], 10);
      const m = parseInt(match[2], 10) - 1;
      const d = parseInt(match[3], 10);
      const baseDate = new Date(y, m, d);
      if (!isNaN(baseDate.getTime())) {
        baseDate.setDate(baseDate.getDate() + 1);
        return formatDate(baseDate);
      }
    }
  }

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  return formatDate(tomorrow);
}

/* ---------- 定时自动发布（已停用） ---------- */

let currentPublishContext = null;

function initPublishHours() {
  const select = $("publish-schedule-hour");
  if (!select || select.children.length > 0) return;
  for (let i = 0; i < 24; i++) {
    const hh = String(i).padStart(2, "0") + ":00";
    const opt = document.createElement("option");
    opt.value = hh;
    opt.textContent = hh;
    if (hh === "08:00") opt.selected = true;
    select.appendChild(opt);
  }
}

function updatePublishSchedulePreview() {
  const date = $("publish-schedule-date")?.value || todayIso();
  const hour = $("publish-schedule-hour")?.value || "08:00";
  const previewEl = $("publish-schedule-preview");
  if (previewEl) {
    previewEl.textContent = "自动定时发布已停用，请下载素材后通过官方后台人工发布。";
  }
}

async function openSchedulePublishModal(context) {
  const isReady = await checkChromeStatus(false);
  if (!isReady) {
    showChromeGuideModal();
    return;
  }

  initPublishHours();
  currentPublishContext = context;
  const isXhs = context.platform === "xhs";

  $("publish-modal-title").textContent = isXhs ? "📕 小红书人工发布" : "📰 今日头条人工发布";
  $("publish-target-summary").innerHTML = `
    <b>发布平台</b>：<span style="color:${isXhs ? '#ff2442' : '#e22c38'}; font-weight:600;">${isXhs ? '小红书（人工发布）' : '今日头条（人工发布）'}</span><br>
    <b>目标文章</b>：<span>${esc(context.title || '未命名文章')}</span>
  `;

  const tipsEl = $("publish-platform-tips");
  if (tipsEl) {
    if (isXhs) {
      tipsEl.innerHTML = `
        💡 <b>小红书发布说明</b>：自动推送已停用，请下载生成的 Markdown 与 3:4 封面，人工审核后通过官方后台发布。
      `;
    } else {
      tipsEl.innerHTML = `
        💡 <b>今日头条发布说明</b>：自动推送已停用，请下载生成的 Markdown、DOCX 与 16:9 封面，人工审核后通过官方后台发布。
      `;
    }
  }

  const nextDateStr = getNextScheduleDate();
  $("publish-schedule-date").value = nextDateStr;
  $("publish-schedule-hour").value = "08:00";
  updatePublishSchedulePreview();

  $("schedule-publish-modal").hidden = false;
}

function closeSchedulePublishModal() {
  $("schedule-publish-modal").hidden = true;
  currentPublishContext = null;
}

async function confirmPublishScheduleAction() {
  toast("小红书和今日头条自动定时发布已停用，请下载素材后人工发布。", "info");
  return;
  if (!currentPublishContext) return;
  const date = $("publish-schedule-date").value;
  const hour = $("publish-schedule-hour").value;
  if (!date) { toast("请选择定时发布日期", "error"); return; }
  const scheduleTime = `${date} ${hour}`;
  const { platform, title, runDir } = currentPublishContext;
  const isXhs = platform === "xhs";
  const pushTaskKey = `${platform}_${runDir || title}`;

  state.pushingTasks.add(pushTaskKey);
  loadRuns();

  const btnConfirm = $("btn-confirm-publish");
  try {
    await withBusy(btnConfirm, `自动定时发布已停用（${scheduleTime}）…`, null, {
      request: () => {
        if (isXhs) {
          return api("/api/push-xhs", {
            method: "POST",
            body: {
              title,
              run_dir: runDir || "",
              schedule_time: scheduleTime,
              action: "publish"
            }
          });
        } else {
          return api("/api/push-toutiao", {
            method: "POST",
            body: {
              title,
              run_dir: runDir || "",
              schedule_time: scheduleTime
            }
          });
        }
      },
      onSuccess: async (data) => {
        state.pushingTasks.delete(pushTaskKey);
        closeSchedulePublishModal();
        localStorage.setItem("last_schedule_date", scheduleTime);
        toast(`${isXhs ? '小红书' : '今日头条'}文章已成功设置为定时发布（${scheduleTime}）！`, "success");
        await loadRuns();

        showModal({
          type: "success",
          title: `${isXhs ? '📕 小红书' : '📰 今日头条'} 定时发布成功！`,
          summaryHtml: `
            已成功通过 Playwright 接管本地 Chrome，将《<b>${esc(title)}</b>》设置定时发布并提交！<br><br>
            ⏰ <b>定时发布时间</b>：<code>${esc(scheduleTime)}</code><br>
            📍 <b>目标平台</b>：${isXhs ? '小红书创作者服务平台' : '今日头条创作者后台'}<br>
            👉 您现在可在本地 Chrome 后台查看已定时发布的记录。
          `,
          actions: [{ label: "我知道了", primary: true, onClick: () => {} }]
        });
      },
      onError: (data) => {
        state.pushingTasks.delete(pushTaskKey);
        loadRuns();
        if (data.error && data.error.includes("未检测到运行中的 Chrome 调试端口")) {
          showChromeGuideModal();
        } else {
          toast(`推送发布失败: ${data.error}`, "error");
        }
      }
    });
  } finally {
    state.pushingTasks.delete(pushTaskKey);
    loadRuns();
  }
}

function initEmailHours() {
  const select = $("email-schedule-hour");
  if (!select || select.children.length > 0) return;
  for (let i = 0; i < 24; i++) {
    const hh = String(i).padStart(2, "0") + ":00";
    const opt = document.createElement("option");
    opt.value = hh;
    opt.textContent = hh;
    if (hh === "08:00") opt.selected = true;
    select.appendChild(opt);
  }
}

function updateEmailSchedulePreview() {
  const date = $("email-schedule-date").value || todayIso();
  const hour = $("email-schedule-hour").value || "08:00";
  $("email-schedule-preview").textContent = `正文第一行将固定为：定时：${date} ${hour}`;
}

function openScheduleEmailModal(context) {
  initEmailHours();
  currentEmailContext = context;
  $("email-to-addr").value = "mengting9909@gmail.com";
  $("email-subject").value = context.title || "未命名文章";

  const nextDateStr = getNextScheduleDate();
  $("email-schedule-date").value = nextDateStr;
  $("email-schedule-hour").value = "08:00";
  updateEmailSchedulePreview();

  const listEl = $("email-attachments-list");
  const files = context.files || [];
  if (files.length > 0) {
    listEl.innerHTML = files.map((f) => {
      const name = f.split("/").pop();
      return `<div class="email-attachment-item">📎 <b>${esc(name)}</b></div>`;
    }).join("");
  } else {
    listEl.innerHTML = '<span class="hint">系统将自动从生成目录打包相关 .md / .docx / 封面图附件</span>';
  }

  $("schedule-email-modal").hidden = false;
}

function closeScheduleEmailModal() {
  $("schedule-email-modal").hidden = true;
  currentEmailContext = null;
}

async function sendScheduledEmailAction() {
  if (!currentEmailContext) return;
  const date = $("email-schedule-date").value;
  const hour = $("email-schedule-hour").value;
  if (!date) { toast("请选择定时发布日期", "error"); return; }
  const scheduleTime = `${date} ${hour}`;
  const title = $("email-subject").value.trim();

  await withBusy($("btn-confirm-send-email"), "正在发送邮件…", null, {
    request: () => api("/api/runs/send-email", {
      method: "POST",
      body: {
        to_addr: "mengting9909@gmail.com",
        title: title,
        schedule_time: scheduleTime,
        run_dir: currentEmailContext.runDir || "",
        platform: currentEmailContext.platform || "xhs",
        attachment_paths: currentEmailContext.files || []
      }
    }),
    onSuccess: async (data) => {
      closeScheduleEmailModal();
      localStorage.setItem("last_email_schedule_time", scheduleTime);
      localStorage.setItem("last_email_sent_at", new Date().toISOString());
      toast(`邮件已成功发送至 mengting9909@gmail.com（定时: ${scheduleTime}）`, "success");
      await loadRuns();
      showModal({
        type: "success",
        title: "📧 定时发布邮件已送达",
        summaryHtml: `已成功将《${esc(title)}》发送至 <b>mengting9909@gmail.com</b>。<br><br>
          <b>正文第一行</b>：<code>定时：${esc(scheduleTime)}</code><br>
          <b>已附带附件</b>：${(data.attached_files || []).map(f => `<code>${esc(f)}</code>`).join("、") || "无"}`
      });
    }
  });
}

async function loadRuns() {
  const box = $("run-history");
  const data = await api("/api/runs");
  if (!data.ok || !data.items.length) {
    state.runs = [];
    box.innerHTML = '<p class="empty">暂无历史记录。</p>';
    return;
  }
  state.runs = data.items;
  box.innerHTML = data.items.map((r) => {
    const plats = r.platforms || {};
    const xhs = plats.xhs || {};
    const jrtt = plats.toutiao || {};

    const xhsTaskKey = `xhs_${r.run_dir || r.title}`;
    const isXhsGenerating = state.generatingTasks.has(xhsTaskKey);

    const jrttTaskKey = `toutiao_${r.run_dir || r.title}`;
    const isJrttGenerating = state.generatingTasks.has(jrttTaskKey);

    // 1. 小红书素材分支：只生成和下载，不提供自动推送入口
    const hasXhsMaterial = Boolean(xhs.title || xhs.md_path || xhs.cover_path);
    let xhsHtml = '';
    if (isXhsGenerating) {
      xhsHtml = `
        <button class="btn small secondary" type="button" disabled style="opacity:0.8; cursor:wait; background:#fff1f2; color:#e11d48; border-color:#fecdd3; font-weight:600;">⏳ 小红书生成中…</button>
      `;
    } else {
      xhsHtml = `
        <span class="badge-status ${hasXhsMaterial ? 'ok' : 'pending'}" style="font-size:11px;">小红书: ${hasXhsMaterial ? '素材已生成' : '待生成'}</span>
        <button class="btn small ${hasXhsMaterial ? 'ghost' : 'secondary'} btn-generate-xhs" data-title="${esc(r.title || '')}" data-rundir="${esc(r.run_dir || '')}" type="button" title="根据母稿生成小红书文案及 3:4 封面">${hasXhsMaterial ? '重生素材' : '📕 生成素材'}</button>
        ${hasXhsMaterial ? `<span class="material-downloads">${materialDownloadLinks(xhs)}</span>` : ''}
      `;
    }

    // 2. 今日头条素材分支：只生成和下载，不提供自动推送入口
    const hasJrttMaterial = Boolean(jrtt.title || jrtt.md_path || jrtt.cover_path || jrtt.docx_path);
    let jrttHtml = '';
    if (isJrttGenerating) {
      jrttHtml = `
        <button class="btn small secondary" type="button" disabled style="opacity:0.8; cursor:wait; background:#fff1f2; color:#be123c; border-color:#fecdd3; font-weight:600;">⏳ 头条生成中…</button>
      `;
    } else {
      jrttHtml = `
        <span class="badge-status ${hasJrttMaterial ? 'ok' : 'pending'}" style="font-size:11px;">头条: ${hasJrttMaterial ? '素材已生成' : '待生成'}</span>
        <button class="btn small ${hasJrttMaterial ? 'ghost' : 'secondary'} btn-generate-jrtt" data-title="${esc(r.title || '')}" data-rundir="${esc(r.run_dir || '')}" type="button" title="根据母稿生成今日头条长图文、16:9 封面和 DOCX">${hasJrttMaterial ? '重生素材' : '📰 生成素材'}</button>
        ${hasJrttMaterial ? `<span class="material-downloads">${materialDownloadLinks(jrtt)}</span>` : ''}
      `;
    }

    const isImported = Boolean(r.imported);
    const platformLabel = r.platform_source === "x" ? "🐦 X推文导入" : (isImported ? "📥 外部导入" : "");
    const imgBadge = (r.images_count > 0) ? `<span class="badge" style="background:#e0f2fe; color:#0369a1; font-size:11px; padding:1px 6px; border-radius:4px; margin-left:4px;">🖼️ ${r.images_count} 张原图</span>` : '';
    const importBadge = platformLabel ? `<span class="badge" style="background:#0284c7; color:#fff; font-size:11px; padding:1px 6px; border-radius:4px; margin-left:6px;">${platformLabel}</span>` : '';
    const linkBtn = r.source_url ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener" style="font-size:11px; color:#2563eb; margin-left:6px; text-decoration:none;">查看原帖 ↗</a>` : '';

    return `
      <div class="queue-item history-item" data-title="${esc(r.title || '')}" data-rundir="${esc(r.run_dir || '')}" data-platforms='${esc(JSON.stringify(plats))}'>
        <div class="queue-info">
          <div style="display:flex; align-items:center; flex-wrap:wrap;">
            <span class="queue-title">${esc(r.title || "（无标题）")}</span>
            ${importBadge}
            ${imgBadge}
            ${linkBtn}
          </div>
          <div class="queue-meta-row">
            <span class="queue-meta">${esc(r.created_at || r.pushed_at || "")} · ${esc(r.author || r.status || "")}</span>
            ${(r.verification || {}).status === "pass" ? '<span class="queue-meta ok">✓ 回读校验通过</span>' : ''}
          </div>
        </div>
        <div class="queue-actions">
          <div class="platform-group" style="display:flex; align-items:center; gap:6px;">${xhsHtml}</div>
          <div class="platform-group" style="display:flex; align-items:center; gap:6px;">${jrttHtml}</div>
        </div>
      </div>`;
  }).join("");
  updateToutiaoArticleSelect();
}

/* ---------- 事件绑定 ---------- */

document.querySelectorAll(".step").forEach((node) => {
  node.addEventListener("click", () => setStep(Number(node.dataset.step)));
});

document.querySelectorAll("#status-chips .chip").forEach((node) => {
  node.addEventListener("click", () => {
    document.querySelectorAll("#status-chips .chip").forEach((c) => c.classList.remove("active"));
    node.classList.add("active");
    state.statusFilter = node.dataset.status;
    renderTopics();
  });
});
document.querySelectorAll("#direction-chips .chip").forEach((node) => {
  node.addEventListener("click", () => {
    document.querySelectorAll("#direction-chips .chip").forEach((c) => c.classList.remove("active"));
    node.classList.add("active");
    state.directionFilter = node.dataset.direction;
    renderTopics();
  });
});
document.querySelectorAll("#date-chips .chip").forEach((node) => {
  node.addEventListener("click", () => {
    document.querySelectorAll("#date-chips .chip").forEach((c) => c.classList.remove("active"));
    node.classList.add("active");
    state.dateFilter = node.dataset.date;
    renderTopics();
  });
});
$("topic-keyword").addEventListener("input", (event) => {
  state.keyword = event.target.value;
  renderTopics();
});
$("topic-sort").addEventListener("change", (event) => {
  state.sort = event.target.value;
  renderTopics();
});

$("topic-cards").addEventListener("click", (event) => {
  const card = event.target.closest(".card");
  if (!card) return;
  if (card.dataset.disabled === "true") return;
  if (event.target.classList.contains("card-check")) return;
  if (event.target.classList.contains("note-toggle")) {
    const note = card.querySelector(".card-note");
    if (note) {
      note.classList.toggle("expanded");
      event.target.textContent = note.classList.contains("expanded") ? "收起备注" : "查看备注";
    }
    return;
  }
  const id = card.dataset.recordId;
  const topic = state.topics.find((t) => t.record_id === id);
  if (topic && topic["状态"] === "已推送") return;

  if (state.checkedIds.has(id)) state.checkedIds.delete(id);
  else state.checkedIds.add(id);
  card.classList.toggle("checked", state.checkedIds.has(id));
  const check = card.querySelector(".card-check");
  if (check) check.checked = state.checkedIds.has(id);
  $("select-count").textContent = state.checkedIds.size ? `已勾选 ${state.checkedIds.size} 条` : "";
  updateBatchSelection();

  // 联动更新全选框状态
  const selectable = filteredTopics().filter((t) => t["状态"] !== "已推送");
  const allChecked = selectable.length > 0 && selectable.every((t) => state.checkedIds.has(t.record_id));
  const someChecked = !allChecked && selectable.some((t) => state.checkedIds.has(t.record_id));
  const thCheck = $("th-check-all");
  if (thCheck) {
    thCheck.checked = allChecked;
    thCheck.indeterminate = someChecked;
  }
});
$("topic-cards").addEventListener("change", (event) => {
  if (event.target.id === "th-check-all") {
    const checked = event.target.checked;
    const selectable = filteredTopics().filter((t) => t["状态"] !== "已推送");
    selectable.forEach((t) => {
      if (checked) state.checkedIds.add(t.record_id);
      else state.checkedIds.delete(t.record_id);
    });
    renderTopics();
    return;
  }

  if (!event.target.classList.contains("card-check")) return;
  const card = event.target.closest(".card");
  if (!card) return;
  const id = card.dataset.recordId;
  const topic = state.topics.find((t) => t.record_id === id);
  if (card.dataset.disabled === "true" || (topic && topic["状态"] === "已推送")) {
    event.target.checked = false;
    return;
  }
  if (event.target.checked) state.checkedIds.add(id);
  else state.checkedIds.delete(id);
  card.classList.toggle("checked", event.target.checked);
  $("select-count").textContent = state.checkedIds.size ? `已勾选 ${state.checkedIds.size} 条` : "";
  updateBatchSelection();

  // 联动更新全选框状态
  const selectable = filteredTopics().filter((t) => t["状态"] !== "已推送");
  const allChecked = selectable.length > 0 && selectable.every((t) => state.checkedIds.has(t.record_id));
  const someChecked = !allChecked && selectable.some((t) => state.checkedIds.has(t.record_id));
  const thCheck = $("th-check-all");
  if (thCheck) {
    thCheck.checked = allChecked;
    thCheck.indeterminate = someChecked;
  }
});

$("write-queue").addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-write]");
  if (!btn) return;
  state.recordId = btn.dataset.write;
  state.topic = state.topics.find((t) => t.record_id === state.recordId);
  $("draft-title").value = state.topic ? (state.topic["选题标题"] || "") : "";
  setStep(3);
});

$("gen-topic-select").addEventListener("change", (event) => {
  state.recordId = event.target.value;
  $("draft-title").value = "";
  $("draft-markdown").value = "";
  showTopicDetail();
});

$("btn-import-html").addEventListener("click", () => $("import-html-file").click());
$("import-html-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) importHtmlFile(file);
  event.target.value = "";
});

$("btn-fetch-topics").addEventListener("click", () => fetchTopics());
$("btn-scan-aihot").addEventListener("click", scanAihotTopics);
$("btn-scan-hot").addEventListener("click", scanHotTopics);
$("btn-scan-skills").addEventListener("click", scanSkillsTopics);
$("btn-toggle-import").addEventListener("click", () => {
  const box = $("topic-import-box");
  box.hidden = !box.hidden;
});
$("btn-import-topics").addEventListener("click", importTopics);
$("btn-mark-selected").addEventListener("click", markSelected);
$("btn-submit-batch").addEventListener("click", submitBatchPublish);
$("btn-refresh-batch").addEventListener("click", () => {
  withBusy($("btn-refresh-batch"), "读取中…", null, {
    request: async () => { await pollBatchPublish(state.batchJobId); return { ok: true }; },
  });
});
$("batch-job-status").addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-batch-retry]");
  if (btn) retryBatchPublish(state.batchJobId, btn);
});
$("btn-goto-queue").addEventListener("click", () => setStep(2));
$("btn-reload-queue").addEventListener("click", () => {
  withBusy($("btn-reload-queue"), "刷新中…", null, {
    request: () => refreshTopics(false),
    onSuccess: () => { renderGenQueue(); toast("写作队列已刷新", "success"); },
  });
});
$("btn-generate").addEventListener("click", generateDraft);
$("btn-compress-draft").addEventListener("click", compressDraftAction);
$("draft-markdown").addEventListener("input", updateDraftCapacity);
$("btn-save-draft").addEventListener("click", saveDraft);
$("btn-goto-preview").addEventListener("click", gotoPreview);
$("btn-preview").addEventListener("click", buildPreview);
$("pv-engine").addEventListener("change", updateEngineFields);
$("btn-push").addEventListener("click", pushDraft);
$("btn-publish-package")?.addEventListener("click", publishPackage);
$("btn-gen-xhs")?.addEventListener("click", generateXhsCopy);
$("btn-gen-jrtt")?.addEventListener("click", generateToutiaoCopy);
$("toutiao-article-select")?.addEventListener("change", (e) => {
  const item = toutiaoArticleList[Number(e.target.value)];
  renderToutiaoMaterialInfo(item);
  renderXhsMaterialInfo(item);
});
$("btn-gen-toutiao-material")?.addEventListener("click", regenerateSelectedToutiaoMaterial);
$("btn-gen-xhs-material")?.addEventListener("click", regenerateSelectedXhsMaterial);
$("btn-refresh-health").addEventListener("click", () => loadHealth());

$("run-history").addEventListener("click", async (event) => {
  const btnGenXhs = event.target.closest(".btn-generate-xhs, .btn-history-xhs");
  const btnGenJrtt = event.target.closest(".btn-generate-jrtt, .btn-history-jrtt");
  const btnEmail = event.target.closest(".btn-email-platform");

  if (!btnGenXhs && !btnGenJrtt && !btnEmail) return;
  const item = event.target.closest(".history-item");
  if (!item) return;

  const itemTitle = item.dataset.title || "";
  const runDir = item.dataset.rundir || "";
  let platforms = {};
  try { platforms = JSON.parse(item.dataset.platforms || "{}"); } catch (e) {}

  // 小红书文案生成
  if (btnGenXhs) {
    const title = btnGenXhs.dataset.title || itemTitle;
    await triggerGenerateXhs({ title, markdown: "", runDir, recordId: "", btn: btnGenXhs });
    await loadRuns();
    return;
  }

  // 今日头条文案生成
  if (btnGenJrtt) {
    const title = btnGenJrtt.dataset.title || itemTitle;
    await triggerGenerateToutiao({ title, markdown: "", runDir, recordId: "", btn: btnGenJrtt });
    await loadRuns();
    return;
  }

  // 邮件发送（逻辑保留，界面隐藏）
  if (btnEmail) {
    const platform = btnEmail.dataset.platform || "xhs";
    const platInfo = platforms[platform] || {};
    openScheduleEmailModal({
      runDir,
      platform,
      title: platInfo.title || itemTitle,
      files: platInfo.files || []
    });
    return;
  }
});

/* 外部推文/文章导入弹窗控制 */
function openImportLinkModal() {
  const modal = $("import-link-modal");
  if (!modal) return;
  const input = $("import-link-url");
  if (input) input.value = "";
  const progressBox = $("import-progress-box");
  if (progressBox) progressBox.style.display = "none";
  const btnConfirm = $("btn-confirm-import");
  if (btnConfirm) {
    btnConfirm.disabled = false;
    btnConfirm.textContent = "立即解析并导入原图";
  }
  modal.hidden = false;
  setTimeout(() => input?.focus(), 100);
}

function closeImportLinkModal() {
  const modal = $("import-link-modal");
  if (modal) modal.hidden = true;
}

async function confirmImportLinkAction() {
  const url = $("import-link-url")?.value?.trim();
  if (!url) {
    toast("请输入推文或文章链接", "error");
    return;
  }
  const btnConfirm = $("btn-confirm-import");
  const progressBox = $("import-progress-box");
  const progressText = $("import-progress-text");

  try {
    if (btnConfirm) {
      btnConfirm.disabled = true;
      btnConfirm.textContent = "正在拉取原图…";
    }
    if (progressBox) progressBox.style.display = "block";
    if (progressText) progressText.textContent = "正在解析推文文案并下载高清原图，请稍候…";

    const data = await api("/api/import-link", {
      method: "POST",
      body: { url },
    });

    if (!data.ok) {
      throw new Error(data.error || "导入失败");
    }

    closeImportLinkModal();
    toast(`🎉 成功导入推文！已下载 ${data.images_count} 张原图`, "success");
    await loadRuns();
    // 切换到第 5 步方便用户直接在矩阵面板生成并下载素材
    setStep(5);
  } catch (err) {
    toast(err.message || "导入发生错误", "error");
    if (progressBox) progressBox.style.display = "none";
    if (btnConfirm) {
      btnConfirm.disabled = false;
      btnConfirm.textContent = "立即解析并导入原图";
    }
  }
}

/* 外部推文导入弹窗事件绑定 */
$("btn-open-import-modal")?.addEventListener("click", openImportLinkModal);
$("btn-open-import-modal-alt")?.addEventListener("click", openImportLinkModal);
$("import-modal-close")?.addEventListener("click", closeImportLinkModal);
$("import-link-mask")?.addEventListener("click", closeImportLinkModal);
$("btn-cancel-import")?.addEventListener("click", closeImportLinkModal);
$("btn-confirm-import")?.addEventListener("click", confirmImportLinkAction);

/* 结果弹框关闭：右上 ×、点击遮罩、ESC */
$("result-modal-close").addEventListener("click", closeModal);
$("result-modal-mask").addEventListener("click", closeModal);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (!$("import-link-modal").hidden) closeImportLinkModal();
    else if (!$("result-modal").hidden) closeModal();
  }
});

["pv-title", "pv-author", "pv-hook", "pv-subtitle", "pv-theme"].forEach((id) => {
  $(id).addEventListener("input", () => {
    if (state.previewHtml) renderPushConfig();
  });
});

/* ---------- 启动 ---------- */

(async function init() {
  updateBatchSelection();
  try { state.batchJobId = localStorage.getItem("studio-batch-job-id") || ""; } catch (_) { /* 存储可选 */ }
  if (state.batchJobId) {
    state.batchActive = true;
    updateBatchSelection();
    pollBatchPublish(state.batchJobId);
  }
  await loadHealth(true);
  await refreshTopics();
  await loadStyleRefs();
  await loadTemplates();
  await loadBmmdStyles();
  updateEngineFields();
  renderGenQueue();
  loadRuns();
})();
