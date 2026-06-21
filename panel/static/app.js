// Content Factory Panel — фронтенд (vanilla JS)
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const PLABEL = { instagram: "Instagram", threads: "Threads", vk: "VK", tiktok: "TikTok", youtube: "YouTube" };
const STATUS_RU = {
  generating: "генерируется", queued: "в очереди", published: "опубликовано",
  partial: "частично", pending_manual: "ждёт выкладки", failed: "ошибка",
  not_configured: "не настроено"
};
const PLAN_STATUS = { planned: "запланировано", generating: "генерация", ready: "готово",
  posted: "выложено", manual_pending: "ждёт выкладки", failed: "ошибка" };
let NICHES = [], state = { view: "dashboard", editingAccount: null };
let planState = { bundleId: null, date: null };
const todayISO = () => new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10); // МСК

async function api(path, method = "GET", body) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch("/api" + path, opt);
  if (!r.ok) { const t = await r.text(); throw new Error(t || r.status); }
  return r.status === 204 ? null : r.json();
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.add("hidden"), 2800);
}
const esc = s => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ───── навигация ─────
function switchView(v) {
  state.view = v;
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(s => s.classList.toggle("hidden", s.id !== "view-" + v));
  $("#view-title").textContent = { dashboard: "Дашборд", plan: "План дня", bundles: "Связки", queue: "Очередь", analytics: "Аналитика" }[v];
  refresh();
}

// ───── дашборд ─────
async function renderDashboard() {
  const o = await api("/overview");
  $("#stat-grid").innerHTML = [
    ["Связок", o.bundles, ""], ["Подписчиков", o.subscribers.toLocaleString("ru"), "accent"],
    ["В очереди", o.queued, ""], ["Ждут выкладки (TikTok)", o.pending_manual, "warn"],
    ["Генерируется", o.generating, ""],
  ].map(([l, v, c]) => `<div class="stat ${c}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  // строка здоровья системы (надёжность на виду); убираем прежнюю перед вставкой (анти-дубль при опросе)
  try {
    const oldH = $("#health-row"); if (oldH) oldH.remove();
    const h = await api("/health");
    const gb = (h.disk_free_mb / 1024).toFixed(0);
    $("#stat-grid").insertAdjacentHTML("afterend",
      `<div class="health ${h.ok ? "ok" : "bad"}" id="health-row">
        <span class="hdot"></span>
        <b>${h.ok ? "Система в норме" : "Внимание"}</b>
        · диск ${gb} ГБ ${h.disk_ok ? "✓" : "⚠ мало!"}
        · LLM готово: ${h.llm_ready_providers}
        · тем в базе: ${h.topics_in_db}
        ${h.last_build ? `· последний: «${esc(h.last_build.topic || "")}»` : ""}
      </div>`);
  } catch (e) { /* health необязателен */ }
  const items = await api("/queue");
  $("#dash-recent").innerHTML = items.length
    ? items.slice(0, 5).map(qItem).join("")
    : `<div class="empty"><div class="big">∅</div>Контента пока нет. Создай связку и сгенерируй ролик.</div>`;
  bindQueueActions($("#dash-recent"));
}

// ───── связки ─────
async function renderBundles() {
  const bundles = await api("/bundles");
  const grid = $("#bundles-grid");
  if (!bundles.length) {
    grid.innerHTML = `<div class="empty"><div class="big">⬡</div>Связок пока нет.<br>Нажми «+ Новая связка», чтобы создать первую.</div>`;
    return;
  }
  grid.innerHTML = bundles.map(b => {
    const niche = NICHES.find(n => n.id === b.niche_id);
    const pills = b.accounts.map(a => `
      <div class="pill ${a.status}" data-acc='${esc(JSON.stringify(a))}'>
        <span class="pdot"></span>${PLABEL[a.platform]}${a.auto_post ? "" : ' <span style="font-size:10px;opacity:.6">руч.</span>'}
      </div>`).join("");
    const s = b.stats;
    return `<div class="bundle">
      <div class="bundle-head">
        <div><div class="bundle-name">${esc(b.name)}</div>
          <span class="bundle-theme">${esc(niche ? niche.title : b.niche_id || "—")}</span></div>
        <div class="bundle-subs"><b>${s.subscribers.toLocaleString("ru")}</b><span>подписчиков</span></div>
      </div>
      <div class="platforms">${pills}</div>
      <div class="bundle-stats">
        <div class="bstat"><b>${s.posted.day}</b><span>за день</span></div>
        <div class="bstat"><b>${s.posted.week}</b><span>за неделю</span></div>
        <div class="bstat"><b>${s.posted.month}</b><span>за месяц</span></div>
        <div class="bstat"><b>${s.queued}</b><span>в очереди</span></div>
      </div>
      <div class="bundle-actions">
        <button class="btn primary sm" data-gen="${b.id}">+ Сгенерировать ролик</button>
        <button class="btn ghost sm danger" data-del-bundle="${b.id}" data-name="${esc(b.name)}">Удалить</button>
      </div>
    </div>`;
  }).join("");

  $$("[data-gen]", grid).forEach(btn => btn.onclick = () => generate(+btn.dataset.gen));
  $$("[data-del-bundle]", grid).forEach(btn => btn.onclick = async () => {
    if (!confirm(`Удалить связку «${btn.dataset.name}» со всем её контентом?`)) return;
    await api("/bundles/" + btn.dataset.delBundle, "DELETE"); toast("Связка удалена"); refresh();
  });
  $$(".pill", grid).forEach(p => p.onclick = () => openAccount(JSON.parse(p.dataset.acc)));
}

// ───── очередь ─────
function qItem(c) {
  const dur = c.duration ? `${Math.round(c.duration)}с · ` : "";
  const m = c.meta || {};
  const vir = m.virality && m.virality.score;
  const qaOk = m.qa ? m.qa.ok : null;            // null = QA не считался (старый контент)
  const badges =
    (vir != null ? `<span class="qbadge vir">🔥 ${vir}</span>` : "") +
    (qaOk === false ? `<span class="qbadge qa-bad" title="${esc((m.qa.issues || []).join('; '))}">⚠ QA не пройден</span>`
      : qaOk === true ? `<span class="qbadge qa-ok">✓ QA</span>` : "");
  const thumb = c.status === "generating"
    ? `<div class="qthumb placeholder"><span class="spinner"></span></div>`
    : (c.video_path ? `<video class="qthumb" src="/api/content/${c.id}/video#t=1" preload="metadata" muted controls></video>`
      : `<div class="qthumb placeholder">нет видео</div>`);
  const targets = Object.entries(c.targets || {}).map(([p, t]) =>
    `<span class="tg ${t.status}">${PLABEL[p]}: ${STATUS_RU[t.status] || t.status}</span>`).join("");
  let actions = "";
  if (c.status === "generating") actions = `<div class="status-badge status-generating"><span class="spinner"></span>генерация</div>`;
  else if (c.status === "failed") actions = `<div class="status-badge status-failed">ошибка</div><button class="btn ghost sm danger" data-del="${c.id}">Удалить</button>`;
  else {
    const ttPending = (c.targets?.tiktok?.status === "pending_manual");
    // QA-гейт: бракованный ролик не публикуем — только скачать/пересоздать/удалить
    const pubBtn = qaOk === false
      ? `<span class="status-badge status-failed" title="${esc((m.qa.issues || []).join('; '))}">⚠ публикация заблокирована (QA)</span>`
      : `<button class="btn primary sm" data-pub="${c.id}">Опубликовать (авто)</button>`;
    actions = `<div class="status-badge status-${c.status}">${STATUS_RU[c.status] || c.status}</div>
      ${pubBtn}
      <a class="btn ghost sm" href="/api/content/${c.id}/video" download>Скачать MP4</a>
      ${ttPending ? `<button class="btn ghost sm" data-tt="${c.id}">TikTok выложен ✓</button>` : ""}
      <button class="btn ghost sm danger" data-del="${c.id}">Удалить</button>`;
  }
  return `<div class="qitem">${thumb}
    <div class="qbody"><div class="qtopic">${esc(c.topic) || "—"} ${badges}</div>
      <div class="qmeta">${dur}связка #${c.bundle_id} · ${esc(c.error || "")}</div>
      <div class="qtargets">${targets}</div></div>
    <div class="qactions">${actions}</div></div>`;
}

async function renderQueue() {
  const bf = $("#queue-filter-bundle").value, sf = $("#queue-filter-status").value;
  let q = "/queue?";
  if (bf) q += "bundle_id=" + bf + "&";
  if (sf) q += "status=" + sf;
  const items = await api(q);
  const list = $("#queue-list");
  list.innerHTML = items.length ? items.map(qItem).join("")
    : `<div class="empty"><div class="big">≣</div>Очередь пуста.</div>`;
  bindQueueActions(list);
  // селект связок
  const sel = $("#queue-filter-bundle");
  if (sel.options.length <= 1) {
    const bundles = await api("/bundles");
    bundles.forEach(b => sel.add(new Option(b.name, b.id)));
  }
}

// ───── аналитика ─────
async function renderAnalytics() {
  const a = await api("/analytics");
  $("#an-stats").innerHTML = [
    ["Роликов с метриками", a.totals.videos, ""],
    ["Всего просмотров", (a.totals.views || 0).toLocaleString("ru"), "accent"],
    ["Снимков статистики", a.totals.snapshots, ""],
    ["Веса обновлены", a.updated ? a.updated.slice(0, 10) : "—", ""],
  ].map(([l, v, c]) => `<div class="stat ${c}"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");
  const t = $("#an-table");
  if (!a.niches.length) {
    t.innerHTML = `<div class="empty"><div class="big">📊</div>Данных пока нет.<br>Появятся после публикаций (нужны токены YouTube/VK).<br><span style="font-size:12px;color:var(--txt-mut)">Сбор — автоматически раз в день (scheduler).</span></div>`;
    return;
  }
  t.innerHTML = `<table class="an-table"><thead><tr>
      <th>Ниша</th><th>Роликов</th><th>Ср. просмотры</th><th>Всего</th><th>Прогноз вир.</th><th>Вес</th>
    </tr></thead><tbody>` + a.niches.map(n => `<tr>
      <td>${esc(n.niche)}</td><td>${n.videos}</td>
      <td><b>${n.avg_views.toLocaleString("ru")}</b></td>
      <td>${n.total_views.toLocaleString("ru")}</td>
      <td>${n.avg_virality ?? "—"}</td>
      <td><span class="wbar" style="--w:${Math.min(100, n.weight / 2 * 100)}%">${n.weight}</span></td>
    </tr>`).join("") + `</tbody></table>`;
}

function bindQueueActions(root) {
  $$("[data-pub]", root).forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = "Публикую…";
    try { await api(`/content/${b.dataset.pub}/publish`, "POST"); toast("Опубликовано (где подключено)"); }
    catch (e) { toast("Ошибка: " + e.message); }
    refresh();
  });
  $$("[data-tt]", root).forEach(b => b.onclick = async () => {
    await api(`/content/${b.dataset.tt}/mark_posted`, "POST", { platform: "tiktok" });
    toast("TikTok отмечен выложенным"); refresh();
  });
  $$("[data-del]", root).forEach(b => b.onclick = async () => {
    if (!confirm("Удалить контент?")) return;
    await api("/content/" + b.dataset.del, "DELETE"); toast("Удалено"); refresh();
  });
}

// ───── генерация ─────
async function generate(bundleId) {
  const topic = prompt("Тема ролика (оставь пустым — придумает AI):", "") ?? "";
  toast("Генерация запущена…");
  try { await api("/content/generate", "POST", { bundle_id: bundleId, topic: topic.trim() }); }
  catch (e) { toast("Ошибка: " + e.message); return; }
  switchView("queue");
}

// ───── модалки ─────
function openModal(id) { $(id).classList.remove("hidden"); }
function closeModals() { $$(".modal-overlay").forEach(m => m.classList.add("hidden")); }

function openAccount(acc) {
  state.editingAccount = acc.id;
  $("#ma-title").textContent = PLABEL[acc.platform] + " · настройка";
  $("#ma-name").value = acc.display_name || "";
  $("#ma-url").value = acc.url || "";
  $("#ma-secret").value = acc.secret_ref || "";
  $("#ma-ext").value = acc.ext_id || "";
  $("#ma-subs").value = acc.subscribers || 0;
  $("#ma-status").value = acc.status || "pending";
  $("#ma-kind").value = acc.kind || "video";
  $("#ma-auto").checked = !!acc.auto_post;
  openModal("#modal-account");
}

// ───── план дня ─────
function planSlot(it) {
  const gen = it.status === "generating";
  const kindLabel = it.kind === "text" ? "📝 текст" : "🎬 видео";
  let body = `<div class="slot-topic">${esc(it.topic) || "(тема не задана)"}</div>`;
  if (it.kind === "text" && it.text)
    body += `<div class="slot-text">${esc(it.text).slice(0, 300)}${it.text.length > 300 ? "…" : ""}</div>`;
  if (it.kind === "video" && it.content && it.content.video_path)
    body += `<video class="qthumb" src="/api/content/${it.content_id}/video#t=1" preload="metadata" muted controls></video>`;
  let actions = "";
  if (gen) actions = `<span class="status-badge status-generating"><span class="spinner"></span>генерация</span>`;
  else {
    actions = `<span class="status-badge status-${it.status}">${PLAN_STATUS[it.status] || it.status}</span>`;
    if (it.status === "planned") actions += `<button class="btn primary sm" data-pgen="${it.id}">Сгенерировать</button>`;
    if (it.status === "ready") {
      if (it.kind === "video" && it.content_id) actions += `<a class="btn ghost sm" href="/api/content/${it.content_id}/video" download>MP4</a>`;
      actions += `<button class="btn ghost sm" data-pmark="${it.id}">Выложено ✓</button>`;
    }
    actions += `<button class="btn ghost sm danger" data-pdel="${it.id}">✕</button>`;
  }
  return `<div class="slot ${it.kind}">
    <div class="slot-time">${it.slot_time}<span>${PLABEL[it.platform]}</span></div>
    <div class="slot-body"><div class="slot-kind">${kindLabel}${it.platform === "tiktok" ? " · ручной" : ""}</div>${body}</div>
    <div class="slot-actions">${actions}</div></div>`;
}

async function renderPlan() {
  const sel = $("#plan-bundle");
  const bundles = await api("/bundles");
  if (!bundles.length) {
    $("#plan-detail").innerHTML = `<div class="empty"><div class="big">⬡</div>Сначала создай связку во вкладке «Связки».</div>`;
    $("#plan-board").innerHTML = ""; return;
  }
  if (sel.options.length !== bundles.length) {
    sel.innerHTML = "";
    bundles.forEach(b => { const n = NICHES.find(x => x.id === b.niche_id); sel.add(new Option(`${b.name} · ${n ? n.title : b.niche_id}`, b.id)); });
  }
  if (!planState.bundleId || !bundles.find(b => b.id == planState.bundleId)) planState.bundleId = bundles[0].id;
  sel.value = planState.bundleId;
  if (!$("#plan-date").value) $("#plan-date").value = todayISO();
  planState.date = $("#plan-date").value;

  const bundle = bundles.find(b => b.id == planState.bundleId);
  const niche = NICHES.find(n => n.id === bundle.niche_id);
  const voice = niche ? (niche.engine === "xtts" ? `${niche.voice} (XTTS)` : niche.voice) : "—";
  $("#plan-detail").innerHTML = `<div class="plan-head">
    <div><div class="bundle-name">${esc(bundle.name)}</div>
      <span class="bundle-theme">${esc(niche ? niche.title : bundle.niche_id)}</span>
      <span class="voice-chip">🎙 ${esc(voice)}</span></div>
    <div class="plan-accs">${bundle.accounts.map(a =>
      `<span class="pill ${a.status}"><span class="pdot"></span>${PLABEL[a.platform]} · ${a.kind === "text" ? "текст" : "видео"}</span>`).join("")}</div>
  </div>`;

  const data = await api(`/bundles/${planState.bundleId}/plan?date=${planState.date}`);
  $("#plan-board").innerHTML = data.items.length
    ? data.items.map(planSlot).join("")
    : `<div class="empty"><div class="big">▤</div>На ${planState.date} плана нет.<br>Нажми «⚡ Построить план на день» — он соберёт темы и слоты по площадкам.<br><span style="font-size:12px;color:var(--txt-mut)">Позже это будет делать утренний парсер (крон 6:00).</span></div>`;
  bindPlanActions();
}

function bindPlanActions() {
  $$("[data-pgen]").forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = "Запуск…";
    await api(`/plan/${b.dataset.pgen}/generate`, "POST"); refresh();
  });
  $$("[data-pmark]").forEach(b => b.onclick = async () => {
    await api(`/plan/${b.dataset.pmark}/mark_posted`, "POST"); toast("Отмечено выложенным"); refresh();
  });
  $$("[data-pdel]").forEach(b => b.onclick = async () => {
    await api(`/plan/${b.dataset.pdel}`, "DELETE"); refresh();
  });
}

// ───── refresh / poll ─────
async function refresh() {
  try {
    if (state.view === "dashboard") await renderDashboard();
    else if (state.view === "plan") await renderPlan();
    else if (state.view === "bundles") await renderBundles();
    else if (state.view === "queue") await renderQueue();
    else if (state.view === "analytics") await renderAnalytics();
  } catch (e) { console.error(e); }
}

// ───── init ─────
async function init() {
  NICHES = await api("/niches");
  const nb = $("#nb-niche");
  NICHES.forEach(n => nb.add(new Option(`${n.title} [${n.lang}] · ${n.engine}`, n.id)));

  $$(".nav-item").forEach(b => b.onclick = () => switchView(b.dataset.view));
  $("#refresh-btn").onclick = refresh;
  $("#new-bundle-btn").onclick = () => openModal("#modal-bundle");
  $$("[data-close]").forEach(b => b.onclick = closeModals);
  $$(".modal-overlay").forEach(m => m.onclick = e => { if (e.target === m) closeModals(); });
  $("#queue-filter-bundle").onchange = renderQueue;
  $("#queue-filter-status").onchange = renderQueue;

  $("#plan-bundle").onchange = () => { planState.bundleId = +$("#plan-bundle").value; renderPlan(); };
  $("#plan-date").onchange = () => { planState.date = $("#plan-date").value; renderPlan(); };
  $("#plan-build").onclick = async () => {
    if (!planState.bundleId) return toast("Нет связки");
    const btn = $("#plan-build"); btn.disabled = true; btn.textContent = "Собираю план…";
    try { await api(`/bundles/${planState.bundleId}/plan/build`, "POST", { date: planState.date }); toast("План на день собран"); }
    catch (e) { toast("Ошибка: " + e.message); }
    btn.disabled = false; btn.textContent = "⚡ Построить план на день"; refresh();
  };
  $("#plan-genall").onclick = async () => {
    const data = await api(`/bundles/${planState.bundleId}/plan?date=${planState.date}`);
    const planned = data.items.filter(i => i.status === "planned");
    if (!planned.length) return toast("Нет слотов к генерации");
    toast(`Генерирую ${planned.length} слот(ов)…`);
    for (const it of planned) { try { await api(`/plan/${it.id}/generate`, "POST"); } catch (e) { /* skip */ } }
    refresh();
  };

  $("#nb-create").onclick = async () => {
    const name = $("#nb-name").value.trim();
    if (!name) return toast("Введи название");
    await api("/bundles", "POST", { name, niche_id: $("#nb-niche").value });
    closeModals(); $("#nb-name").value = ""; toast("Связка создана"); switchView("bundles");
  };
  $("#ma-save").onclick = async () => {
    await api("/accounts/" + state.editingAccount, "PATCH", {
      display_name: $("#ma-name").value, url: $("#ma-url").value,
      subscribers: +$("#ma-subs").value, status: $("#ma-status").value,
      kind: $("#ma-kind").value, auto_post: $("#ma-auto").checked ? 1 : 0,
      secret_ref: $("#ma-secret").value, ext_id: $("#ma-ext").value,
    });
    closeModals(); toast("Сохранено"); refresh();
  };

  switchView("dashboard");
  setInterval(() => { if (!document.hidden) refresh(); }, 6000); // авто-опрос (генерация → готово)
}
init();
