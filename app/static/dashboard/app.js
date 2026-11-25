const state = { charts: [], pages: {}, collapsedTournaments: {} };

const view = document.getElementById("view");
const title = document.getElementById("page-title");
const subtitle = document.getElementById("page-subtitle");
const toast = document.getElementById("toast");
const themeToggle = document.getElementById("theme-toggle");
const themeLabel = themeToggle.querySelector("[data-theme-label]");

if (typeof window.Chart === "undefined") {
  window.Chart = class FallbackChart {
    constructor(canvas) {
      this.canvas = canvas;
      this.draw();
    }
    destroy() {
      const ctx = this.canvas.getContext("2d");
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
    draw() {
      const ctx = this.canvas.getContext("2d");
      const rect = this.canvas.getBoundingClientRect();
      const width = Math.max(320, Math.floor(rect.width || 640));
      const height = Math.max(180, Math.floor(rect.height || 260));
      this.canvas.width = width;
      this.canvas.height = height;
      ctx.strokeStyle = "#cbd5e1";
      ctx.beginPath();
      ctx.moveTo(40, 12);
      ctx.lineTo(40, height - 32);
      ctx.lineTo(width - 12, height - 32);
      ctx.stroke();
    }
  };
}

function syncIcons() {
  if (window.lucide) window.lucide.createIcons();
  // Keep dense data readable as labeled records on small screens.
  document.querySelectorAll("#view table:not(.signals-table)").forEach(table => {
    const headings = [...table.querySelectorAll("thead th")].map(th => th.textContent.trim());
    table.classList.toggle("mobile-records", headings.length >= 5);
    table.classList.toggle("mobile-simple-table", headings.length < 5);
    table.querySelectorAll("tbody tr").forEach(row => {
      [...row.cells].forEach((cell, index) => {
        cell.dataset.label = cell.colSpan > 1 ? "" : headings[index] || "操作";
      });
    });
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("dashboard-theme", theme);
  themeLabel.textContent = theme === "dark" ? "亮色" : "暗色";
  themeToggle.querySelector("[data-lucide]")?.setAttribute("data-lucide", theme === "dark" ? "sun" : "moon");
  syncIcons();
  if (window.Chart.defaults) {
    const color = cssVar("--muted");
    const borderColor = cssVar("--line");
    Chart.defaults.color = color;
    Chart.defaults.borderColor = borderColor;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.font.size = window.innerWidth <= 768 ? 10 : 12;
    state.charts.forEach(chart => {
      chart.options.color = color;
      Object.values(chart.options.scales || {}).forEach(scale => {
        if (scale.ticks) scale.ticks.color = color;
        if (scale.grid) scale.grid.color = borderColor;
        if (scale.pointLabels) scale.pointLabels.color = color;
        if (scale.angleLines) scale.angleLines.color = borderColor;
      });
      chart.update("none");
    });
  }
}

function initTheme() {
  const saved = localStorage.getItem("dashboard-theme");
  const preferred = window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  applyTheme(saved || preferred);
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 2800);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    const text = await res.text();
    try {
      const data = JSON.parse(text);
      message = data.detail || JSON.stringify(data);
    } catch {
      message = text;
    }
    throw new Error(message || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

function clearCharts() {
  state.charts.forEach((chart) => chart.destroy());
  state.charts = [];
}

function setHeader(nextTitle, nextSubtitle) {
  title.textContent = nextTitle;
  subtitle.textContent = nextSubtitle;
}

function setActive(route) {
  document.querySelectorAll(".nav a").forEach((a) => {
    const isActive = a.dataset.route === route;
    a.classList.toggle("active", isActive);
    if (isActive && window.innerWidth <= 980) {
      const nav = a.parentElement;
      const linkRect = a.getBoundingClientRect();
      const navRect = nav.getBoundingClientRect();
      if (linkRect.left < navRect.left || linkRect.right > navRect.right) {
        nav.scrollTo({
          left: nav.scrollLeft + linkRect.left - navRect.left - (nav.clientWidth - linkRect.width) / 2,
          behavior: "smooth",
        });
      }
    }
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmt(value, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return escapeHtml(value);
}

function pct(value) {
  const n = Number(value || 0);
  return `${(n * 100).toFixed(1)}%`;
}

function dateInputValue(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function time(value) {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return fmt(value);
  return d.toLocaleString("zh-CN", { hour12: false });
}

const signalPublicationFormatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hourCycle: "h23",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit",
});

function signalPublicationTime(value) {
  if (!value) return "-";
  const timestamp = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value) ? value + "Z" : value;
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "-" : signalPublicationFormatter.format(date);
}

function levelBadge(level, warning = null) {
  const value = String(level || "unknown");
  if (value === "S") {
    const badge = `<span class="level S" aria-label="S级信号"><svg class="level-crown" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m3 6 5 4 4-6 4 6 5-4-2 12H5L3 6Z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M6 21h12" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg><span>S</span></span>`;
    if (!warning) return badge;
    const hint = `库内上次比赛：${time(warning.last_match_date)}；与本场间隔约 ${warning.days} 天。仅为参赛记录提醒，不改变信号评级。`;
    return `<span class="signal-grade">${badge}<span class="inactivity-warning" role="note" title="${escapeHtml(hint)}" aria-label="${escapeHtml(hint)}">3周无记录</span></span>`;
  }
  return `<span class="level ${escapeHtml(value)}">${escapeHtml(value === "Risk" ? "风险" : value)}</span>`;
}

function formalSignalBadge(signal) {
  return `<span class="signal-grade">${levelBadge(signal.signal_level, signal.inactivity_warning)}<small class="signal-lock">${signal.is_official_s ? "已确认 · 锁定" : "旧版记录"}</small></span>`;
}

function tournamentLevelBadge(level) {
  const key = String(level || "").toLowerCase().replace(/[\s_-]/g, "");
  const tiers = {
    super1000: ["Super 1000", "1000"], super750: ["Super 750", "750"],
    super500: ["Super 500", "500"], super300: ["Super 300", "300"],
    super100: ["Super 100", "100"], worldtourfinals: ["世界巡回赛总决赛", "1000"],
  };
  const [label, tier] = tiers[key] || [level || "未标注", "unknown"];
  return `<span class="tournament-tier tier-${tier}">${escapeHtml(String(label))}</span>`;
}

function tournamentNameCell(tournament, stage = null, round = null) {
  const fullName = String(tournament || "").trim();
  if (!fullName) return '<span class="signal-tournament-name">-</span>';
  const shortName = fullName
    .split(",")[0]
    .replace(/\s+(?:Women|Men|Mixed)\s+(?:Singles|Doubles)\b.*$/i, "")
    .replace(/\s+\d{4}$/, "")
    .trim() || fullName;
  const stages = { qualification: "资格赛", main_draw: "正赛" };
  const rounds = { qualification_round_1: "首轮", qualification_round_2: "次轮",
    qualification_final: "决轮", round_of_128: "128强", round_of_64: "64强",
    round_of_32: "32强", round_of_16: "16强", quarterfinal: "八强",
    semifinal: "半决赛", final: "决赛", round_robin: "小组赛" };
  const stageLabel = stages[stage];
  const note = stageLabel ? `<small class="signal-competition-stage">${stageLabel}${rounds[round] ? "·" + rounds[round] : ""}</small>` : "";
  return `<span class="signal-tournament-name" title="${escapeHtml(fullName)}">${escapeHtml(shortName)}</span>${note}`;
}

function statusBadge(status) {
  const value = String(status || "unknown");
  const labels = {
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    not_synced: "未同步",
    not_started: "未开始",
    scheduled: "未开始",
    closed: "已关闭",
    ended: "已结束",
    live: "进行中",
    suspended: "暂停",
    interrupted: "中断",
    delayed: "延迟",
    retired: "退赛",
    walkover: "退赛",
    cancelled: "取消",
    abandoned: "终止",
  };
  return `<span class="status-badge ${escapeHtml(value)}">${labels[value] || escapeHtml(value)}</span>`;
}

function roundLabel(value) {
  return ({qualification_round_1: "资格赛首轮", qualification_round_2: "资格赛次轮",
    qualification_final: "资格赛决轮", round_of_128: "128强", round_of_64: "64强",
    round_of_32: "32强", round_of_16: "16强", quarterfinal: "八强",
    semifinal: "半决赛", final: "决赛", round_robin: "小组赛"})[value] || fmt(value);
}

function yesNo(value) {
  return value ? "开启" : "关闭";
}

function zhStatus(value) {
  return ({
    live: "进行中",
    ended: "已结束",
    completed: "已完成",
    closed: "已关闭",
    scheduled: "未开始",
    not_started: "未开始",
    suspended: "暂停",
    interrupted: "中断",
    delayed: "延迟",
    cancelled: "取消",
    retired: "退赛",
    walkover: "退赛",
    abandoned: "终止",
  }[value] || fmt(value));
}

function zhSurface(value) {
  return ({ indoor: "室内", all: "全部", "15-point": "15 分制", "21-point": "21 分制" }[value] || fmt(value));
}

function zhResult(value) {
  if (value === null || value === undefined || value === "") return "-";
  const labels = {
    success: "命中",
    failed: "未命中",
    won: "胜",
    lost: "负",
    won_match: "命中",
    lost_match: "未命中",
    unknown: "待确认",
    result_pending: "赛果待补",
    retired: "退赛",
    walkover: "退赛",
    abandoned: "终止",
    interrupted: "中断",
  };
  let cls = value;
  if (value === "won_match") cls = "success";
  if (value === "lost_match") cls = "failed";
  const label = labels[value] || value;
  return `<span class="result-badge ${escapeHtml(cls)}">${escapeHtml(label)}</span>`;
}

function signalTarget(signal) {
  const recommendation = signal?.reason_json?.recommendation || {};
  const evaluation = signal?.reason_json?.evaluation || {};
  return recommendation.label || evaluation.target_label_name || "整场比赛";
}

function playerName(player) {
  return player ? fmt(player.name) : "-";
}

function playerLink(player, className = "player-link") {
  if (!player?.id) return playerName(player);
  return `<a class="${escapeHtml(className)}" href="#player/${encodeURIComponent(player.id)}">${playerName(player)}</a>`;
}

function matchName(match) {
  const players = matchPlayers(match);
  if (players.length < 2) return match ? `比赛 ${match.id}` : "-";
  return `${playerName(players[0])} vs ${playerName(players[1])}`;
}

function matchNameLinks(match) {
  const players = matchPlayers(match);
  if (players.length < 2) return match ? `比赛 ${match.id}` : "-";
  return `${playerLink(players[0])} vs ${playerLink(players[1])}`;
}

function metric(label, value, hint = "") {
  return `
    <div class="metric">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${value}</div>
      ${hint ? `<div class="metric-hint">${escapeHtml(hint)}</div>` : ""}
    </div>`;
}

function empty(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function panel(titleText, body, extraClass = "") {
  return `<section class="panel ${extraClass}"><h2 class="panel-title">${escapeHtml(titleText)}</h2>${body}</section>`;
}

function getPage(key) {
  return state.pages[key] || 1;
}

function setPage(key, page) {
  state.pages[key] = Math.max(1, Number(page) || 1);
}

function resetPage(key) {
  state.pages[key] = 1;
}

function clientPage(rows, key, perPage = 20) {
  const total = (rows || []).length;
  const totalPages = Math.max(Math.ceil(total / perPage), 1);
  const page = Math.min(getPage(key), totalPages);
  state.pages[key] = page;
  const offset = (page - 1) * perPage;
  return {
    rows: (rows || []).slice(offset, offset + perPage),
    pagination: { page, per_page: perPage, total, total_pages: totalPages, offset },
  };
}

function paginationControls(pagination, key) {
  if (!pagination || pagination.total <= pagination.per_page) return "";
  const page = Number(pagination.page || 1);
  const totalPages = Number(pagination.total_pages || 1);
  const total = Number(pagination.total || 0);
  const start = total === 0 ? 0 : Number(pagination.offset || 0) + 1;
  const end = Math.min(Number(pagination.offset || 0) + Number(pagination.per_page || 0), total);
  const pages = Array.from(new Set([1, page - 1, page, page + 1, totalPages]))
    .filter((item) => item >= 1 && item <= totalPages)
    .sort((a, b) => a - b);
  return `
    <div class="pagination" data-page-key="${escapeHtml(key)}">
      <div class="pagination-summary">第 ${start}-${end} 条 / 共 ${total} 条</div>
      <div class="pagination-actions">
        <button class="pagination-btn" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""} aria-label="上一页">
          <i data-lucide="chevron-left"></i>
        </button>
        ${pages.map((item) => `<button class="pagination-btn ${item === page ? "active" : ""}" data-page="${item}">${item}</button>`).join("")}
        <button class="pagination-btn" data-page="${page + 1}" ${page >= totalPages ? "disabled" : ""} aria-label="下一页">
          <i data-lucide="chevron-right"></i>
        </button>
      </div>
    </div>`;
}

function bindPagination(root, onChange) {
  root.querySelectorAll(".pagination [data-page]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.closest(".pagination")?.dataset.pageKey;
      if (!key) return;
      setPage(key, btn.dataset.page);
      await onChange(key);
      syncIcons();
    });
  });
}

function renderJson(data) {
  return `<pre class="json-box">${escapeHtml(JSON.stringify(data, null, 2))}</pre>`;
}

function lineChart(canvasId, labels, datasets, max = 1) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      scales: {
        y: { min: 0, max, ticks: { callback: (v) => (max === 1 ? `${Math.round(v * 100)}%` : v) } },
        x: { ticks: { maxTicksLimit: 10 } },
      },
      plugins: { legend: { position: "bottom" } },
    },
  });
  state.charts.push(chart);
}

function barChart(canvasId, labels, values, color = cssVar("--accent-2") || "#2563eb") {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const chart = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label: "数量", data: values, backgroundColor: color }] },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      plugins: { legend: { display: false } },
    },
  });
  state.charts.push(chart);
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function asNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function pctValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  return pct(value);
}

function playerPlainName(player) {
  return player?.name || "-";
}

function liveMatchScore(match) {
  const score = match.latest_score;
  const bucket = matchStatusBucket(match.status);
  if (["completed", "cancelled"].includes(bucket) && match.final_score) {
    return `比分：${fmt(match.final_score)}（按对阵顺序）`;
  }
  if (score) {
    const pair = values => values.map(v => fmt(v)).join("–");
    return `第${fmt(score.game_no)}局${score.set_ended ? "结束" : ""} · 局分 ${pair(score.games)} · 小分 ${pair(score.points)}（按对阵顺序）`;
  }
  if (match.final_score) return `比分：${fmt(match.final_score)}（按对阵顺序）`;
  return bucket === "scheduled" ? "比分：尚未开始" : "比分：等待更新";
}

function matchPlayers(match) {
  const order = { home: 0, away: 1 };
  return [...(match?.players || [])]
    .sort((a, b) => (order[a.side] ?? 2) - (order[b.side] ?? 2))
    .map((mp) => mp.player).filter(Boolean);
}

function tournamentKey(match) {
  return String(match?.tournament_id || `${match?.tournament || "unknown"}-${match?.surface || "unknown"}`);
}

function matchStatusBucket(status) {
  if (["live", "suspended", "interrupted", "delayed"].includes(status)) return "active";
  if (["ended", "completed", "closed"].includes(status)) return "completed";
  if (["cancelled", "abandoned", "retired", "walkover"].includes(status)) return "cancelled";
  return "scheduled";
}

function statusCount(statusCounts, statuses) {
  return (statuses || []).reduce((total, status) => total + Number(statusCounts?.[status] || 0), 0);
}

function groupMatchesByTournament(matches) {
  const groups = new Map();
  (matches || []).forEach((match) => {
    const key = tournamentKey(match);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        tournament: match.tournament || "未知赛事",
        surface: match.surface,
        tournament_level: match.tournament_level,
        matches: [],
        status_counts: {},
      });
    }
    const group = groups.get(key);
    group.tournament_level ||= match.tournament_level;
    group.matches.push(match);
    group.status_counts[match.status || "unknown"] = (group.status_counts[match.status || "unknown"] || 0) + 1;
  });
  return Array.from(groups.values()).sort((a, b) => {
    const activeDiff = Number(b.matches.some((m) => matchStatusBucket(m.status) === "active")) - Number(a.matches.some((m) => matchStatusBucket(m.status) === "active"));
    if (activeDiff) return activeDiff;
    return a.tournament.localeCompare(b.tournament);
  });
}

function latestSnapshotFor(latestSnapshots, playerId) {
  return (latestSnapshots || []).find((item) => item.player?.id === playerId)?.snapshot || null;
}

function statFor(stats, playerId) {
  return (stats || []).find((item) => item.player?.id === playerId) || null;
}

function uniquePointLabels(seriesByPlayer) {
  const labels = new Set();
  (seriesByPlayer || []).forEach((group) => {
    (group.points || []).forEach((point) => labels.add(point.snapshot_point_index));
  });
  return Array.from(labels).sort((a, b) => Number(a) - Number(b));
}

function multiPlayerMetricChart(canvasId, seriesByPlayer, metrics, max = 1) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const labels = uniquePointLabels(seriesByPlayer);
  const palette = ["#2563eb", "#dc2626", "#16a34a", "#f97316", "#7c3aed", "#0891b2", "#be123c", "#4d7c0f"];
  const pointStyles = ["circle", "rectRot", "triangle", "rect", "star", "crossRot"];
  const datasets = [];
  (seriesByPlayer || []).forEach((group, playerIndex) => {
    const byPoint = new Map((group.points || []).map((point) => [point.snapshot_point_index, point]));
    metrics.forEach((metricDef, metricIndex) => {
      const datasetIndex = playerIndex * metrics.length + metricIndex;
      const color = palette[datasetIndex % palette.length];
      const playerDash = playerIndex % 2 === 1 ? [3, 3] : undefined;
      datasets.push({
        label: `${playerPlainName(group.player)} · ${metricDef.label}`,
        data: labels.map((label) => byPoint.get(label)?.[metricDef.key] ?? null),
        borderColor: color,
        backgroundColor: color,
        borderDash: metricDef.dashed ? [8, 4] : playerDash,
        borderWidth: 2.4,
        tension: 0.24,
        spanGaps: true,
        pointRadius: 2.8,
        pointHoverRadius: 5,
        pointStyle: pointStyles[metricIndex % pointStyles.length],
      });
    });
  });
  const chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: "index" },
      scales: {
        y: { min: 0, max, ticks: { callback: (v) => (max === 1 ? `${Math.round(v * 100)}%` : v) } },
        x: { title: { display: true, text: "Point Index" }, ticks: { maxTicksLimit: 12 } },
      },
      plugins: {
        legend: {
          position: "bottom",
          labels: { usePointStyle: true, boxWidth: 10, boxHeight: 10 },
        },
      },
      elements: {
        line: { capBezierPoints: true },
      },
    },
  });
  state.charts.push(chart);
}

function radarChart(canvasId, labels, datasets) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const chart = new Chart(ctx, {
    type: "radar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0,
          max: 1,
          ticks: { callback: (v) => `${Math.round(v * 100)}%`, backdropColor: "transparent" },
          grid: { color: cssVar("--line") },
          angleLines: { color: cssVar("--line") },
        },
      },
      plugins: { legend: { position: "bottom" } },
    },
  });
  state.charts.push(chart);
}

function groupedBarChart(canvasId, labels, datasets) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const chart = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: { y: { beginAtZero: true } },
      plugins: { legend: { position: "bottom" } },
    },
  });
  state.charts.push(chart);
}

function playerScoreRow(player, latestSnapshots, stats) {
  const snap = latestSnapshotFor(latestSnapshots, player.id);
  const score = snap?.score_before;
  const stat = statFor(stats, player.id);
  const serveRate = stat?.service_point_win_rate ?? snap?.service_point_win_rate;
  const returnRate = stat?.return_point_win_rate ?? snap?.return_point_win_rate;
  const totalRate = stat?.total_point_win_rate ?? snap?.total_point_win_rate;
  const role = snap?.serve_known ? (snap.is_server ? "该分发球" : "该分接发") : "发球方待核验";
  return `
    <div class="score-player">
      <div class="score-player-main">
        <div>
          <div class="player-name">${playerLink(player)}</div>
          <div class="player-meta">${escapeHtml(role)} · ${score ? `最近记录分前：第${fmt(score.game_no)}局 · 小分 ${score.points.map(v => fmt(v)).join("–")}（本球员在前）` : "暂无逐分比分"}</div>
        </div>
        <div class="scoreline">${score ? `局分 ${score.games.map(v => fmt(v)).join("–")}` : "-"}</div>
      </div>
      <div class="mini-metrics">
        <span>总得分 ${pctValue(totalRate)}</span>
        <span>发球 ${pctValue(serveRate)}</span>
        <span>接发 ${pctValue(returnRate)}</span>
      </div>
    </div>`;
}

function matchDashboardHero(match, latestSnapshots, stats, summary) {
  const players = matchPlayers(match);
  return `
    <section class="match-hero">
      <div class="match-hero-head">
        <div>
          <div class="eyebrow">${tournamentLevelBadge(match.tournament_level)} ${fmt(match.tournament)} · ${zhSurface(match.surface)} · ${roundLabel(match.round)}</div>
          <h2>${matchNameLinks(match)}</h2>
        </div>
        <div class="hero-status">${zhStatus(match.status)}</div>
      </div>
      <div class="scoreboard-grid">
        ${players.map((player) => playerScoreRow(player, latestSnapshots, stats)).join("") || empty("暂无球员信息。")}
      </div>
      <div class="hero-facts">
        <span>最终比分：${fmt(match.final_score)}</span>
        <span>已记录分点：${fmt(summary?.total_points, "0")}</span>
        <span>趋势快照：${fmt(summary?.snapshots_count, "0")}</span>
        <span>最近更新：${time(summary?.last_updated_at)}</span>
      </div>
    </section>`;
}

function statComparisonTable(match, latestSnapshots, stats) {
  const players = matchPlayers(match);
  if (!players.length) return empty("暂无球员统计。");
  const rows = [
    ["总得分率", (stat, snap) => pctValue(stat?.total_point_win_rate ?? snap?.total_point_win_rate)],
    ["发球得分率", (stat, snap) => pctValue(stat?.service_point_win_rate ?? snap?.service_point_win_rate)],
    ["接发得分率", (stat, snap) => pctValue(stat?.return_point_win_rate ?? snap?.return_point_win_rate)],
  ];
  return `
    <div class="table-panel compact-table">
      <table>
        <thead><tr><th>指标</th>${players.map((player) => `<th>${playerLink(player)}</th>`).join("")}</tr></thead>
        <tbody>
          ${rows.map(([label, getter]) => `
            <tr>
              <td>${escapeHtml(label)}</td>
              ${players.map((player) => `<td>${getter(statFor(stats, player.id), latestSnapshotFor(latestSnapshots, player.id))}</td>`).join("")}
            </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

function recentPointsList(points) {
  if (!points?.length) return empty("暂无逐分记录。");
  return `
    <div class="timeline-list">
      ${points.slice().reverse().map((point) => {
        const tags = [
          point.is_set_point ? "局点" : "",
          point.is_match_point ? "赛点" : "",
        ].filter(Boolean);
        return `
          <div class="point-row">
            <div class="point-index">#${fmt(point.point_index)}</div>
            <div>
              <div class="point-title">第${fmt(point.game_no)}局 · ${playerLink(point.winner)} 得分</div>
              <div class="point-meta">${point.server ? `发球 ${playerLink(point.server)} · ` : ""}分前 ${fmt(point.home_score_before)}-${fmt(point.away_score_before)}（对阵顺序）</div>
            </div>
            <div class="point-tags">${tags.map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}</div>
          </div>`;
      }).join("")}
    </div>`;
}

function gameTimeline(games) {
  if (!games?.length) return empty("暂无局走势。");
  return `
    <div class="game-timeline" aria-label="局走势">
      ${games.map((game) => `
        <div class="game-node ${game.was_break ? "break" : ""} ${game.had_set_point || game.had_match_point ? "key" : ""}">
          <div class="game-dot"></div>
          <div class="game-title">第${fmt(game.game_no)}局</div>
          <div class="game-meta">${game.winner ? `本局胜者 · ${playerLink(game.winner)}` : "进行中"} · ${fmt(game.score)}</div>
        </div>`).join("")}
    </div>`;
}

function signalContextPanel(signal, snapshot) {
  return panel("信号上下文", `
    <div class="signal-context">
      <div class="grid cols-4">
        ${metric("等级", formalSignalBadge(signal))}
        ${metric("置信度", pct(signal.trend_confidence_score))}
        ${metric("动量", pct(signal.momentum_score))}
        ${metric("风险", pct(signal.collapse_risk_score))}
      </div>
      <div class="context-grid">
        <div class="kv"><span>方向</span><strong>${playerLink(signal.player)}</strong></div>
        <div class="kv"><span>触发比分</span><span>${snapshot ? fmt(snapshot.current_score_text) : "-"}</span></div>
        <div class="kv"><span>触发分点</span><span>${snapshot ? fmt(snapshot.snapshot_point_index) : "-"}</span></div>
        <div class="kv"><span>模型概率</span><span>${pctValue(signal.model_probability)}</span></div>
      </div>
    </div>`);
}

function drawMatchDashboardCharts(data) {
  const players = matchPlayers(data.match);
  multiPlayerMetricChart("match-power-chart", data.series_by_player || [], [
    { key: "trend_confidence_score", label: "胜势置信" },
    { key: "momentum_score", label: "短线动量" },
    { key: "collapse_risk_score", label: "崩盘风险", dashed: true },
  ]);
  multiPlayerMetricChart("match-rate-chart", data.series_by_player || [], [
    { key: "total_point_win_rate", label: "总得分" },
    { key: "service_point_win_rate", label: "发球" },
    { key: "return_point_win_rate", label: "接发" },
  ]);

  const radarLabels = ["总得分", "发球", "接发"];
  const radarColors = [cssVar("--accent-2") || "#2563eb", cssVar("--accent") || "#0f766e"];
  radarChart("match-radar-chart", radarLabels, players.map((player, index) => {
    const snap = latestSnapshotFor(data.latest_snapshots, player.id) || {};
    const color = radarColors[index % radarColors.length];
    return {
      label: playerPlainName(player),
      data: [
        snap.total_point_win_rate,
        snap.service_point_win_rate,
        snap.return_point_win_rate,
      ].map((value) => asNumber(value)),
      borderColor: color,
      backgroundColor: `${color}33`,
      pointBackgroundColor: color,
    };
  }));

  groupedBarChart("match-count-chart", ["得分", "发球得分", "接发得分"], players.map((player, index) => {
    const stat = statFor(data.stats, player.id) || {};
    const color = radarColors[index % radarColors.length];
    return {
      label: playerPlainName(player),
      data: [asNumber(stat.total_points_won), asNumber(stat.service_points_won), asNumber(stat.return_points_won)],
      backgroundColor: `${color}cc`,
    };
  }));
}

function matchDashboard(data, options = {}) {
  const signal = options.signal;
  return `
    ${signal ? signalContextPanel(signal, data.snapshot) : ""}
    ${signal ? panel("相似局势历史对照", '<div id="signal-history-comparison">正在查找相似历史局势…</div>') : ""}
    ${matchDashboardHero(data.match, data.latest_snapshots, data.stats, data.summary)}
    <div class="grid cols-2">
      ${panel("胜势坐标轴", `<div class="chart-box tall"><canvas id="match-power-chart"></canvas></div>`)}
      ${panel("得分率坐标轴", `<div class="chart-box tall"><canvas id="match-rate-chart"></canvas></div>`)}
    </div>
    <div class="grid cols-2">
      ${panel("当前能力雷达", `<div class="chart-box"><canvas id="match-radar-chart"></canvas></div>`)}
      ${panel("技术统计对比", `<div class="chart-box"><canvas id="match-count-chart"></canvas></div>`)}
    </div>
    <div class="grid cols-2">
      ${panel("双方详细统计", statComparisonTable(data.match, data.latest_snapshots, data.stats), "no-pad-panel")}
      ${panel("最近逐分", recentPointsList(data.recent_points), "no-pad-panel")}
    </div>
    ${panel("局势时间轴", gameTimeline(data.game_timeline), "no-pad-panel")}
    <div><h2 class="section-title">信号记录</h2>${signalsTable(data.signals, false, { pageKey: `match-${data.match.id}-signals`, pageSize: 20 })}</div>`;
}

function signalCards(rows, includeMatch) {
  return `<div class="signal-cards">${rows.map(s => `
    <article class="signal-card" aria-label="${escapeHtml(`信号 ${s.id}`)}">
      <div class="signal-card-top"><div class="signal-card-heading"><span class="signal-card-grade" title="${s.is_official_s ? '已确认 · 锁定' : '旧版记录'}">${levelBadge(s.signal_level, s.inactivity_warning)}</span>${playerLink(s.player)}</div>${statusBadge(s.match_status)}</div>
      ${includeMatch ? `<div class="signal-card-event">${tournamentLevelBadge(s.tournament_level)}<div>${tournamentNameCell(s.tournament, s.competition_stage, s.round)}</div></div>` : ""}
      <div class="signal-card-context">
        ${includeMatch ? `<a href="#match/${s.match_id}">比赛 #${fmt(s.match_id)}</a>` : ""}
        ${s.trigger ? `<span>触发点 ${fmt(s.trigger.published_point)}</span>` : ""}
      </div>
      <dl class="signal-card-metrics">
        <div><dt>趋势</dt><dd>${pct(s.trend_confidence_score)}</dd></div>
        <div><dt>风险</dt><dd>${pct(s.collapse_risk_score)}</dd></div>
        <div><dt>结算</dt><dd>${zhResult(s.direction_match_result)}</dd></div>
      </dl>
      ${s.current_notice ? `<details class="signal-card-notice"><summary>补充说明</summary><p>${escapeHtml(s.current_notice)}</p></details>` : ""}
      <footer class="signal-card-footer"><span>北京时间 ${signalPublicationTime(s.created_at)}</span><a href="#signal/${s.id}" aria-label="查看${escapeHtml(s.player?.name || '')}的信号详情">详情 <span aria-hidden="true">›</span></a></footer>
    </article>`).join("")}</div>`;
}

function signalsTable(signals, includeMatch = false, options = {}) {
  const pageKey = options.pageKey;
  const pageData = pageKey && !options.pagination ? clientPage(signals || [], pageKey, options.pageSize || 20) : null;
  const rows = pageData ? pageData.rows : (signals || []);
  const pagination = options.pagination || pageData?.pagination;
  if (!rows.length) return empty("暂无信号。");
  return `
    ${signalCards(rows, includeMatch)}
    <div class="table-panel signals-table-panel">
      <table class="signals-table">
        <thead>
          <tr><th>发布时间（北京时间）</th><th>信号</th>${includeMatch ? "<th>比赛</th><th>赛事级别</th><th>巡回赛</th>" : ""}<th>比赛状态</th><th>固定方向</th><th>触发趋势</th><th>触发风险</th><th>最终结算</th><th></th></tr>
        </thead>
        <tbody>
          ${rows.map((s) => `
            <tr>
              <td class="signal-time" data-label="发布时间（北京时间）">${signalPublicationTime(s.created_at)}</td>
              <td data-label="信号">${formalSignalBadge(s)}</td>
              ${includeMatch ? `<td data-label="比赛"><a class="link-button" href="#match/${s.match_id}" title="${escapeHtml(s.tournament || "")}">比赛 ${s.match_id}</a></td><td data-label="赛事级别">${tournamentLevelBadge(s.tournament_level)}</td><td data-label="巡回赛">${tournamentNameCell(s.tournament, s.competition_stage, s.round)}</td>` : ""}
              <td data-label="比赛状态">${statusBadge(s.match_status)}</td>
              <td data-label="固定方向">${playerLink(s.player)}${s.trigger ? `<small class="signal-lock">触发点 ${fmt(s.trigger.published_point)}</small>` : ""}${s.current_notice ? `<small class="signal-lock">${escapeHtml(s.current_notice)}</small>` : ""}</td>
              <td data-label="触发趋势">${pct(s.trend_confidence_score)}</td>
              <td data-label="触发风险">${pct(s.collapse_risk_score)}</td>
              <td data-label="最终结算">${zhResult(s.direction_match_result)}</td>
              <td data-label="详情"><a class="link-button" href="#signal/${s.id}">查看详情</a></td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
    ${pageKey ? paginationControls(pagination, pageKey) : ""}`;
}

async function renderOverview() {
  setHeader("总览", "系统状态、额度与近期趋势信号");
  const data = await api("/web/api/overview");
  const counts = data.counts || {};
  view.innerHTML = `
    <div class="grid cols-4">
      ${metric("实时比赛", counts.live_matches || 0)}
      ${metric("24小时信号", counts.signals_24h || 0)}
      ${metric("跟踪球员", counts.tracked_players || 0)}
      ${metric("已完赛", counts.completed_matches || 0)}
    </div>
    <div class="grid cols-2">
      ${panel("信号等级分布", `<div class="chart-box small"><canvas id="level-chart"></canvas></div>`)}
      ${panel("API 额度", `<div class="chart-box small"><canvas id="quota-chart"></canvas></div>`)}
    </div>
    <div>
      <h2 class="section-title">近期信号</h2>
      ${signalsTable(data.recent_signals, true)}
    </div>`;
  barChart("level-chart", (data.level_distribution || []).map((x) => x.level), (data.level_distribution || []).map((x) => x.count));
  barChart("quota-chart", (data.quota || []).map((q) => q.api_key_hash), (data.quota || []).map((q) => q.quota_remaining), "#0f766e");
}

async function renderHistory() {
  setHeader("历史同步", "查看每日同步状态，按日期或日期段同步真实 Sportradar 数据");
  const defaultStart = dateInputValue(-13);
  const defaultEnd = dateInputValue(0);
  view.innerHTML = `
    <section class="panel">
      <div class="sync-form">
        <label>开始日期<input id="history-start" class="field" type="date" value="${defaultStart}" /></label>
        <label>结束日期<input id="history-end" class="field" type="date" value="${defaultEnd}" /></label>
        <label class="check-field"><input id="history-women-only" type="checkbox" checked disabled /> 仅 BWF 女单</label>
        <button id="history-load" class="action-button"><i data-lucide="search"></i>查询状态</button>
        <button id="history-sync" class="action-button primary"><i data-lucide="download-cloud"></i>同步</button>
        <button id="history-resync" class="action-button danger"><i data-lucide="rotate-ccw"></i>强制重同步</button>
      </div>
      <p class="help-text">强制重同步会清除所选日期的 processed_dates 标记，然后重新拉取 summary 和 timeline；已有比赛会按外部 ID 更新。</p>
    </section>
    <div id="history-summary" class="grid cols-4"></div>
    <div id="history-table">${empty("请选择日期段后查询。")}</div>
    <div id="history-result"></div>`;
  syncIcons();

  const startEl = document.getElementById("history-start");
  const endEl = document.getElementById("history-end");
  const wtaEl = document.getElementById("history-women-only");
  const tableEl = document.getElementById("history-table");
  const summaryEl = document.getElementById("history-summary");
  const resultEl = document.getElementById("history-result");
  const historyPageKey = "history-dates";

  const enqueue = async (start, end, force) => {
    const query = new URLSearchParams({
      start_date: start,
      end_date: end,
      women_only: String(wtaEl.checked),
      force: String(force),
    });
    const result = await api(`/web/api/actions/history-sync?${query.toString()}`, { method: "POST" });
    resultEl.innerHTML = panel("最近操作", renderJson(result), "result-panel");
    showToast(force ? "已提交重同步任务" : "已提交同步任务");
  };

  const renderHistoryRows = (rows) => {
    const pageData = clientPage(rows, historyPageKey, 15);
    tableEl.innerHTML = `
      <div class="table-panel">
        <table>
          <thead><tr><th>日期</th><th>同步状态</th><th>导入成功数</th><th>库内比赛</th><th>完赛</th><th>比赛状态分布</th><th>记录时间</th><th>操作</th></tr></thead>
          <tbody>
            ${pageData.rows.map((r) => `
              <tr>
                <td>${fmt(r.date)}</td>
                <td>${statusBadge(r.processed_status)}</td>
                <td>${fmt(r.processed_match_count, "0")}</td>
                <td>${fmt(r.db_match_count, "0")}</td>
                <td>${fmt(r.completed_match_count, "0")}</td>
                <td>${Object.entries(r.status_counts || {}).map(([k, v]) => `${zhStatus(k)} ${v}`).join(" / ") || "-"}</td>
                <td>${time(r.processed_at)}</td>
                <td>
                  <button class="inline-action" data-date="${fmt(r.date)}" data-force="false">同步</button>
                  <button class="inline-action danger" data-date="${fmt(r.date)}" data-force="true">重同步</button>
                </td>
              </tr>`).join("") || `<tr><td colspan="8">暂无记录。</td></tr>`}
          </tbody>
        </table>
      </div>
      ${paginationControls(pageData.pagination, historyPageKey)}`;
    tableEl.querySelectorAll("[data-date]").forEach((btn) => {
      btn.addEventListener("click", () => enqueue(btn.dataset.date, btn.dataset.date, btn.dataset.force === "true"));
    });
    bindPagination(tableEl, () => renderHistoryRows(rows));
    syncIcons();
  };

  const load = async () => {
    resetPage(historyPageKey);
    const data = await api(`/web/api/history-sync?start_date=${encodeURIComponent(startEl.value)}&end_date=${encodeURIComponent(endEl.value)}`);
    const rows = data.dates || [];
    const done = rows.filter((r) => r.processed_status === "completed").length;
    const partial = rows.filter((r) => r.processed_status === "partial").length;
    const missing = rows.filter((r) => r.processed_status === "not_synced").length;
    const matches = rows.reduce((sum, r) => sum + Number(r.db_match_count || 0), 0);
    summaryEl.innerHTML = `
      ${metric("日期数", rows.length)}
      ${metric("已完成", done)}
      ${metric("部分完成", partial)}
      ${metric("未同步", missing, `${matches} 场已入库比赛`)}`;
    renderHistoryRows(rows);
  };

  document.getElementById("history-load").addEventListener("click", load);
  document.getElementById("history-sync").addEventListener("click", () => enqueue(startEl.value, endEl.value, false));
  document.getElementById("history-resync").addEventListener("click", () => enqueue(startEl.value, endEl.value, true));
  await load();
}

async function renderSignals() {
  setHeader("信号", "每场一次，发布后固定方向。每15秒刷新。");
  const pageKey = "signals";
  const pageSizes = [10, 25, 50, 100];
  let savedPageSize = state.signalPageSize;
  try { savedPageSize ??= Number(localStorage.getItem("dashboard-signals-page-size")); } catch {}
  state.signalPageSize = pageSizes.includes(savedPageSize) ? savedPageSize : 25;
  view.innerHTML = `
    <div class="signals-toolbar">
      <p id="signals-refresh-status" role="status">正在同步信号…</p>
      <label class="signals-page-size" for="signals-page-size">每页显示
        <select id="signals-page-size">
          ${pageSizes.map(size => `<option value="${size}" ${size === state.signalPageSize ? "selected" : ""}>${size} 条</option>`).join("")}
        </select>
      </label>
    </div>
    <div id="formal-s-summary"></div>
    <div id="signals-list"></div>`;
  const list = document.getElementById("signals-list");
  const summary = document.getElementById("formal-s-summary");
  const refreshStatus = document.getElementById("signals-refresh-status");
  let loading = false;
  let reloadRequested = false;
  let disposed = false;
  let lastUpdated = null;
  let controller = null;
  const load = async () => {
    if (disposed) return;
    if (loading) { reloadRequested = true; return; }
    loading = true;
    reloadRequested = false;
    const requestedPage = getPage(pageKey);
    const requestedSize = state.signalPageSize;
    controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 12000);
    try {
      const data = await api(`/web/api/signals?scope=official&page=${requestedPage}&per_page=${requestedSize}`, {
        cache: "no-store", signal: controller.signal,
      });
      if (disposed) return;
      if (requestedPage !== getPage(pageKey) || requestedSize !== state.signalPageSize) return;
      const stats = data.summary;
      if (stats) summary.innerHTML = `<div class="grid cols-4">${metric("信号", stats.total)}${metric("待结算", stats.pending)}${metric("命中 / 未中", `${stats.correct} / ${stats.wrong}`)}${metric("准确率", stats.accuracy == null ? "—" : pct(stats.accuracy))}</div>${stats.special ? `<small class="signal-summary-note">退赛等 ${stats.special} 场单列，不计入准确率</small>` : ""}`;
      list.innerHTML = data.signals.length ? signalsTable(data.signals, true, { pageKey, pagination: data.pagination }) : empty("暂无信号。未确认评分不会作为推荐显示。");
      bindPagination(list, load);
      syncIcons();
      lastUpdated = new Date().toLocaleTimeString("zh-CN", { hour12: false });
      refreshStatus.textContent = `最近更新：${lastUpdated} · 每15秒自动同步`;
    } catch (err) {
      if (!disposed) refreshStatus.textContent = `${lastUpdated ? `最近更新：${lastUpdated} · ` : ""}同步失败，将自动重试`;
    } finally {
      window.clearTimeout(timeout);
      loading = false;
      if (reloadRequested && !disposed) void load();
    }
  };
  document.getElementById("signals-page-size").addEventListener("change", (event) => {
    const size = Number(event.target.value);
    if (!pageSizes.includes(size)) return;
    state.signalPageSize = size;
    try { localStorage.setItem("dashboard-signals-page-size", String(size)); } catch {}
    resetPage(pageKey);
    void load();
  });
  const refreshWhenVisible = () => { if (!document.hidden) void load(); };
  const timer = setInterval(refreshWhenVisible, 15000);
  document.addEventListener("visibilitychange", refreshWhenVisible);
  window.addEventListener("focus", refreshWhenVisible);
  window.addEventListener("online", refreshWhenVisible);
  signalRefreshCleanup = () => {
    disposed = true;
    clearInterval(timer);
    controller?.abort();
    document.removeEventListener("visibilitychange", refreshWhenVisible);
    window.removeEventListener("focus", refreshWhenVisible);
    window.removeEventListener("online", refreshWhenVisible);
  };
  await load();
}


function historyScore(score) {
  return `局分 ${score.games.join("–")} · 小分 ${score.points.join("–")}`;
}

function signalHistoryHtml(data) {
  if (!data.current || !data.summary) return empty("缺少发布时的比分，暂无法对照历史。");
  const n = data.matched_matches;
  const summary = data.summary;
  const enough = data.status === "available";
  const rate = enough ? `历史胜率 ${pct(summary.win_rate)} · 95%区间 ${pct(summary.win_rate_interval[0])}–${pct(summary.win_rate_interval[1])}`
    : `至少 ${data.minimum_matches} 场才展示历史胜率`;
  const cases = (data.matches || []).map(row => `<tr>
    <td>${fmt(String(row.date).slice(0, 10))}</td>
    <td><a class="link-button" href="#match/${encodeURIComponent(row.match_id)}">${fmt(row.opponent)}</a><small class="signal-lock">${fmt(row.tournament)}</small></td>
    <td>${fmt(historyScore(row.anchor))}<small class="signal-lock">第 ${fmt(row.anchor.point)} 个回合</small></td>
    <td>${pct(row.anchor.recent20)}</td>
    <td>${row.won ? "获胜" : "失利"}</td>
  </tr>`).join("");
  return `<div class="grid cols-2">
    ${metric("相似历史比赛", n, `同赛制候选 ${data.candidate_matches} 场`)}
    ${metric("历史获胜", n ? `${summary.wins} / ${n}` : "—", rate)}
  </div>
  <p class="metric-hint">S 发布比分（该球员在前） ${fmt(historyScore(data.current))} · 近 20 回合得分率 ${pct(data.current.recent20)}</p>
  ${n ? `<details><summary class="link-button">查看 ${n} 场历史明细</summary><div class="table-wrap"><table><thead><tr><th>日期</th><th>对手 / 赛事</th><th>相似局势</th><th>近 20 回合得分率</th><th>赛果</th></tr></thead><tbody>${cases}</tbody></table></div></details>` : empty("未找到相似局势。")}
  <p class="metric-hint">对照使用本场开赛前完赛的同赛制单打，每场取一个最相似位置。匹配相同局序和局分，小分总差不超过 4，近 20 回合得分率相差不超过 15 个百分点。</p>`;
}

async function loadSignalHistory(id, container) {
  try {
    const data = await api(`/web/api/signals/${id}/history-comparison`, { cache: "no-store" });
    if (!container?.isConnected) return;
    container.innerHTML = signalHistoryHtml(data);
    syncIcons();
  } catch (error) {
    if (container?.isConnected) container.innerHTML = empty(`历史对照暂不可用：${error.message}`);
  }
}

async function renderSignalDetail(id) {
  const data = await api(`/web/api/signals/${id}`);
  const s = data.signal;
  const match = data.match;
  setHeader(`信号 ${id}`, `${matchName(match)} · ${playerName(s.player)} · 北京时间 ${signalPublicationTime(s.created_at)}`);
  view.innerHTML = `
    ${matchDashboard(data, { signal: s })}
    <div class="grid cols-2">
      ${panel("触发原因", `<ul>${(s.reason_json.reasons || []).map((r) => `<li>${fmt(r)}</li>`).join("") || "<li>暂无记录</li>"}</ul>`)}
      ${panel("风险提示", `<ul>${(s.risk_json.risks || []).map((r) => `<li>${fmt(r)}</li>`).join("") || "<li>暂无记录</li>"}</ul>`)}
    </div>
    <a class="action-button primary detail-link" href="#match/${s.match_id}"><i data-lucide="external-link"></i>打开完整比赛详情</a>`;
  drawMatchDashboardCharts(data);
  bindPagination(view, render);
  void loadSignalHistory(id, document.getElementById("signal-history-comparison"));
}

async function renderLive() {
  setHeader("今日比赛", "当天比赛及跨日未完赛比赛；暂停／中断仍持续追踪");
  const data = await api("/web/api/matches/live");
  const groups = groupMatchesByTournament(data.matches || []);
  const activeStatuses = data.active_statuses || ["live", "suspended", "interrupted", "delayed"];
  const scheduledStatuses = data.scheduled_statuses || ["not_started", "scheduled"];
  const completedStatuses = data.completed_statuses || ["ended", "completed", "closed"];
  const activeTotal = statusCount(data.status_counts, ["live"]);
  const pausedTotal = statusCount(data.status_counts, ["suspended", "interrupted", "delayed"]);
  const scheduledTotal = statusCount(data.status_counts, scheduledStatuses);
  const completedTotal = statusCount(data.status_counts, completedStatuses);
  if (!groups.length) {
    view.innerHTML = empty("今天暂无比赛记录。");
    return;
  }
  view.innerHTML = `
    <div class="day-match-summary">
      ${metric("日期", fmt(data.date))}
      ${metric("全部比赛", fmt((data.matches || []).length, "0"))}
      ${metric("未开始", fmt(scheduledTotal, "0"))}
      ${metric("进行中", fmt(activeTotal, "0"))}
      ${metric("暂停／中断／延迟", fmt(pausedTotal, "0"))}
      ${metric("已完赛", fmt(completedTotal, "0"))}
    </div>
    <div class="tournament-groups">
      ${groups.map((group) => {
        const collapsed = !!state.collapsedTournaments[group.key];
        const activeCount = group.matches.filter((m) => m.status === "live").length;
        const pausedCount = group.matches.filter((m) => ["suspended", "interrupted", "delayed"].includes(m.status)).length;
        const scheduledCount = group.matches.filter((m) => matchStatusBucket(m.status) === "scheduled").length;
        const completedCount = group.matches.filter((m) => matchStatusBucket(m.status) === "completed").length;
        const cancelledCount = group.matches.filter((m) => matchStatusBucket(m.status) === "cancelled").length;
        return `
          <section class="tournament-group ${collapsed ? "collapsed" : ""}">
            <button class="tournament-toggle" data-tournament-key="${escapeHtml(group.key)}" aria-expanded="${collapsed ? "false" : "true"}">
              <span class="toggle-icon"><i data-lucide="${collapsed ? "chevron-right" : "chevron-down"}"></i></span>
              <span>
                <strong>${fmt(group.tournament)}</strong>
                <small>${tournamentLevelBadge(group.tournament_level)} · ${zhSurface(group.surface)}</small>
              </span>
              <span class="tournament-counts">
                <em>${group.matches.length} 场</em>
                ${activeCount ? `<em class="active">${activeCount} 进行中</em>` : ""}
                ${pausedCount ? `<em>${pausedCount} 暂停／中断／延迟</em>` : ""}
                ${scheduledCount ? `<em>${scheduledCount} 未开始</em>` : ""}
                ${completedCount ? `<em>${completedCount} 已完赛</em>` : ""}
                ${cancelledCount ? `<em>${cancelledCount} 已取消</em>` : ""}
              </span>
            </button>
            ${collapsed ? "" : `
              <div class="live-grid">
                ${group.matches.map((m) => {
                  const players = matchPlayers(m);
                  return `
                    <article class="live-card ${matchStatusBucket(m.status)}">
                      <div class="live-card-head">
                        <span>${time(m.match_date)}${m.carried_over ? " · 跨日未完赛" : ""}</span>
                        <strong>${zhStatus(m.status)}</strong>
                      </div>
                      <div class="live-card-title"><a class="match-link" href="#match/${m.id}">${matchName(m)}</a></div>
                      <div class="live-card-meta">${zhSurface(m.surface)} · ${roundLabel(m.round)} · 信号 ${fmt(m.signals_count, "0")}</div>
                      <div class="live-card-score">${liveMatchScore(m)}</div>
                      <div class="live-card-players">
                        ${players.map((player) => {
                          const snap = latestSnapshotFor(m.latest_snapshots, player.id);
                          return `
                            <div>
                              <span>${playerLink(player)}</span>
                              <strong>${snap ? pct(snap.trend_confidence_score) : "-"}</strong>
                            </div>`;
                        }).join("")}
                      </div>
                    </article>`;
                }).join("")}
              </div>`}
          </section>`;
      }).join("")}
    </div>`;
  view.querySelectorAll(".tournament-toggle").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.dataset.tournamentKey;
      state.collapsedTournaments[key] = !state.collapsedTournaments[key];
      await renderLive();
      syncIcons();
    });
  });
}

async function renderMatch(id) {
  const data = await api(`/web/api/matches/${id}`);
  const match = data.match;
  setHeader(`比赛 ${id}`, matchName(match));
  view.innerHTML = matchDashboard(data);
  drawMatchDashboardCharts(data);
  bindPagination(view, render);
}

async function renderPlayers() {
  setHeader("球员", "画像、模式与信号历史");
  const pageKey = "players";
  view.innerHTML = `
    <div class="toolbar">
      <input id="player-search" class="field" placeholder="输入球员姓名" />
      <button id="player-search-btn" class="action-button"><i data-lucide="search"></i>搜索</button>
    </div>
    <div id="players-list"></div>`;
  syncIcons();
  const load = async () => {
    const q = document.getElementById("player-search").value.trim();
    const data = await api(`/web/api/players?page=${getPage(pageKey)}&per_page=25&search=${encodeURIComponent(q)}`);
    const list = document.getElementById("players-list");
    list.innerHTML = `
      <div class="table-panel">
        <table>
          <thead><tr><th>姓名</th><th>国家</th><th>持拍手</th><th></th></tr></thead>
          <tbody>${data.players.map((p) => `<tr><td>${fmt(p.name)}</td><td>${fmt(p.country)}</td><td>${fmt(p.hand)}</td><td><a class="link-button" href="#player/${p.id}">打开</a></td></tr>`).join("") || `<tr><td colspan="4">未找到球员。</td></tr>`}</tbody>
        </table>
      </div>
      ${paginationControls(data.pagination, pageKey)}`;
    bindPagination(list, load);
    syncIcons();
  };
  document.getElementById("player-search-btn").addEventListener("click", async () => {
    resetPage(pageKey);
    await load();
  });
  document.getElementById("player-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      resetPage(pageKey);
      load();
    }
  });
  await load();
}

function pctMaybe(value) {
  if (value === null || value === undefined) return "-";
  return pct(value);
}

function signedPct(value) {
  if (value === null || value === undefined) return "-";
  const n = Number(value || 0) * 100;
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function renderBehaviorSummary(behavior) {
  const s = behavior?.summary || {};
  return `<div class="grid cols-3">
    ${metric("整场胜率", pctMaybe(s.match_win_rate), `${fmt(s.matches, "0")} 场`)}
    ${metric("总得分率", pctMaybe(s.rally_win_rate), `${fmt(s.rallies, "0")} 个回合`)}
    ${metric("局点回合得分率", pctMaybe(s.pressure_win_rate), `${fmt(s.pressure_rallies, "0")} 个回合`)}
  </div>`;
}

function renderBehaviorInsights() {
  return "";
}

function renderBehaviorTables(behavior) {
  const rows = behavior?.phase_rows || [];
  if (!rows.length) return empty("暂无完整逐分历史。");
  return `<table><thead><tr><th>赛制</th><th>阶段</th><th>回合</th><th>得分率</th></tr></thead>
    <tbody>${rows.map(r => `<tr><td>${fmt(r.scoring_target)} 分制</td><td>${fmt(r.phase)}</td>
      <td>${fmt(r.rallies)}</td><td>${pctMaybe(r.win_rate)}</td></tr>`).join("")}</tbody></table>`;
}

function renderPlayerHistoricalMatches(matches, pageKey) {
  const pageData = clientPage(matches || [], pageKey, 20);
  const rows = pageData.rows;
  return `
    <div class="table-panel">
      <table>
        <thead><tr><th>日期</th><th>赛事</th><th>轮次</th><th>场地</th><th>对手</th><th>结果</th><th>比分</th><th>逐分点</th></tr></thead>
        <tbody>${rows.map((m) => `
          <tr>
            <td>${time(m.match_date)}</td>
            <td>${tournamentLevelBadge(m.tournament_level)} <a class="link-button" href="#match/${m.id}">${fmt(m.tournament)}</a></td>
            <td>${roundLabel(m.round)}</td>
            <td>${zhSurface(m.surface)}</td>
            <td>${playerName(m.opponent)}</td>
            <td>${zhResult(m.result)}</td>
            <td>${fmt(m.final_score)}</td>
            <td>${fmt(m.point_count, "0")}</td>
          </tr>`).join("") || `<tr><td colspan="8">暂无逐分历史比赛。</td></tr>`}</tbody>
      </table>
    </div>
    ${paginationControls(pageData.pagination, pageKey)}`;
}

async function renderPlayer(id) {
  const data = await api(`/web/api/players/${id}`);
  setHeader(data.player.name, "球员画像、历史模式与信号记录");
  view.innerHTML = `
    ${renderBehaviorSummary(data.behavior)}
    ${panel("行为模式", renderBehaviorInsights(data.behavior))}
    ${renderBehaviorTables(data.behavior, `player-${id}-behavior`)}
    <div><h2 class="section-title">洞察依据比赛</h2>${renderPlayerHistoricalMatches(data.historical_matches, `player-${id}-matches`)}</div>
    <div class="table-panel">
      <table>
        <thead><tr><th>场地</th><th>样本</th><th>胜场总得分率</th><th>负场总得分率</th><th>胜场发球得分</th><th>胜场接发得分</th></tr></thead>
        <tbody>${data.profiles.map((p) => `
          <tr><td>${zhSurface(p.surface)}</td><td>${p.sample_matches}</td><td>${pct(p.win.total_points_win_rate)}</td><td>${pct(p.loss.total_points_win_rate)}</td><td>${pct(p.win.service_points_win_rate)}</td><td>${pct(p.win.return_points_win_rate)}</td></tr>`).join("") || `<tr><td colspan="6">暂无画像。</td></tr>`}</tbody>
      </table>
    </div>
    <div><h2 class="section-title">信号</h2>${signalsTable(data.signals, true, { pageKey: `player-${id}-signals`, pageSize: 20 })}</div>`;
  bindPagination(view, render);
}

async function renderBacktests() {
  setHeader("回测", "历史验证结果");
  const runsKey = "backtest-runs";
  const resultsKey = "backtest-results";
  const data = await api(`/web/api/backtests?runs_page=${getPage(runsKey)}&runs_per_page=10&results_page=${getPage(resultsKey)}&results_per_page=25`);
  view.innerHTML = `
    <div class="grid cols-2">
      ${panel("近期回测", `
        <div class="table-panel">
          <table><thead><tr><th>名称</th><th>创建时间</th><th>比赛数</th><th>信号数</th></tr></thead>
          <tbody>${data.runs.map((r) => `<tr><td>${fmt(r.run_name)}</td><td>${time(r.created_at)}</td><td>${fmt(r.metrics_json.total_matches, "0")}</td><td>${fmt(r.metrics_json.total_signals_triggered, "0")}</td></tr>`).join("") || `<tr><td colspan="4">暂无回测。</td></tr>`}</tbody></table>
        </div>
        ${paginationControls(data.runs_pagination, runsKey)}`)}
      ${panel("命中率", `<div class="chart-box small"><canvas id="backtest-hit"></canvas></div>`)}
    </div>
    <div class="table-panel">
      <table>
        <thead><tr><th>等级</th><th>场地</th><th>阶段</th><th>样本</th><th>命中</th><th>命中率</th><th>整场胜率</th><th>最大连错</th></tr></thead>
        <tbody>${data.results.map((r) => `<tr><td>${levelBadge(r.signal_level)}</td><td>${zhSurface(r.surface)}</td><td>${fmt(r.match_stage)}</td><td>${r.sample_count}</td><td>${r.hit_count}</td><td>${pct(r.hit_rate)}</td><td>${pct(r.metrics_json?.direction_match_win_rate)}</td><td>${r.max_consecutive_losses}</td></tr>`).join("") || `<tr><td colspan="8">暂无结果。</td></tr>`}</tbody>
      </table>
    </div>
    ${paginationControls(data.results_pagination, resultsKey)}`;
  barChart("backtest-hit", data.results.map((r) => r.signal_level), data.results.map((r) => r.hit_rate));
  bindPagination(view, render);
}

function schedulerPanel(scheduler) {
  const cfg = scheduler.config || {};
  const jobs = scheduler.jobs || [];
  const serverTime = scheduler.server_time || "未知";
  const serverTz = scheduler.server_timezone || "未知";
  
  return panel("定时任务", `
    <div class="server-time-indicator">
      <div><strong>当前系统时间：</strong><span class="time-value">${serverTime}</span></div>
      <div><strong>系统时区：</strong><span class="time-value">${serverTz}</span></div>
    </div>
    <div class="grid cols-3 scheduler-status">
      ${metric("调度器", yesNo(scheduler.running))}
      ${metric("实时轮询", yesNo(cfg.enable_live_monitor), `${cfg.live_monitor_interval_seconds || 180} 秒 · 每日赛程自动窗口`)}
      ${metric("每日历史同步", yesNo(cfg.enable_daily_history_import), `${cfg.daily_history_import_hour ?? 2}:${String(cfg.daily_history_import_minute ?? 0).padStart(2, "0")} ${cfg.daily_history_import_timezone || "Asia/Shanghai"}`)}
    </div>
    <form id="scheduler-form" class="scheduler-form-new">
      <div class="scheduler-form-section">
        <h4 class="section-title">赛程自动监控</h4><p>按每日 BWF 女单赛程自动开启；未结束和延赛持续监控，全部结束后关闭实时扫描。赛程异常时保持监控。</p>
        <div class="scheduler-form-grid">
          <label class="check-field"><input id="cfg-live-enabled" type="checkbox" ${cfg.enable_live_monitor ? "checked" : ""} /> 启用赛程自动监控</label>
          <label>比赛期间基础轮询间隔 (秒)<input id="cfg-live-interval" class="field" type="number" min="60" max="3600" step="30" value="${cfg.live_monitor_interval_seconds || 180}" /></label>
        </div>
      </div>
      
      <div class="scheduler-form-section">
        <h4 class="section-title">每日历史同步配置</h4>
        <div class="scheduler-form-grid">
          <label class="check-field"><input id="cfg-history-enabled" type="checkbox" ${cfg.enable_daily_history_import ? "checked" : ""} /> 开启每日历史同步</label>
          <label>小时 (0-23)<input id="cfg-history-hour" class="field" type="number" min="0" max="23" value="${cfg.daily_history_import_hour ?? 2}" /></label>
          <label>分钟 (0-59)<input id="cfg-history-minute" class="field" type="number" min="0" max="59" value="${cfg.daily_history_import_minute ?? 0}" /></label>
          <label>同步时区<input id="cfg-history-timezone" class="field" value="${fmt(cfg.daily_history_import_timezone || "Asia/Shanghai")}" /></label>
        </div>
      </div>
      
      <div class="form-actions">
        <button class="action-button primary" type="submit"><i data-lucide="save"></i>保存并重启调度</button>
      </div>
    </form>
    <div class="table-panel compact-table">
      <table>
        <thead><tr><th>任务</th><th>下次运行</th><th>触发器</th></tr></thead>
        <tbody>${jobs.map((job) => `<tr><td>${fmt(job.id)}</td><td>${time(job.next_run_time)}</td><td>${fmt(job.trigger)}</td></tr>`).join("") || `<tr><td colspan="3">当前没有已注册任务。</td></tr>`}</tbody>
      </table>
    </div>
    <p class="help-text">页面保存的是当前进程内配置；长期配置由服务器文件管理。</p>
  `);
}

async function renderOps() {
  setHeader("运维", "额度、定时任务、缓存与维护动作");
  const [overview, scheduler] = await Promise.all([
    api("/web/api/overview"),
    api("/web/api/scheduler"),
  ]);
  view.innerHTML = `
    ${schedulerPanel(scheduler)}
    ${panel("API 额度", `
      <div class="table-panel">
        <table><thead><tr><th>Key 哈希</th><th>已用</th><th>剩余</th><th>冷却</th><th>最近请求</th></tr></thead>
        <tbody>${overview.quota.map((q) => `<tr><td>${q.api_key_hash}</td><td>${q.quota_used}</td><td>${q.quota_remaining}</td><td>${fmt(q.cooldown_until)}</td><td>${time(q.last_request_at)}</td></tr>`).join("") || `<tr><td colspan="5">暂无额度记录。</td></tr>`}</tbody></table>
      </div>`)}
    ${scheduler.web_manage_sportradar_keys ? panel("录入新 API Key", `
      <form id="keys-add-form" class="config-form">
        <label>
          请输入 API Key（多个 Key 请换行输入）：
          <textarea id="new-api-keys" class="field" rows="5" style="width: 100%; font-family: monospace;" placeholder="示例：&#10;key1_abcde...&#10;key2_fghij..." required></textarea>
        </label>
        <button type="submit" class="action-button primary">录入 Key</button>
      </form>
    `) : ""}
    <div class="action-grid">
      <button class="action-button primary" data-action="/web/api/actions/monitor-run-once">手动扫描一次</button>
      ${scheduler.web_manage_sportradar_keys ? `<button class="action-button" data-action="/web/api/actions/keys-cleanup">校验并清理失效 Key</button>` : ""}
      <button class="action-button" data-action="/web/api/actions/cache-clear">清理 API 缓存</button>
      <button class="action-button" data-action="/web/api/actions/profiles-rebuild">重建球员画像</button>
      <button class="action-button" data-action="/web/api/actions/patterns-mine">挖掘球员模式</button>
      <button class="action-button primary" data-action="/web/api/actions/models-train?target_label=label_win_match">训练全场胜负模型</button>
    </div>
    <div id="ops-result"></div>`;
  syncIcons();

  document.getElementById("scheduler-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {
      enable_live_monitor: document.getElementById("cfg-live-enabled").checked,
      live_monitor_interval_seconds: Number(document.getElementById("cfg-live-interval").value),
      enable_daily_history_import: document.getElementById("cfg-history-enabled").checked,
      daily_history_import_hour: Number(document.getElementById("cfg-history-hour").value),
      daily_history_import_minute: Number(document.getElementById("cfg-history-minute").value),
      daily_history_import_timezone: document.getElementById("cfg-history-timezone").value.trim(),
    };
    const result = await api("/web/api/actions/scheduler-config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    document.getElementById("ops-result").innerHTML = panel("最近操作", renderJson(result), "result-panel");
    showToast("调度配置已更新");
    await renderOps();
  });

  document.getElementById("keys-add-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = event.target.querySelector("button[type='submit']");
    btn.disabled = true;
    try {
      const keysText = document.getElementById("new-api-keys").value;
      const result = await api("/web/api/actions/keys-add", {
        method: "POST",
        body: JSON.stringify({ keys: keysText }),
      });
      document.getElementById("ops-result").innerHTML = panel("最近操作", renderJson(result), "result-panel");
      if (result.status === "success") {
        showToast(`成功录入 ${result.added_count} 个 API Key`);
      } else if (result.error) {
        showToast("录入失败：" + result.error);
      }
      await renderOps();
    } catch (err) {
      document.getElementById("ops-result").innerHTML = panel("操作失败", escapeHtml(err.message), "result-panel");
      showToast("操作失败");
    } finally {
      btn.disabled = false;
    }
  });

  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const result = await api(btn.dataset.action, { method: "POST" });
        document.getElementById("ops-result").innerHTML = panel("最近操作", renderJson(result), "result-panel");
        showToast("操作完成");
        // Reload dashboard keys overview after verify/cleanup finishes
        if (btn.dataset.action.includes("keys-cleanup") || btn.dataset.action.includes("cache-clear")) {
          await renderOps();
        }
      } catch (err) {
        document.getElementById("ops-result").innerHTML = panel("操作失败", escapeHtml(err.message), "result-panel");
        showToast("操作失败");
      } finally {
        btn.disabled = false;
      }
    });
  });
}

let signalRefreshCleanup = null;
async function render() {
  signalRefreshCleanup?.();
  signalRefreshCleanup = null;
  clearCharts();
  const hash = window.location.hash.replace(/^#/, "") || "signals";
  const [route, id] = hash.split("/");
  const activeRoute = route === "signal" ? "signals" : route === "match" ? "live" : route === "player" ? "players" : route;
  setActive(activeRoute);
  view.innerHTML = empty("加载中...");
  try {
    if (route === "overview") await renderOverview();
    else if (route === "history") await renderHistory();
    else if (route === "signals") await renderSignals();
    else if (route === "pushed-signals") window.location.replace("#signals");
    else if (route === "signal" && id) await renderSignalDetail(id);
    else if (route === "live") await renderLive();
    else if (route === "match" && id) await renderMatch(id);
    else if (route === "players") await renderPlayers();
    else if (route === "player" && id) await renderPlayer(id);
    else if (route === "backtests") await renderBacktests();
    else if (route === "ops") await renderOps();
    else window.location.hash = "#overview";
    syncIcons();
  } catch (err) {
    view.innerHTML = empty(err.message);
  }
}

document.getElementById("refresh-btn").addEventListener("click", render);
themeToggle.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme || "light";
  applyTheme(current === "dark" ? "light" : "dark");
});
window.addEventListener("hashchange", render);
initTheme();
syncIcons();
render();
