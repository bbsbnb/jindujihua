const state = { current: null, runs: [], workflowAction: null, deleteTarget: null };
const $ = (id) => document.getElementById(id);
const SIDEBAR_MIN = 180;
const SIDEBAR_MAX = 420;

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(body.error || body || "请求失败");
  return body;
}

function esc(value) {
  return String(value ?? "-").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function fmtDate(value) { return value ? String(value).slice(0, 10) : "-"; }
function fmtDays(value) { return value === null || value === undefined ? "-" : `${value > 0 ? "+" : ""}${value} 天`; }
function pill(text, tone = "neutral") { return `<span class="pill ${tone}">${esc(text)}</span>`; }
function emptyRow(columns, message = "暂无数据") { return `<tr><td colspan="${columns}" style="text-align:center;color:#647781;padding:24px">${esc(message)}</td></tr>`; }

function showToast(message, error = false) {
  const toast = $("toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.className = "toast", 3200);
}

function setLoading(active) { $("loading").hidden = !active; }

function setSidebarWidth(width, persist = false) {
  const value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, Math.round(width)));
  document.querySelector(".app-shell").style.setProperty("--sidebar-width", `${value}px`);
  $("sidebarResizer").setAttribute("aria-valuenow", String(value));
  if (persist) localStorage.setItem("scheduleSidebarWidth", String(value));
}

function initializeSidebarResize() {
  const savedWidth = Number(localStorage.getItem("scheduleSidebarWidth"));
  if (Number.isFinite(savedWidth) && savedWidth > 0) setSidebarWidth(savedWidth);
  const resizer = $("sidebarResizer");
  resizer.addEventListener("pointerdown", event => {
    if (event.button !== 0) return;
    resizer.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-sidebar");
  });
  resizer.addEventListener("pointermove", event => {
    if (resizer.hasPointerCapture(event.pointerId)) setSidebarWidth(event.clientX);
  });
  const stopResize = event => {
    if (!resizer.hasPointerCapture(event.pointerId)) return;
    resizer.releasePointerCapture(event.pointerId);
    document.body.classList.remove("resizing-sidebar");
    const width = parseFloat(getComputedStyle(document.querySelector(".app-shell")).getPropertyValue("--sidebar-width"));
    setSidebarWidth(width, true);
  };
  resizer.addEventListener("pointerup", stopResize);
  resizer.addEventListener("pointercancel", stopResize);
  resizer.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = parseFloat(getComputedStyle(document.querySelector(".app-shell")).getPropertyValue("--sidebar-width"));
    const width = event.key === "Home" ? SIDEBAR_MIN : event.key === "End" ? SIDEBAR_MAX : current + (event.key === "ArrowLeft" ? -10 : 10);
    setSidebarWidth(width, true);
  });
}

async function loadRuns() {
  try {
    const data = await api("/api/runs");
    state.runs = data.runs;
    renderRuns();
  } catch (error) { showToast(error.message, true); }
}

function renderRuns() {
  const latestByFile = new Map();
  state.runs.forEach(run => {
    const key = String(run.input_filename || run.project_id).trim().toLowerCase();
    if (!latestByFile.has(key)) latestByFile.set(key, run);
  });
  const plans = [...latestByFile.values()];
  $("runCount").textContent = `${plans.length} 份计划`;
  $("runList").innerHTML = plans.map(run => {
    const filename = run.input_filename || run.project_id;
    const displayName = filename.replace(/\.xlsx$/i, "");
    return `
      <div class="run-entry ${state.current?.run_id === run.run_id ? "active" : ""}">
        <button class="run-item" data-run="${esc(run.run_id)}" title="${esc(filename)}"><strong>${esc(displayName)}</strong></button>
        <button class="run-delete" data-delete-run="${esc(run.run_id)}" data-delete-name="${esc(filename)}" title="删除计划" aria-label="删除 ${esc(displayName)}">×</button>
      </div>`;
  }).join("") || `<div style="padding:10px;color:#8097a6;font-size:11px">暂无已分析计划</div>`;
  document.querySelectorAll("[data-run]").forEach(button => button.addEventListener("click", () => openRun(button.dataset.run)));
  document.querySelectorAll("[data-delete-run]").forEach(button => button.addEventListener("click", () => openDeletePlan(button.dataset.deleteRun, button.dataset.deleteName)));
}

function openDeletePlan(runId, filename) {
  state.deleteTarget = { runId, filename };
  $("deletePlanName").textContent = filename.replace(/\.xlsx$/i, "");
  $("deletePlanDialog").showModal();
}

async function deletePlan(event) {
  event.preventDefault();
  const target = state.deleteTarget;
  if (!target) return;
  $("deletePlanDialog").close();
  setLoading(true);
  try {
    const result = await api(`/api/runs/${target.runId}/archive`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ actor: "计划工程师" }) });
    if (state.current?.input_filename === target.filename) {
      state.current = null;
      $("resultView").hidden = true;
      $("emptyState").hidden = false;
    }
    state.deleteTarget = null;
    await loadRuns();
    showToast(`已删除计划，归档 ${result.archived_runs} 个历史版本`);
  } catch (error) { showToast(error.message, true); }
  finally { setLoading(false); }
}

async function openRun(runId) {
  setLoading(true);
  try {
    state.current = await api(`/api/runs/${runId}`);
    render();
    renderRuns();
  } catch (error) { showToast(error.message, true); }
  finally { setLoading(false); }
}

async function upload(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".xlsx")) return showToast("请选择 .xlsx 文件", true);
  if (!file.size) return showToast("所选文件为空，请重新选择有效的 Excel 工作簿", true);
  if (file.size > 25 * 1024 * 1024) return showToast("文件超过 25 MB，请精简后重新导入", true);
  setLoading(true);
  try {
    const signature = new Uint8Array(await file.slice(0, 8).arrayBuffer());
    const isZip = signature[0] === 0x50 && signature[1] === 0x4b;
    const ole = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
    const isOle = ole.every((value, index) => signature[index] === value);
    if (isOle) throw new Error("该文件实际是旧版 .xls 或受密码保护的 Office 文件。请取消密码保护，并另存为 .xlsx 后重新导入");
    if (!isZip) throw new Error("该文件不是有效的 .xlsx 工作簿，可能只是修改了文件后缀。请用 Excel/WPS 另存为 .xlsx 后重新导入");
    state.current = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "X-Filename": encodeURIComponent(file.name), "X-Actor": encodeURIComponent("计划工程师") }, body: file });
    await loadRuns();
    render();
    showToast("分析完成，交付物已生成");
  } catch (error) { showToast(error.message, true); }
  finally { setLoading(false); $("fileInput").value = ""; }
}

function render() {
  const data = state.current;
  if (!data) return;
  $("emptyState").hidden = true;
  $("resultView").hidden = false;
  $("projectId").textContent = `${data.project.project_id} · V${data.version}`;
  $("projectName").textContent = data.project.project_name;
  $("projectMeta").textContent = `数据日期 ${fmtDate(data.project.data_date)} · ${data.summary.import_mode || "标准模板"} · 规则 ${data.rule_version} · 输入 ${data.input_filename}`;
  $("stateBadge").hidden = data.state === "草案";
  $("stateBadge").textContent = data.state === "草案" ? "" : data.state;
  $("stateBadge").className = `state-badge ${data.state === "已批准" ? "approved" : data.state === "已驳回" ? "rejected" : ""}`;
  $("downloadExcel").href = data.downloads.excel;
  $("downloadPdf").href = data.downloads.pdf;
  $("issueCount").textContent = data.issues.length ? `(${data.issues.length})` : "";
  $("eligibilityBanner").className = `alert-banner${data.formal_eligible ? "" : " blocked"}`;
  if (data.formal_eligible) {
    $("eligibilityBanner").textContent = "数据校验通过，具备进入人工复核流程的条件。";
  } else if (data.requires_import_confirmation && data.unresolved_blocking_count === 0) {
    $("eligibilityBanner").textContent = `有 ${data.summary.blocking_count} 项自动识别内容待计划工程师确认；确认后可直接提交复核。`;
  } else {
    $("eligibilityBanner").textContent = `仍有 ${data.unresolved_blocking_count ?? data.summary.blocking_count} 项数据问题，请修订输入后建立新版本。`;
  }
  renderWorkflow();
  renderMetrics(data);
  renderMilestones(data.milestones);
  renderCriticalPath(data.summary.critical_path);
  renderAlerts(data.forecast);
  renderSchedule(data.schedule);
  renderGantt(data.schedule);
  renderConflicts(data.conflicts);
  renderVariance(data.forecast);
  renderScenarios(data.scenarios);
  renderIssues(data.issues);
  renderAudit(data.audit || []);
  renderTrace(data.traceability);
}

function renderWorkflow() {
  const data = state.current;
  const button = $("workflowButton");
  const reject = $("rejectButton");
  button.hidden = false;
  reject.hidden = true;
  const canConfirmImport = data.requires_import_confirmation && data.unresolved_blocking_count === 0;
  button.disabled = !data.formal_eligible && !canConfirmImport;
  if (data.state === "草案") { button.textContent = canConfirmImport ? "确认并提交复核" : "提交复核"; button.dataset.action = "review"; }
  else if (data.state === "已复核") { button.textContent = "批准基线"; button.dataset.action = "approve"; reject.hidden = false; }
  else if (data.state === "已驳回") { button.textContent = "重新提交"; button.dataset.action = "resubmit"; button.disabled = false; }
  else { button.hidden = true; reject.hidden = false; }
}

function renderMetrics(data) {
  const s = data.summary;
  const metrics = [
    ["合同竣工", fmtDate(s.contract_finish)], ["预测竣工", fmtDate(s.forecast_finish)],
    ["对合同偏差", fmtDays(s.variance_contract_days), (s.variance_contract_days || 0) > 0],
    ["任务总数", s.task_count], ["当前关键任务", s.critical_count], ["重点预警", s.alert_count, s.alert_count > 0]
  ];
  $("metricGrid").innerHTML = metrics.map(([label, value, danger]) => `<div class="metric ${danger ? "danger" : ""}"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("");
}

function renderMilestones(rows) {
  $("milestoneBody").innerHTML = rows.map(row => `<tr class="${row.alert ? "row-alert" : ""}"><td>${esc(row.name)}</td><td>${fmtDate(row.contract_date)}</td><td>${fmtDate(row.current_date)}</td><td>${fmtDate(row.forecast_date)}</td><td>${fmtDays(row.variance_current_days)}</td><td>${pill(row.alert ? "延期风险" : "正常", row.alert ? "bad" : "ok")}</td></tr>`).join("") || emptyRow(6, "未配置里程碑");
}

function renderCriticalPath(path) {
  $("criticalPath").innerHTML = path?.length ? path.map((item, index) => `${index ? '<span class="path-arrow">→</span>' : ""}<span class="path-node">${esc(item)}</span>`).join("") : `<span style="color:#647781;font-size:12px">未识别关键线路</span>`;
}

function renderAlerts(rows) {
  const alerts = rows.filter(row => row.alert);
  $("alertLabel").textContent = `${alerts.length} 项`;
  $("alertBody").innerHTML = alerts.map(row => `<tr class="row-alert"><td>${esc(row.wbs)}</td><td>${esc(row.status)}</td><td>${row.planned_percent.toFixed(1)}</td><td>${row.actual_percent.toFixed(1)}</td><td>${row.variance_pp > 0 ? "+" : ""}${row.variance_pp.toFixed(1)}pp</td><td>${fmtDate(row.forecast_finish)}</td><td>${fmtDays(row.finish_variance_days)}</td><td>${esc(row.alert_reason)}</td></tr>`).join("") || emptyRow(8, "当前没有达到阈值的重点预警");
}

function renderSchedule(rows) {
  $("scheduleBody").innerHTML = rows.map(row => `<tr><td>${esc(row.wbs)}</td><td>${esc(row.name)}</td><td>${row.duration}</td><td>${fmtDate(row.early_start)}</td><td>${fmtDate(row.early_finish)}</td><td>${fmtDate(row.late_start)}</td><td>${fmtDate(row.late_finish)}</td><td>${row.total_float}</td><td>${row.free_float}</td><td>${pill(row.critical ? "是" : "否", row.critical ? "bad" : "neutral")}</td></tr>`).join("") || emptyRow(10);
}

function renderGantt(rows) {
  if (!rows.length) { $("gantt").innerHTML = "暂无数据"; return; }
  const dates = rows.flatMap(row => [new Date(row.early_start), new Date(row.early_finish)]);
  const start = new Date(Math.min(...dates));
  const finish = new Date(Math.max(...dates));
  const span = Math.max(1, (finish - start) / 86400000 + 1);
  const labels = Array.from({length: 6}, (_, i) => ({ pct: i * 20, date: new Date(start.getTime() + span * i / 5 * 86400000) }));
  const scale = `<div class="gantt-scale"><div class="gantt-name">任务 / 日期</div><div class="scale-track">${labels.map(item => `<span class="scale-label" style="left:${item.pct}%">${fmtDate(item.date.toISOString())}</span>`).join("")}</div></div>`;
  const body = rows.map(row => {
    const left = Math.max(0, (new Date(row.early_start) - start) / 86400000 / span * 100);
    const width = Math.max(1, ((new Date(row.early_finish) - new Date(row.early_start)) / 86400000 + 1) / span * 100);
    return `<div class="gantt-row"><div class="gantt-name" title="${esc(row.name)}">${esc(row.wbs)} ${esc(row.name)}</div><div class="gantt-track"><span class="gantt-bar ${row.critical ? "key" : ""}" style="left:${left}%;width:${width}%" title="${fmtDate(row.early_start)} 至 ${fmtDate(row.early_finish)}"></span></div></div>`;
  }).join("");
  $("gantt").innerHTML = scale + body;
}

function renderConflicts(rows) {
  $("conflictBody").innerHTML = rows.map(row => `<tr class="row-alert"><td>${esc(row.conflict_type)}</td><td>${esc(row.key)}</td><td>${fmtDate(row.conflict_date)}</td><td>${row.demand}</td><td>${row.capacity}</td><td>${esc(row.tasks.join(", "))}</td><td>${esc(row.message)}</td></tr>`).join("") || emptyRow(7, "未发现资源或工作面冲突");
}

function renderVariance(rows) {
  $("varianceBody").innerHTML = rows.map(row => `<tr class="${row.alert ? "row-alert" : ""}"><td>${esc(row.wbs)}</td><td>${esc(row.status)}</td><td>${row.planned_percent.toFixed(1)}</td><td>${row.actual_percent.toFixed(1)}</td><td>${row.variance_pp > 0 ? "+" : ""}${row.variance_pp.toFixed(1)}pp</td><td>${fmtDate(row.baseline_finish)}</td><td>${fmtDate(row.contract_baseline_finish)}</td><td>${fmtDate(row.forecast_finish)}</td><td>${fmtDays(row.finish_variance_days)}</td><td>${fmtDays(row.contract_variance_days)}</td><td>${row.remaining_duration}</td><td>${pill(row.current_critical ? "是" : "否", row.current_critical ? "bad" : "neutral")}</td><td>${esc(row.alert_reason || "-")}</td></tr>`).join("") || emptyRow(13);
}

function renderScenarios(rows) {
  $("scenarioList").innerHTML = rows.map(row => `<article class="scenario ${row.quantified ? "" : "unquantified"}"><div class="scenario-title"><h3>${esc(row.scenario_id)} · ${esc(row.action_type)} · ${esc(row.task_wbs)} ${esc(row.task_name)}</h3>${pill(row.quantified ? "已量化" : "待补参数", row.quantified ? "ok" : "warn")}</div><div class="scenario-grid"><div><span>调整前 / 后工期</span><strong>${row.before_duration} / ${row.after_duration ?? "-"} 工作日</strong></div><div><span>资源增量</span><strong>${row.resource_delta ?? "-"}</strong></div><div><span>预计追回</span><strong>${row.recovered_days === null ? "-" : row.recovered_days + " 工作日"}</strong></div><div><span>审批要求</span><strong>${esc(row.approval)}</strong></div></div><p class="scenario-notes">${esc(row.milestone_impact)}；${esc(row.resource_conflict)}；${esc(row.risk)}</p></article>`).join("") || `<div class="critical-path"><span style="color:#647781;font-size:12px">当前没有达到组合预警阈值的关键任务，无需生成纠偏情景。</span></div>`;
}

function renderIssues(rows) {
  $("issueBody").innerHTML = rows.map(row => `<tr class="${row.blocking ? "row-alert" : ""}"><td>${pill(row.severity, row.severity === "错误" ? "bad" : "warn")}</td><td>${esc(row.code)}</td><td>${esc(row.entity_id || "-")}</td><td>${esc(row.field_name || "-")}</td><td>${esc(row.message)}</td><td>${row.blocking ? pill("是", "bad") : pill("否")}</td></tr>`).join("") || emptyRow(6, "未发现数据质量问题");
}

function renderAudit(rows) {
  $("auditList").innerHTML = rows.map(row => `<div class="audit-item"><time>${esc(row.created_at.replace("T", " ").slice(0, 19))}</time><strong>${esc(row.from_state || "-")} → ${esc(row.to_state)}</strong><span>${esc(row.actor)} · ${esc(row.comment || row.action)}</span></div>`).join("") || `<div style="padding:16px;color:#647781;font-size:12px">当前页面尚未刷新审计记录</div>`;
}

function renderTrace(rows) {
  $("traceCount").textContent = `${rows.length} 条`;
  $("traceBody").innerHTML = rows.slice(0, 250).map(row => `<tr><td>${esc(row.output)}</td><td>${esc(row.entity_id)}</td><td>${esc(row.field)}</td><td>${esc(row.value)}</td><td>${esc(row.input_refs)}</td><td>${esc(row.rule)}</td><td>${esc(row.rule_version)}</td></tr>`).join("") || emptyRow(7);
}

function openWorkflow(action) {
  state.workflowAction = action;
  const titles = { review: "提交复核", approve: "批准基线", reject: "驳回", resubmit: "重新提交" };
  $("dialogTitle").textContent = titles[action];
  $("actorInput").value = action === "approve" || action === "reject" ? state.current.project.manager : state.current.project.preparer;
  $("commentInput").value = "";
  $("commentInput").placeholder = action === "reject" || action === "resubmit" ? "必须填写原因" : "可填写复核意见";
  const needsConfirmation = action === "review" && state.current.requires_import_confirmation;
  $("importConfirmation").hidden = !needsConfirmation;
  $("importConfirmationInput").checked = false;
  $("confirmWorkflow").textContent = needsConfirmation ? "确认并提交" : "确认";
  $("workflowDialog").showModal();
}

async function submitWorkflow(event) {
  event.preventDefault();
  const action = state.workflowAction;
  const actor = $("actorInput").value.trim();
  const comment = $("commentInput").value.trim();
  if (!actor) return showToast("请填写操作人", true);
  if (["reject", "resubmit"].includes(action) && !comment) return showToast("驳回或重提必须填写原因", true);
  const needsConfirmation = action === "review" && state.current.requires_import_confirmation;
  if (needsConfirmation && !$("importConfirmationInput").checked) return showToast("请先勾选确认自动识别和补充内容", true);
  $("workflowDialog").close();
  setLoading(true);
  try {
    if (needsConfirmation) {
      state.current = await api(`/api/runs/${state.current.run_id}/confirm-import`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ actor, acknowledged: true }) });
      if (!state.current.formal_eligible) {
        render();
        throw new Error(`确认已记录，但仍有 ${state.current.unresolved_blocking_count} 项数据问题需要处理`);
      }
    }
    state.current = await api(`/api/runs/${state.current.run_id}/transition`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ action, actor, comment }) });
    await loadRuns(); render(); showToast("流程状态已更新");
  } catch (error) { showToast(error.message, true); }
  finally { setLoading(false); }
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("chatText");
  const message = input.value.trim();
  if (!message) return;
  if (!state.current) return showToast("请先载入一次分析", true);
  addMessage(message, "user"); input.value = "";
  try {
    const result = await api(`/api/runs/${state.current.run_id}/chat`, { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({ message }) });
    addMessage(result.answer, "agent");
  } catch (error) { addMessage(error.message, "agent"); }
}

function addMessage(text, kind) {
  const node = document.createElement("div"); node.className = `message ${kind}`; node.textContent = text;
  $("chatMessages").appendChild(node); $("chatMessages").scrollTop = $("chatMessages").scrollHeight;
}

function setAssistantOpen(open) {
  const panel = $("assistantPanel");
  panel.classList.toggle("open", open);
  panel.setAttribute("aria-hidden", String(!open));
  $("assistantToggle").setAttribute("aria-expanded", String(open));
  if (open) setTimeout(() => $("chatText").focus(), 200);
}

document.querySelectorAll(".tab").forEach(button => button.addEventListener("click", () => {
  document.querySelectorAll(".tab, .tab-panel").forEach(node => node.classList.remove("active"));
  button.classList.add("active"); $(`tab-${button.dataset.tab}`).classList.add("active");
}));
$("uploadButton").addEventListener("click", () => $("fileInput").click());
$("emptyUpload").addEventListener("click", () => $("fileInput").click());
$("fileInput").addEventListener("change", event => upload(event.target.files[0]));
$("newProjectButton").addEventListener("click", () => $("newProjectDialog").showModal());
$("newProjectUpload").addEventListener("click", () => {
  $("newProjectDialog").close();
  $("fileInput").click();
});
$("refreshRuns").addEventListener("click", loadRuns);
$("workflowButton").addEventListener("click", event => openWorkflow(event.currentTarget.dataset.action));
$("rejectButton").addEventListener("click", () => openWorkflow("reject"));
$("workflowForm").addEventListener("submit", submitWorkflow);
$("deletePlanForm").addEventListener("submit", deletePlan);
$("chatForm").addEventListener("submit", sendChat);
$("clearChat").addEventListener("click", () => $("chatMessages").innerHTML = `<div class="message agent">对话已清空。你可以继续查询当前运行结果。</div>`);
$("assistantToggle").addEventListener("click", () => setAssistantOpen(true));
$("closeAssistant").addEventListener("click", () => setAssistantOpen(false));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && $("assistantPanel").classList.contains("open")) setAssistantOpen(false);
});
initializeSidebarResize();
loadRuns();
