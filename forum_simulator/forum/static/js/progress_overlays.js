(function () {
  const KEY = "gs_metrics_v1";
  const DEFAULTS = { threads: 0, replies: 0, reports: 0 };
  const DEFAULT_MILESTONES = {
    threads: [1, 3, 5, 10, 20],
    replies: [5, 10, 20, 40, 80],
    reports: [1, 3, 5, 10]
  };

  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  function readStore() {
    try {
      const raw = sessionStorage.getItem(KEY);
      if (!raw) return { ...DEFAULTS };
      const parsed = JSON.parse(raw);
      return { ...DEFAULTS, ...parsed };
    } catch (e) {
      return { ...DEFAULTS };
    }
  }
  function writeStore(obj) {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(obj));
    } catch (e) { /* ignore */ }
  }

  function nextMilestone(list, value) {
    for (let i = 0; i < list.length; i++) {
      if (value < list[i]) return list[i];
    }
    return null;
  }

  function findHitMilestone(list, valueBefore, valueAfter) {
    // find the smallest milestone m with valueBefore < m <= valueAfter
    for (let i = 0; i < list.length; i++) {
      const m = list[i];
      if (valueBefore < m && valueAfter >= m) return m;
    }
    return null;
  }

  function fmtDelta(obj) {
    const parts = [];
    if (obj.threads) parts.push(`+${obj.threads} thread${obj.threads===1?"":"s"}`);
    if (obj.replies) parts.push(`+${obj.replies} repl${obj.replies===1?"y":"ies"}`);
    if (obj.reports) parts.push(`+${obj.reports} report${obj.reports===1?"":"s"}`);
    return parts.join(" • ");
  }

  function confettiBurst(root) {
    // lightweight burst: 24 circles animating up
    const canvas = document.createElement("canvas");
    canvas.width = root.clientWidth;
    canvas.height = root.clientHeight;
    canvas.className = "gs-confetti";
    root.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    const pieces = Array.from({ length: 24 }).map(() => ({
      x: Math.random() * canvas.width,
      y: canvas.height + Math.random() * 20,
      r: 2 + Math.random() * 4,
      vx: (Math.random() - 0.5) * 2,
      vy: - (2 + Math.random() * 3),
      a: 1.0
    }));
    let t = 0;
    function tick() {
      t++;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      pieces.forEach(p => {
        p.x += p.vx;
        p.y += p.vy;
        p.vy += 0.04; // gravity
        p.a -= 0.01;
        ctx.globalAlpha = Math.max(0, p.a);
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = "#fff"; // colorless; themed via blend
        ctx.fill();
      });
      ctx.globalAlpha = 1;
      if (t < 180) requestAnimationFrame(tick);
      else canvas.remove();
    }
    requestAnimationFrame(tick);
  }

  function ensureDOM() {
    let hud = document.querySelector(".gs-progress-hud");
    if (hud) return hud;
    const tpl = document.getElementById("gs-progress-overlay-template");
    if (tpl) {
      document.body.insertAdjacentHTML("beforeend", tpl.innerHTML);
      return document.querySelector(".gs-progress-hud");
    }
    // fallback if template missing
    const div = document.createElement("div");
    div.className = "gs-progress-hud";
    div.innerHTML = `
      <div class="gs-progress-toast" aria-live="polite"></div>
      <div class="gs-progressbar">
        <div class="gs-progressbar__fill"></div>
        <div class="gs-progressbar__label"></div>
      </div>`;
    document.body.appendChild(div);
    return div;
  }

  function setProgress(hud, percent, label) {
    const bar = hud.querySelector(".gs-progressbar__fill");
    const lab = hud.querySelector(".gs-progressbar__label");
    if (bar) bar.style.width = clamp(percent, 0, 100) + "%";
    if (lab) lab.textContent = label || "";
    hud.classList.add("is-flash");
    window.setTimeout(() => hud.classList.remove("is-flash"), 400);
    hud.classList.add("is-visible");
    window.setTimeout(() => hud.classList.remove("is-visible"), 2400);
  }

  function showToast(hud, text, milestoneHit) {
    const node = hud.querySelector(".gs-progress-toast");
    if (!node) return;
    node.textContent = text;
    node.classList.remove("is-pop", "is-achievement");
    // force reflow to retrigger animation
    void node.offsetWidth;
    if (milestoneHit) node.classList.add("is-achievement");
    node.classList.add("is-pop");
  }

  function updateBar(hud, store, milestones) {
    // choose the "nearest" next milestone across categories for the label
    let nearestCat = null, nearestNext = null, distance = Infinity, cur = 0;
    for (const cat of ["threads", "replies", "reports"]) {
      const v = store[cat] || 0;
      const list = milestones[cat] || [];
      const next = nextMilestone(list, v);
      if (next) {
        const d = next - v;
        if (d < distance) {
          distance = d; nearestCat = cat; nearestNext = next; cur = v;
        }
      }
    }
    if (nearestNext === null) {
      setProgress(hud, 100, "Maxed out — keep flexing ✨");
      return;
    }
    const pct = Math.round((cur / nearestNext) * 100);
    const label = `${nearestCat}: ${cur} / ${nearestNext}`;
    setProgress(hud, pct, label);
  }

  const ProgressHUD = {
    _milestones: DEFAULT_MILESTONES,
    _opts: { flashMs: 1200, toastMs: 2400 },
    init(opts = {}) {
      this._milestones = { ...DEFAULT_MILESTONES, ...(opts.milestones || {}) };
      this._opts = { ...this._opts, ...opts };
      this._hud = ensureDOM();
      // boot with current state
      this._store = readStore();
      updateBar(this._hud, this._store, this._milestones);
      // global event
      window.addEventListener("metrics:update", (ev) => {
        const detail = (ev && ev.detail) || {};
        this.bump(detail);
      });
      // expose for manual use
      window.ProgressHUD = this;
      return this;
    },
    bump(delta = {}) {
      const before = readStore();
      const after = { ...before };
      ["threads", "replies", "reports"].forEach(k => {
        const inc = parseInt(delta[k] || 0, 10);
        if (!isFinite(inc) || inc === 0) return;
        after[k] = Math.max(0, (after[k] || 0) + inc);
      });
      writeStore(after);
      this._store = after;
      const hud = this._hud || ensureDOM();
      // compute milestone hits
      let hitText = null;
      for (const cat of ["threads", "replies", "reports"]) {
        const m = findHitMilestone(this._milestones[cat] || [], before[cat] || 0, after[cat] || 0);
        if (m) {
          hitText = `Achievement unlocked — ${cat} ${m}!`;
        }
      }
      showToast(hud, hitText || fmtDelta(delta), !!hitText);
      if (hitText) confettiBurst(hud);
      updateBar(hud, after, this._milestones);
    },
    reset() {
      writeStore({ ...DEFAULTS });
      this._store = { ...DEFAULTS };
      updateBar(this._hud || ensureDOM(), this._store, this._milestones);
    }
  };

  // auto-init if template is present
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (document.getElementById("gs-progress-overlay-template")) ProgressHUD.init({});
    });
  } else {
    if (document.getElementById("gs-progress-overlay-template")) ProgressHUD.init({});
  }
})();