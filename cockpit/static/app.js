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
        .then(d => { pending.innerHTML = d.answer_html || esc(d.answer || ""); if (d.tool_calls && d.tool_calls.length) { const t = document.createElement("div"); t.className = "toolcalls"; t.textContent = "Werkzeuge: " + d.tool_calls.map(x => x.name).join(", "); pending.appendChild(t); } })
        .catch(err => { pending.innerHTML = "<span class='err'>Fehler: " + esc(String(err)) + "</span>"; })
        .finally(() => { btn.disabled = false; ta.focus(); });
    });
    ta.addEventListener("keydown", function (e) { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") form.requestSubmit(); });
  };
})();
