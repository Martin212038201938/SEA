/* SEA-Cockpit: kleine Helfer für Charts und Chat (kein Framework). */
(function () {
  const C = { blau: "#313E72", tuerkis: "#67C7EF", rot: "#CF2E2E", grau: "#9aa5bf" };

  function fmtEur(v) { return v == null ? "–" : v.toLocaleString("de-DE", { style: "currency", currency: "EUR" }); }

  window.seaChart = function (canvasId, series, opts) {
    if (!window.Chart) return;
    const el = document.getElementById(canvasId);
    if (!el || !series || !series.length) return;
    const labels = series.map(d => d.date);
    const datasets = [
      { label: "Kosten (€)", data: series.map(d => d.cost), borderColor: C.blau, backgroundColor: C.blau, yAxisID: "y", tension: .25, pointRadius: 0, borderWidth: 2 },
      { label: "Klicks", data: series.map(d => d.clicks), borderColor: C.tuerkis, backgroundColor: C.tuerkis, yAxisID: "y1", tension: .25, pointRadius: 0, borderWidth: 2 },
      { label: "Conversions", data: series.map(d => d.conversions), type: "bar", backgroundColor: C.rot, yAxisID: "y1", maxBarThickness: 10 },
    ];
    if (opts && opts.marker) {
      // vertikale Markierung (z. B. Experimentstart) als Dataset
      const maxCost = Math.max(...series.map(d => d.cost || 0));
      datasets.push({ label: opts.markerLabel || "Start", data: series.map(d => d.date === opts.marker ? maxCost : null), type: "bar", backgroundColor: "rgba(207,46,46,.25)", yAxisID: "y", maxBarThickness: 4 });
    }
    new Chart(el, {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true, interaction: { mode: "index", intersect: false },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } }, tooltip: { callbacks: { label: ctx => ctx.dataset.label.startsWith("Kosten") ? ctx.dataset.label + ": " + fmtEur(ctx.parsed.y) : ctx.dataset.label + ": " + ctx.parsed.y } } },
        scales: {
          x: { ticks: { maxTicksLimit: 12, color: "#6b7591" }, grid: { display: false } },
          y: { position: "left", ticks: { color: "#6b7591", callback: v => v + " €" }, grid: { color: "#eef1f7" } },
          y1: { position: "right", ticks: { color: "#6b7591" }, grid: { display: false }, beginAtZero: true },
        },
      },
    });
  };

  window.seaLoadTimeseries = function (canvasId, params) {
    const q = new URLSearchParams(params || {}).toString();
    fetch("/api/timeseries?" + q, { credentials: "same-origin" }).then(r => r.json()).then(data => window.seaChart(canvasId, data));
  };

  // Auswahlkarten (Frage + Optionen) unter einer Antwort rendern; Klick sendet die Option als Nachricht
  function renderChoices(container, cards, send) {
    (cards || []).forEach(card => {
      const box = document.createElement("div");
      box.className = "choices";
      if (card.question) { const q = document.createElement("div"); q.className = "choices-q"; q.textContent = card.question; box.appendChild(q); }
      card.options.forEach(opt => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "choice";
        b.innerHTML = "<span class='choice-label'></span>" + (opt.description ? "<span class='choice-desc'></span>" : "");
        b.querySelector(".choice-label").textContent = opt.label;
        if (opt.description) b.querySelector(".choice-desc").textContent = opt.description;
        b.addEventListener("click", () => {
          box.querySelectorAll(".choice").forEach(x => { x.disabled = true; x.classList.toggle("chosen", x === b); });
          send(card.question ? card.question + " → " + opt.label : opt.label);
        });
        box.appendChild(b);
      });
      container.appendChild(box);
    });
  }

  // Chat
  window.seaChat = function (formId, logId, thread) {
    const form = document.getElementById(formId), log = document.getElementById(logId);
    if (!form) return;
    const ta = form.querySelector("textarea"), btn = form.querySelector("button");
    function add(role, html, tools) {
      const div = document.createElement("div");
      div.className = "msg " + role;
      div.innerHTML = html;
      if (tools && tools.length) {
        const t = document.createElement("div");
        t.className = "toolcalls";
        t.textContent = "Werkzeuge: " + tools.map(x => x.name).join(", ");
        div.appendChild(t);
      }
      log.appendChild(div);
      window.scrollTo(0, document.body.scrollHeight);
      return div;
    }
    function esc(s) { return s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      const q = ta.value.trim();
      if (!q) return;
      add("user", esc(q).replace(/\n/g, "<br>"));
      ta.value = ""; btn.disabled = true;
      const pending = add("assistant", "<span class='muted'>Analysiere …</span>");
      fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ question: q, thread }) })
        .then(r => r.json())
        .then(d => { pending.innerHTML = d.answer_html || esc(d.answer || ""); if (d.tool_calls && d.tool_calls.length) { const t = document.createElement("div"); t.className = "toolcalls"; t.textContent = "Werkzeuge: " + d.tool_calls.map(x => x.name).join(", "); pending.appendChild(t); } renderChoices(pending, d.choices, q2 => { ta.value = q2; form.requestSubmit(); }); })
        .catch(err => { pending.innerHTML = "<span class='err'>Fehler: " + esc(String(err)) + "</span>"; })
        .finally(() => { btn.disabled = false; ta.focus(); });
    });
    ta.addEventListener("keydown", function (e) { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") form.requestSubmit(); });
  };

  // Suchbegriffs-Triage: Entscheidung per Klick, Zeile wird markiert (kein Reload)
  window.seaTriage = function (tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;
    table.addEventListener("click", e => {
      const btn = e.target.closest("button[data-decision]");
      if (!btn) return;
      const tr = btn.closest("tr"), cell = btn.closest(".triage-cell");
      const payload = { platform: tr.dataset.platform, search_term: tr.dataset.term, decision: btn.dataset.decision, negative_match: btn.dataset.match || "phrase" };
      cell.querySelectorAll("button").forEach(b => b.disabled = true);
      fetch("/api/suchbegriffe/entscheidung", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify(payload) })
        .then(r => r.json())
        .then(d => {
          let badge = cell.querySelector(".badge");
          if (!badge) { badge = document.createElement("span"); cell.insertBefore(badge, cell.firstChild); }
          const dec = d.decision.decision;
          badge.className = "badge " + (dec === "ausschliessen" ? "kritisch" : dec === "behalten" ? "umgesetzt" : "warn");
          badge.textContent = dec + (dec === "ausschliessen" ? " (" + d.decision.negative_match + ")" : "");
          tr.classList.add("decided");
        })
        .catch(() => alert("Speichern fehlgeschlagen"))
        .finally(() => cell.querySelectorAll("button").forEach(b => b.disabled = false));
    });
  };

  // Analyst-Panel: Chat rechts auf jeder Seite, schickt die offene Seite (Pfad + Filter) als Kontext mit.
  function seaPanel() {
    const panel = document.getElementById("sea-panel");
    if (!panel) return;
    document.body.classList.add("has-panel");
    const user = panel.dataset.user;
    const log = document.getElementById("panel-log"), form = document.getElementById("panel-form");
    const ta = form.querySelector("textarea"), btn = form.querySelector("button[type=submit]");
    const ctxEl = document.getElementById("panel-ctx");
    const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} } };
    let thread = store.get("sea-panel-thread", "panel-" + user);
    if (!thread.startsWith("panel-" + user)) thread = "panel-" + user;

    function esc(s) { return String(s).replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])); }
    function add(role, html) {
      const div = document.createElement("div");
      div.className = "msg " + role;
      div.innerHTML = html;
      log.appendChild(div);
      log.scrollTop = log.scrollHeight;
      return div;
    }
    function pageInfo() {
      const query = {};
      new URLSearchParams(location.search).forEach((v, k) => { query[k] = v; });
      return { path: location.pathname, query };
    }
    function loadHistory() {
      fetch("/api/chat/history?thread=" + encodeURIComponent(thread), { credentials: "same-origin" })
        .then(r => r.json())
        .then(d => {
          (d.messages || []).forEach((m, i, arr) => {
            const div = add(m.role, m.role === "assistant" ? m.html : esc(m.text).replace(/\n/g, "<br>"));
            if (m.role === "assistant" && i === arr.length - 1) renderChoices(div, m.choices, send);  // nur die letzte Karte bleibt klickbar
          });
          log.scrollTop = log.scrollHeight;
        }).catch(() => {});
    }
    function send(q) {
      if (!q) return;
      add("user", esc(q).replace(/\n/g, "<br>"));
      ta.value = ""; btn.disabled = true;
      const pending = add("assistant", "<span class='muted'>Schaue mir die Daten an …</span>");
      fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ question: q, thread, page: pageInfo() }) })
        .then(r => r.json())
        .then(d => {
          pending.innerHTML = d.answer_html || esc(d.answer || "");
          const meta = [];
          if (d.tool_calls && d.tool_calls.length) meta.push("Werkzeuge: " + d.tool_calls.map(x => x.name).join(", "));
          if (d.model) meta.push(d.model);
          if (meta.length) { const t = document.createElement("div"); t.className = "model"; t.textContent = meta.join(" · "); pending.appendChild(t); }
          renderChoices(pending, d.choices, send);
          log.scrollTop = log.scrollHeight;
        })
        .catch(err => { pending.innerHTML = "<span class='err'>Fehler: " + esc(String(err)) + "</span>"; })
        .finally(() => { btn.disabled = false; ta.focus(); });
    }

    const title = document.querySelector("main h1");
    if (ctxEl) ctxEl.textContent = "Kontext: " + (title ? title.textContent.trim() : location.pathname);
    form.addEventListener("submit", e => { e.preventDefault(); send(ta.value.trim()); });
    ta.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") form.requestSubmit(); });
    document.getElementById("panel-chips").addEventListener("click", e => { const b = e.target.closest("button[data-q]"); if (b) send(b.dataset.q); });
    document.getElementById("panel-new").addEventListener("click", () => {
      thread = "panel-" + user + "-" + Date.now().toString(36);
      store.set("sea-panel-thread", thread);
      if (big) big.href = "/chat?thread=" + encodeURIComponent(thread);
      log.innerHTML = "";
      add("assistant", "<span class='muted small'>Neues Gespräch. Der Kontext ist weiterhin die Seite, die du gerade offen hast.</span>");
    });
    const big = document.getElementById("panel-big");
    if (big) big.href = "/chat?thread=" + encodeURIComponent(thread);
    loadHistory();
  }
  document.addEventListener("DOMContentLoaded", seaPanel);
})();
