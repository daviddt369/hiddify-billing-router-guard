/* "Состояние нод" card for the V2 dashboard: where the exits (DE / NL ...) answer or not. Plain JS next to the compiled single page app:
   it waits for the dashboard to be drawn, puts one card at the top of it and refreshes every 30 s. Data: GET <api base>nodes-status/ (super admin). */
(function () {
  var API = (window.__API_BASE__ || "") + "nodes-status/";
  var COLORS = { ok: "#22c55e", partial: "#f59e0b", down: "#ef4444", unknown: "#94a3b8", off: "#64748b" };
  var TEXT = { ok: "Доступна", partial: "Частично", down: "Недоступна", unknown: "Нет данных", off: "Выключена" };
  var NODE_TEXT = { ok: "работает", down: "не отвечает", unknown: "нет данных", off: "выключена" };

  function css() {
    if (document.getElementById("nodes-status-css")) return;
    var s = document.createElement("style");
    s.id = "nodes-status-css";
    s.textContent =
      "#nodes-status-card{margin-bottom:14px;padding:14px 17.5px;font-family:inherit}" +
      "#nodes-status-card .ns-head{display:flex;align-items:center;gap:.75rem;margin-bottom:.75rem}" +
      "#nodes-status-card .ns-title{font-weight:700;font-size:14px}" +
      "#nodes-status-card .ns-muted{opacity:.7;font-size:11px;font-weight:500}" +
      "#nodes-status-card .ns-btn{margin-left:auto;cursor:pointer;border:1px solid rgba(148,163,184,.4);background:transparent;color:inherit;border-radius:6px;padding:5px 9px;opacity:.8;font-size:12px;font-family:inherit}" +
      "#nodes-status-card .ns-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:.75rem}" +
      "#nodes-status-card .ns-node{border:1px solid rgba(148,163,184,.25);border-radius:10px;padding:12px 14px}" +
      "#nodes-status-card .ns-top{display:flex;align-items:center;gap:.5rem}" +
      "#nodes-status-card .ns-dot{width:.7rem;height:.7rem;border-radius:50%;flex:none}" +
      "#nodes-status-card .ns-name{font-weight:700;font-size:14px}" +
      "#nodes-status-card .ns-state{margin-left:auto;font-size:12px;font-weight:700}" +
      "#nodes-status-card .ns-ping{font-size:13px;font-weight:500;margin:6px 0 2px}" +
      "#nodes-status-card .ns-chips{display:flex;flex-wrap:wrap;gap:.3rem;margin-top:.4rem}" +
      "#nodes-status-card .ns-chip{font-size:11px;font-weight:500;border-radius:999px;padding:1px 9px;border:1px solid;white-space:nowrap}";
    document.head.appendChild(s);
  }

  function esc(t) { var d = document.createElement("div"); d.textContent = t == null ? "" : String(t); return d.innerHTML; }

  function chip(n) {
    var c = COLORS[n.state] || COLORS.unknown;
    var tip = (NODE_TEXT[n.state] || "") + (n.ping_ms != null ? ", " + n.ping_ms + " мс" : "") + (n.error ? " · " + n.error : "") + (n.stale ? " · данные устарели" : "");
    return '<span class="ns-chip" style="color:' + c + ";border-color:" + c + '" title="' + esc(tip) + '">' + esc(n.name) + "</span>";
  }

  function render(card, d) {
    var html = '<div class="ns-head"><span class="ns-title">Состояние нод</span><span class="ns-muted">обновлено ' + new Date(d.generated_at || Date.now()).toLocaleTimeString() +
      '</span><button class="ns-btn" type="button">Обновить</button></div>';
    if (d.error) html += '<div class="ns-muted">Не удалось получить данные: ' + esc(d.error) + "</div>";
    html += '<div class="ns-grid">';
    (d.groups || []).forEach(function (g) {
      var c = COLORS[g.state] || COLORS.unknown;
      var hosts = {};
      (g.nodes || []).forEach(function (n) { if (n.host) hosts[n.host] = 1; });
      html += '<div class="ns-node"><div class="ns-top"><span class="ns-dot" style="background:' + c + '"></span><span class="ns-name">' + esc(g.title) + '</span><span class="ns-state" style="color:' + c + '">' +
        (TEXT[g.state] || "") + "</span></div>" +
        '<div class="ns-ping">' + (g.ping_ms != null ? "пинг " + g.ping_ms + " мс" : "пинг —") + " · " + g.ok + " из " + g.total + " транспортов</div>" +
        '<div class="ns-muted">' + esc(Object.keys(hosts).join(", ")) + "</div>" +
        '<div class="ns-chips">' + (g.nodes || []).map(chip).join("") + "</div></div>";
    });
    if ((d.unassigned || []).length) {
      html += '<div class="ns-node"><div class="ns-top"><span class="ns-dot" style="background:' + COLORS.unknown + '"></span><span class="ns-name">Без страны</span></div><div class="ns-chips">' + d.unassigned.map(chip).join("") + "</div></div>";
    }
    html += "</div>";
    card.innerHTML = html;
    var b = card.querySelector(".ns-btn");
    if (b) b.onclick = function () { load(card); };
  }

  function load(card) {
    fetch(API, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) { render(card, d); })
      .catch(function (e) { card.innerHTML = '<div class="ns-muted">Состояние нод: нет ответа (' + esc(e) + ")</div>"; });
  }

  function ensure() {
    var dash = document.querySelector(".dashboard");
    if (!dash || document.getElementById("nodes-status-card")) return;
    css();
    var card = document.createElement("article");
    card.id = "nodes-status-card";
    card.className = "tile";
    card.innerHTML = '<div class="ns-muted">Состояние нод: загрузка…</div>';
    dash.insertBefore(card, dash.firstChild);
    load(card);
  }

  new MutationObserver(ensure).observe(document.body, { childList: true, subtree: true });
  setInterval(function () { var c = document.getElementById("nodes-status-card"); if (c) load(c); }, 30000);
})();
