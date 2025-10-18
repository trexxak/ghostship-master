(() => {
  const SOUND_STORAGE_KEY = "forum-sfx-enabled";
  const body = document.body;
  let activeMentionHandle = null;
  const composerRegistry = [];

  const refreshButton = document.querySelector('[data-sw-refresh]');
  let pendingServiceWorker = null;

  function revealRefresh() {
    if (!refreshButton) {
      return;
    }
    refreshButton.hidden = false;
    refreshButton.classList.add('is-ready');
  }

  function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) {
      return;
    }
    const options = window.__swOptions || { path: '/service-worker.js', scope: '/' };
    navigator.serviceWorker.register(options.path, { scope: options.scope || '/' }).then((registration) => {
      if (registration.waiting) {
        pendingServiceWorker = registration.waiting;
        revealRefresh();
      }
      registration.addEventListener('updatefound', () => {
        const newWorker = registration.installing;
        if (!newWorker) {
          return;
        }
        newWorker.addEventListener('statechange', () => {
          if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
            pendingServiceWorker = newWorker;
            revealRefresh();
          }
        });
      });
    }).catch((error) => {
      console.warn('SW registration failed', error);
    });

    if (refreshButton) {
      refreshButton.addEventListener('click', () => {
        if (pendingServiceWorker) {
          pendingServiceWorker.postMessage({ type: 'SKIP_WAITING' });
        } else {
          window.location.reload();
        }
      });
    }

    navigator.serviceWorker.addEventListener('controllerchange', () => {
      window.location.reload();
    });
  }

  registerServiceWorker();

const Soundboard = {
    context: null,
    enabled: true,
    ensureContext() {
      if (!this.context) {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) {
          this.enabled = false;
          return null;
        }
        this.context = new AudioContext();
      }
      return this.context;
    },
    play(type = "mission") {
      if (!this.enabled) {
        return;
      }
      const ctx = this.ensureContext();
      if (!ctx) {
        return;
      }
      const now = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      const baseFrequency = type === "omen" ? 420 : type === "seance" ? 560 : 640;
      osc.type = type === "omen" ? "triangle" : "sine";
      osc.frequency.setValueAtTime(baseFrequency, now);
      osc.frequency.exponentialRampToValueAtTime(baseFrequency * 0.6, now + 0.8);

      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.linearRampToValueAtTime(0.06, now + 0.08);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 1.4);

      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + 1.5);
    },
  };

  function getCookie(name) {
    const cookies = document.cookie ? document.cookie.split(";") : [];
    for (let i = 0; i < cookies.length; i += 1) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith(`${name}=`)) {
        return decodeURIComponent(cookie.substring(name.length + 1));
      }
    }
    return "";
  }

  function getCsrfToken() {
    return getCookie("csrftoken");
  }

  function showToast(message, tone = "info") {
    const stack = document.querySelector("[data-ui-toast]");
    if (!stack || !message) {
      return;
    }
    const toast = document.createElement("div");
    toast.className = `ui-toast${tone ? ` ui-toast--${tone}` : ""}`;
    toast.textContent = message;
    stack.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("is-visible"));
    window.setTimeout(() => {
      toast.classList.remove("is-visible");
      toast.addEventListener("transitionend", () => toast.remove(), { once: true });
    }, 4200);
  }

  function smoothScrollTo(target) {
    let element = null;
    if (!target) {
      return;
    }
    if (typeof target === "string") {
      element = document.querySelector(target);
    } else if (target instanceof Element) {
      element = target;
    }
    if (!element) {
      return;
    }
    element.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function addEmojiToComposers(emoji, { silent = false } = {}) {
    let added = false;
    composerRegistry.forEach((entry) => {
      if (!entry || typeof entry.addEmoji !== "function") {
        return;
      }
      const result = entry.addEmoji(emoji, silent);
      if (result) {
        added = true;
      }
    });
    return added;
  }

  function syncSoundPreference(button) {
    const stored = window.localStorage.getItem(SOUND_STORAGE_KEY);
    const enabled = stored !== "off";
    Soundboard.enabled = enabled;
    if (button) {
      button.setAttribute("data-sound-state", enabled ? "on" : "off");
      button.querySelector(".sound-toggle__label").textContent = enabled ? "Chimes on" : "Chimes muted";
    }
  }

  function toggleSound(button) {
    Soundboard.enabled = !Soundboard.enabled;
    window.localStorage.setItem(SOUND_STORAGE_KEY, Soundboard.enabled ? "on" : "off");
    syncSoundPreference(button);
  }

  function triggerAmbientChimes() {
    document.querySelectorAll("[data-sfx]").forEach((el) => {
      if (el.dataset.sfxPlayed === "true") {
        return;
      }
      const tone = el.dataset.sfx || "mission";
      Soundboard.play(tone);
      el.dataset.sfxPlayed = "true";
    });
  }

  function parseJsonScript(id) {
    const script = document.getElementById(id);
    if (!script) {
      return [];
    }
    try {
      return JSON.parse(script.textContent || "[]");
    } catch (error) {
      // eslint-disable-next-line no-console
      console.debug("Unable to parse JSON script", id, error);
      return [];
    }
  }

  function renderAchievementToasts() {
    const container = document.querySelector("[data-toast-container]");
    if (!container) {
      return;
    }
    const toasts = parseJsonScript("progress-toasts-data");
    if (!Array.isArray(toasts) || toasts.length === 0) {
      return;
    }
    toasts.slice(0, 3).forEach((toast, index) => {
      if (toast && toast.emoji) {
        addEmojiToComposers(toast.emoji, { silent: true });
      }
      const node = document.createElement("article");
      node.className = "achievement-toast";
      node.setAttribute("role", "status");
      const targetThread = toast.thread_id ? `/threads/${toast.thread_id}/` : "";
      const targetFragment = toast.post_id ? `#post-${toast.post_id}` : "";
      const targetUrl = targetThread ? `${targetThread}${targetFragment}` : targetFragment;
      node.innerHTML = `
        <div class="achievement-toast-emoji">${toast.emoji || "🏆"}</div>
        <div class="achievement-toast-body">
          <h4>Hooray! ${toast.name}</h4>
          <p class="meta">Unlocked ${toast.unlocked_at ? new Date(toast.unlocked_at).toLocaleString() : "just now"}</p>
          ${targetUrl ? '<button type="button" data-toast-link>View highlight</button>' : ""}
        </div>
      `;
      if (targetUrl) {
        node.querySelector("[data-toast-link]").addEventListener("click", () => {
          window.location.href = targetUrl;
        });
      }
      container.appendChild(node);
      setTimeout(() => {
        node.classList.add("is-visible");
        if (Soundboard.enabled) {
          Soundboard.play("mission");
        }
      }, 120 * index);
      setTimeout(() => {
        node.classList.remove("is-visible");
        setTimeout(() => node.remove(), 320);
      }, 6000 + index * 600);
    });
  }

  function renderAchievementTicker() {
    const ticker = document.querySelector("[data-ticker]");
    if (!ticker) {
      return;
    }
    const itemsWrap = ticker.querySelector("[data-ticker-items]");
    const dismissButton = ticker.querySelector("[data-ticker-dismiss]");
    const entries = parseJsonScript("progress-ticker-data");
    if (!Array.isArray(entries) || entries.length === 0) {
      return;
    }
    itemsWrap.innerHTML = "";
    entries.slice(0, 4).forEach((item) => {
      const node = document.createElement("span");
      node.className = "ticker-item";
      node.innerHTML = `<span class="ticker-item-emoji">${item.emoji || "🌟"}</span><span>${item.agent || "trexxak"} unlocked ${item.name}</span>`;
      const targetThread = item.thread_id ? `/threads/${item.thread_id}/` : "";
      const targetFragment = item.post_id ? `#post-${item.post_id}` : "";
      const targetUrl = targetThread ? `${targetThread}${targetFragment}` : targetFragment;
      if (targetUrl) {
        node.dataset.tickerLink = targetUrl;
        node.tabIndex = 0;
        node.setAttribute("role", "button");
        node.addEventListener("click", () => {
          window.location.href = targetUrl;
        });
        node.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            window.location.href = targetUrl;
          }
        });
      }
      itemsWrap.appendChild(node);
    });

    const hideTicker = () => {
      ticker.classList.remove("is-visible");
      setTimeout(() => {
        ticker.hidden = true;
      }, 220);
    };

    ticker.hidden = false;
    requestAnimationFrame(() => ticker.classList.add("is-visible"));
    if (dismissButton) {
      dismissButton.addEventListener("click", hideTicker, { once: true });
    }
    setTimeout(hideTicker, 20000);
  }

  function renderAchievementBroadcasts() {
    const container = document.querySelector("[data-broadcast-container]");
    if (!container) {
      return;
    }
    const broadcasts = parseJsonScript("progress-broadcasts-data");
    if (!Array.isArray(broadcasts) || broadcasts.length === 0) {
      return;
    }

    const showBroadcast = (payload) => {
      const node = document.createElement("article");
      node.className = "achievement-broadcast";
      node.setAttribute("role", "status");
      const targetThread = payload.thread_id ? `/threads/${payload.thread_id}/` : "";
      const targetFragment = payload.post_id ? `#post-${payload.post_id}` : "";
      const targetUrl = targetThread ? `${targetThread}${targetFragment}` : targetFragment;
      node.innerHTML = `
        <div class="achievement-broadcast__halo"></div>
        <div class="achievement-broadcast__content">
          <div class="achievement-broadcast__emoji">${payload.emoji || "🌟"}</div>
          <div class="achievement-broadcast__copy">
            <p class="achievement-broadcast__title">${payload.name || "Milestone unlocked"}</p>
            <p class="achievement-broadcast__meta">${payload.agent || "trexxak"} just raised the ship-wide bar.</p>
          </div>
          ${targetUrl ? '<button type="button" class="achievement-broadcast__cta" data-broadcast-link>Witness it</button>' : ""}
        </div>
      `;
      if (targetUrl) {
        const link = node.querySelector("[data-broadcast-link]");
        link.addEventListener("click", (event) => {
          event.preventDefault();
          window.location.href = targetUrl;
        });
      }
      container.appendChild(node);
      requestAnimationFrame(() => node.classList.add("is-visible"));
      if (Soundboard.enabled) {
        Soundboard.play("seance");
      }
      const lifespan = 7200;
      setTimeout(() => {
        node.classList.remove("is-visible");
        setTimeout(() => node.remove(), 500);
      }, lifespan);
    };

    broadcasts.slice(0, 3).forEach((entry, index) => {
      const delay = index * 5200;
      setTimeout(() => showBroadcast(entry), delay);
    });
  }

  function setupThreadAutoUpdates() {
    const stream = document.querySelector("[data-thread-updates]");
    if (!stream) {
      return;
    }

    const threadId = Number(stream.dataset.threadId || "0");
    if (!threadId) {
      return;
    }

    let lastPostId = Number(stream.dataset.lastPostId || "0");
    const pollInterval = 12000;
    let fetching = false;

    const endpoint = stream.dataset.apiUrl || `/api/threads/${threadId}/updates/`;

    async function pollUpdates() {
      if (fetching) {
        return;
      }
      fetching = true;
      try {
        const response = await fetch(`${endpoint}?after=${lastPostId}`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        if (!payload || !Array.isArray(payload.html) || payload.html.length === 0) {
          let latestPostValue = lastPostId || 0;
          if (payload && payload.latest_post_id) {
            latestPostValue = payload.latest_post_id;
          }
          lastPostId = Number(latestPostValue);
          stream.dataset.lastPostId = String(lastPostId);
          return;
        }
        payload.html.forEach((html, index) => {
          stream.insertAdjacentHTML("beforeend", html);
          let postData = null;
          if (payload && Array.isArray(payload.posts)) {
            postData = payload.posts[index];
          }
          if (postData && postData.id) {
            const node = stream.querySelector(`#post-${postData.id}`);
            if (node) {
              node.classList.add("post-card--new");
              setTimeout(() => node.classList.remove("post-card--new"), 6000);
            }
          }
        });
        if (typeof window.__applyMentionHighlight === "function") {
          window.__applyMentionHighlight();
        }
        if (Soundboard.enabled) {
          Soundboard.play("mission");
        }
        lastPostId = Number(payload.latest_post_id || lastPostId || 0);
        stream.dataset.lastPostId = String(lastPostId);
      } catch (error) {
        // eslint-disable-next-line no-console
        console.debug("Thread update poll failed", error);
      } finally {
        fetching = false;
      }
    }

    const runPoll = () => {
      if (document.hidden) {
        return;
      }
      pollUpdates();
    };

    runPoll();
    setInterval(runPoll, pollInterval);
  }

  function setupMentionHighlighting() {
    const stream = document.querySelector("[data-thread-updates]");
    if (!stream) {
      window.__applyMentionHighlight = undefined;
      return;
    }

    const apply = () => {
      stream.querySelectorAll("[data-author-handle]").forEach((post) => {
        const handle = (post.dataset.authorHandle || "").toLowerCase();
        if (activeMentionHandle && handle === activeMentionHandle) {
          post.classList.add("post-card--highlight");
        } else {
          post.classList.remove("post-card--highlight");
        }
      });
      stream.querySelectorAll(".post-content .mention[data-handle]").forEach((mention) => {
        const handle = (mention.dataset.handle || "").toLowerCase();
        mention.setAttribute("role", "button");
        mention.setAttribute("tabindex", "0");
        const isActive = Boolean(activeMentionHandle && handle === activeMentionHandle);
        mention.setAttribute("aria-pressed", isActive ? "true" : "false");
        if (isActive) {
          mention.classList.add("mention--active");
        } else {
          mention.classList.remove("mention--active");
        }
      });
    };

    window.__applyMentionHighlight = apply;

    const toggleHandle = (handle) => {
      if (!handle) {
        return;
      }
      activeMentionHandle = activeMentionHandle === handle ? null : handle;
      apply();
    };

    stream.addEventListener("click", (event) => {
      const mention = event.target.closest(".post-content .mention[data-handle]");
      if (!mention) {
        return;
      }
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
        return;
      }
      event.preventDefault();
      const handle = (mention.dataset.handle || "").toLowerCase();
      if (!handle) {
        return;
      }
      toggleHandle(handle);
      mention.focus();
    });

    stream.addEventListener("keydown", (event) => {
      const mention = event.target.closest(".post-content .mention[data-handle]");
      if (!mention) {
        return;
      }
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      event.preventDefault();
      const handle = (mention.dataset.handle || "").toLowerCase();
      if (!handle) {
        return;
      }
      toggleHandle(handle);
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && activeMentionHandle) {
        activeMentionHandle = null;
        apply();
      }
    });

    apply();
  }

  const NOTIFICATION_POLL_INTERVAL = 15000;

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  function formatTimestamp(value) {
    if (!value) {
      return "";
    }
    try {
      return new Date(value).toLocaleString();
    } catch (error) {
      return "";
    }
  }

  function initNotifications() {
    if (document.body.dataset.oiActive !== "true") {
      return;
    }
    const notificationCenter = document.querySelector("[data-oi-notification]");
    const notificationBell = document.querySelector("[data-notification-toggle]");
    const notificationPanel = document.querySelector("[data-notification-panel]");
    const notificationList = document.querySelector("[data-notification-list]");
    const notificationCount = document.querySelector("[data-notification-count]");
    const notificationClear = document.querySelector("[data-notification-clear]");

    if (!notificationCenter || !notificationBell || !notificationPanel || !notificationList) {
      return;
    }

    let panelOpen = false;
    let pollingTimer = null;
    let inFlight = false;
    let previousCount = 0;

    const setCount = (value) => {
      if (!notificationCount) {
        return;
      }
      const count = Math.max(0, Number(value) || 0);
      if (count > 0) {
        notificationCount.textContent = String(count);
        notificationCount.hidden = false;
        notificationBell.classList.add("has-unread");
        if (count > previousCount) {
          Soundboard.play("mission");
        }
      } else {
        notificationCount.hidden = true;
        notificationBell.classList.remove("has-unread");
      }
      previousCount = count;
    };

    const render = (items) => {
      notificationList.innerHTML = "";
      if (!Array.isArray(items) || items.length === 0) {
        const empty = document.createElement("p");
        empty.className = "notification-empty";
        empty.textContent = "No notifications yet.";
        notificationList.appendChild(empty);
        return;
      }
      items.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "notification-item";
        button.innerHTML = `
          <span class="notification-title">${escapeHtml(item.message || "Update")}</span>
          <span class="notification-meta">${formatTimestamp(item.created)}</span>
        `;
        if (item.preview) {
          const previewSpan = document.createElement("span");
          previewSpan.className = "notification-preview";
          previewSpan.textContent = item.preview;
          button.appendChild(previewSpan);
        }
        button.addEventListener("click", () => {
          closePanel();
          if (item.url) {
            window.location.href = item.url;
          }
        });
        notificationList.appendChild(button);
      });
    };

    const fetchNotifications = async (ack = false) => {
      if (inFlight) {
        return;
      }
      inFlight = true;
      const params = new URLSearchParams();
      if (ack) {
        params.set("ack", "1");
      }
      params.set("t", String(Date.now()));
      try {
        const response = await fetch(`/api/notifications/?${params.toString()}`, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        render(payload.notifications || []);
        setCount(payload.unread || 0);
      } catch (error) {
        // eslint-disable-next-line no-console
        console.debug("notifications poll failed", error);
      } finally {
        inFlight = false;
      }
    };

    const openPanel = () => {
      if (panelOpen) {
        return;
      }
      panelOpen = true;
      notificationPanel.hidden = false;
      notificationPanel.classList.add("is-open");
      notificationBell.setAttribute("aria-expanded", "true");
      fetchNotifications(true);
    };

    const closePanel = () => {
      if (!panelOpen) {
        return;
      }
      panelOpen = false;
      notificationPanel.hidden = true;
      notificationPanel.classList.remove("is-open");
      notificationBell.setAttribute("aria-expanded", "false");
    };

    const togglePanel = () => {
      if (panelOpen) {
        closePanel();
      } else {
        openPanel();
      }
    };

    notificationBell.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      togglePanel();
    });

    if (notificationClear) {
      notificationClear.addEventListener("click", (event) => {
        event.preventDefault();
        fetchNotifications(true);
        closePanel();
      });
    }

    document.addEventListener("click", (event) => {
      if (!panelOpen) {
        return;
      }
      if (!notificationCenter.contains(event.target)) {
        closePanel();
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && panelOpen) {
        closePanel();
      }
    });

    document.addEventListener("visibilitychange", () => {
      if (!panelOpen && !document.hidden) {
        fetchNotifications(false);
      }
    });

    if (!pollingTimer) {
      pollingTimer = window.setInterval(() => {
        if (!panelOpen && !document.hidden) {
          fetchNotifications(false);
        }
      }, NOTIFICATION_POLL_INTERVAL);
    }

    fetchNotifications(false);
  }

  function collectUnlockedEmoji() {
    const seen = new Set();
    const list = [];
    document.querySelectorAll(".achievement-card.unlocked[data-emoji]").forEach((card) => {
      const symbol = (card.dataset.emoji || "").trim();
      if (symbol && !seen.has(symbol)) {
        seen.add(symbol);
        list.push(symbol);
      }
    });
    return list.slice(0, 12);
  }

  function initEmojiPins() {
    const triggers = document.querySelectorAll("[data-emoji-pick]");
    if (!triggers.length) {
      return;
    }
    triggers.forEach((trigger) => {
      trigger.addEventListener("click", (event) => {
        event.preventDefault();
        const symbol = (trigger.dataset.emojiPick || trigger.dataset.emoji || trigger.textContent || "").trim();
        if (!symbol) {
          return;
        }
        const added = addEmojiToComposers(symbol, { silent: true });
        showToast(
          added
            ? `${symbol} auto-docked for you. Check the composer!`
            : `${symbol} is already waiting in the dock.`,
          added ? "success" : "info",
        );
      });
    });
  }

  function initMailThreads() {
    const lists = document.querySelectorAll("[data-mail-thread-list]");
    if (!lists.length) {
      return;
    }

    lists.forEach((list) => {
      const threads = Array.from(list.querySelectorAll("[data-mail-thread]"));
      threads.forEach((thread) => {
        const toggle = thread.querySelector("[data-mail-thread-toggle]");
        const panel = thread.querySelector("[data-mail-thread-panel]");
        if (!toggle || !panel) {
          return;
        }

        const setExpanded = (expanded) => {
          panel.hidden = !expanded;
          thread.classList.toggle("is-open", expanded);
          toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        };

        setExpanded(!panel.hidden);

        toggle.addEventListener("click", (event) => {
          event.preventDefault();
          const expanded = toggle.getAttribute("aria-expanded") === "true";
          setExpanded(!expanded);
        });
      });
    });
  }

  function initDmComposer() {
    const rawOptions = parseJsonScript("dm-recipient-options");
    if (!Array.isArray(rawOptions) || rawOptions.length === 0) {
      return;
    }

    const normalizeHandle = (value) => {
      return String(value || "")
        .trim()
        .replace(/^@+/, "")
        .replace(/\s+/g, " ")
        .toLowerCase();
    };

    const options = rawOptions
      .map((item) => {
        const name = String(item && item.name ? item.name : "").trim();
        const search = normalizeHandle(name);
        if (!name || !search) {
          return null;
        }
        const metaParts = [];
        if (item && item.archetype) {
          metaParts.push(String(item.archetype));
        }
        if (item && item.role) {
          const role = String(item.role);
          if (role && role !== "member") {
            metaParts.push(role.replace(/_/g, " "));
          }
        }
        return {
          id: item && typeof item.id === "number" ? item.id : Number(item.id),
          name,
          search,
          meta: metaParts.join(" · "),
        };
      })
      .filter((entry) => entry && Number.isFinite(entry.id));

    if (!options.length) {
      return;
    }

    const lookup = new Map();
    options.forEach((option) => {
      lookup.set(option.search, option);
    });

    const hideSuggestions = (suggestionBox, input) => {
      suggestionBox.innerHTML = "";
      suggestionBox.hidden = true;
      input.setAttribute("aria-expanded", "false");
    };

    document.querySelectorAll("[data-dm-composer]").forEach((form) => {
      if (form.dataset.dmComposerReady === "true") {
        return;
      }
      const field = form.querySelector("[data-dm-recipient-field]");
      if (!field) {
        return;
      }

      const errorNode = form.querySelector("[data-dm-recipient-error]");
      const fieldName = field.getAttribute("name") || "to";
      const initialValue = field.value || "";

      const wrapper = document.createElement("div");
      wrapper.className = "dm-recipient-combobox";

      const chipContainer = document.createElement("div");
      chipContainer.className = "dm-recipient-chips";
      chipContainer.setAttribute("role", "list");

      const input = document.createElement("input");
      input.type = "text";
      input.className = "dm-recipient-input";
      input.placeholder = field.getAttribute("placeholder") || "Add recipients…";
      input.setAttribute("autocomplete", "off");
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "false");
      input.setAttribute("aria-haspopup", "listbox");

      const suggestionBox = document.createElement("div");
      suggestionBox.className = "dm-recipient-suggestions";
      suggestionBox.hidden = true;
      suggestionBox.setAttribute("role", "listbox");

      wrapper.appendChild(chipContainer);
      wrapper.appendChild(input);
      wrapper.appendChild(suggestionBox);

      const hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = fieldName;
      form.insertBefore(hidden, field);
      field.after(wrapper);
      field.removeAttribute("name");
      field.value = "";
      field.hidden = true;
      field.disabled = true;

      form.dataset.dmComposerReady = "true";

      const selected = [];
      const selectedIds = new Set();
      let matches = [];
      let activeIndex = -1;

      const clearError = () => {
        if (errorNode) {
          errorNode.textContent = "";
          errorNode.hidden = true;
        }
        wrapper.classList.remove("has-error");
      };

      const showError = (message) => {
        if (!errorNode) {
          return;
        }
        errorNode.textContent = message || "";
        errorNode.hidden = !message;
        wrapper.classList.toggle("has-error", Boolean(message));
      };

      const updateHidden = () => {
        hidden.value = selected.map((option) => option.name).join(", ");
      };

      const clearRecipients = () => {
        selected.splice(0, selected.length);
        selectedIds.clear();
        chipContainer.innerHTML = "";
        updateHidden();
      };

      const removeRecipient = (id) => {
        const index = selected.findIndex((option) => option.id === id);
        if (index === -1) {
          return;
        }
        selected.splice(index, 1);
        selectedIds.delete(id);
        const chip = chipContainer.querySelector(`[data-id="${id}"]`);
        if (chip) {
          chip.remove();
        }
        updateHidden();
      };

      const addRecipient = (option, { silent = false } = {}) => {
        if (!option || selectedIds.has(option.id)) {
          return false;
        }
        selected.push(option);
        selectedIds.add(option.id);

        const chip = document.createElement("span");
        chip.className = "dm-recipient-chip";
        chip.dataset.id = String(option.id);

        const label = document.createElement("span");
        label.textContent = `@${option.name}`;
        chip.appendChild(label);

        const removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.setAttribute("aria-label", `Remove ${option.name}`);
        removeButton.innerHTML = "&times;";
        removeButton.addEventListener("click", (event) => {
          event.preventDefault();
          removeRecipient(option.id);
          input.focus();
        });
        chip.appendChild(removeButton);

        chipContainer.appendChild(chip);
        updateHidden();
        if (!silent) {
          clearError();
        }
        return true;
      };

      const findOptionByHandle = (handle) => {
        if (!handle) {
          return null;
        }
        return lookup.get(normalizeHandle(handle));
      };

      const setRecipients = (handles) => {
        if (!Array.isArray(handles) || !handles.length) {
          return false;
        }
        const optionsToApply = handles
          .map((handle) => findOptionByHandle(handle))
          .filter((option) => option && Number.isFinite(option.id));
        if (!optionsToApply.length) {
          return false;
        }
        clearRecipients();
        optionsToApply.forEach((option) => {
          addRecipient(option, { silent: true });
        });
        updateHidden();
        clearError();
        return true;
      };

      const addRecipientByHandle = (handle) => {
        const option = findOptionByHandle(handle);
        if (!option) {
          return false;
        }
        const added = addRecipient(option);
        if (added) {
          updateHidden();
        }
        return added;
      };

      const setActiveIndex = (index) => {
        activeIndex = index;
        const nodes = suggestionBox.querySelectorAll(".dm-recipient-suggestion");
        nodes.forEach((node, idx) => {
          if (idx === activeIndex) {
            node.classList.add("is-active");
            node.setAttribute("aria-selected", "true");
          } else {
            node.classList.remove("is-active");
            node.setAttribute("aria-selected", "false");
          }
        });
      };

      const renderSuggestions = (term) => {
        const query = normalizeHandle(term);
        matches = options
          .filter((option) => {
            if (selectedIds.has(option.id)) {
              return false;
            }
            if (!query) {
              return true;
            }
            return option.search.includes(query);
          })
          .slice(0, 8);

        suggestionBox.innerHTML = "";
        if (!matches.length) {
          hideSuggestions(suggestionBox, input);
          activeIndex = -1;
          return;
        }

        matches.forEach((option, index) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "dm-recipient-suggestion";
          button.dataset.id = String(option.id);
          button.setAttribute("role", "option");
          button.innerHTML = `<span>@${escapeHtml(option.name)}</span>`;
          if (option.meta) {
            button.innerHTML += `<span class="dm-recipient-meta">${escapeHtml(option.meta)}</span>`;
          }
          button.addEventListener("click", (event) => {
            event.preventDefault();
            addRecipient(option);
            input.value = "";
            hideSuggestions(suggestionBox, input);
            input.focus();
          });
          suggestionBox.appendChild(button);
          if (index === 0) {
            button.classList.add("is-active");
            button.setAttribute("aria-selected", "true");
            activeIndex = 0;
          }
        });

        suggestionBox.hidden = false;
        input.setAttribute("aria-expanded", "true");
      };

      const commitMatches = (text, { allowPartial = false } = {}) => {
        const raw = String(text || "");
        const segments = raw
          .split(",")
          .map((segment) => segment.trim())
          .filter((segment) => segment);

        if (!segments.length) {
          return true;
        }

        let ok = true;
        segments.forEach((segment) => {
          if (!ok) {
            return;
          }
          const normalized = normalizeHandle(segment);
          let option = lookup.get(normalized);
          if (!option) {
            option = matches.find((candidate) => candidate.search.startsWith(normalized));
          }
          if (!option) {
            option = options.find(
              (candidate) => !selectedIds.has(candidate.id) && candidate.search.startsWith(normalized),
            );
          }
          if (option) {
            addRecipient(option);
          } else if (!allowPartial) {
            ok = false;
            showError(`No ghost registered as ${segment}.`);
          }
        });

        if (ok) {
          input.value = "";
          renderSuggestions("");
          clearError();
        }

        return ok;
      };

      input.addEventListener("input", () => {
        clearError();
        renderSuggestions(input.value);
      });

      input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
          if (matches.length) {
            event.preventDefault();
            const next = activeIndex + 1 >= matches.length ? 0 : activeIndex + 1;
            setActiveIndex(next);
          }
          return;
        }
        if (event.key === "ArrowUp") {
          if (matches.length) {
            event.preventDefault();
            const next = activeIndex - 1 < 0 ? matches.length - 1 : activeIndex - 1;
            setActiveIndex(next);
          }
          return;
        }
        if (event.key === "Enter") {
          if (matches.length && activeIndex >= 0 && matches[activeIndex]) {
            event.preventDefault();
            addRecipient(matches[activeIndex]);
            input.value = "";
            hideSuggestions(suggestionBox, input);
            return;
          }
          if (input.value.trim()) {
            if (!commitMatches(input.value)) {
              event.preventDefault();
            } else {
              event.preventDefault();
            }
          }
          return;
        }
        if (event.key === "," || event.key === "Tab") {
          if (matches.length && activeIndex >= 0 && matches[activeIndex]) {
            event.preventDefault();
            addRecipient(matches[activeIndex]);
            input.value = "";
            hideSuggestions(suggestionBox, input);
            return;
          }
          if (input.value.trim()) {
            if (!commitMatches(input.value, { allowPartial: event.key === "Tab" })) {
              event.preventDefault();
            } else {
              event.preventDefault();
            }
          }
          return;
        }
        if (event.key === "Backspace" && !input.value) {
          const last = selected[selected.length - 1];
          if (last) {
            removeRecipient(last.id);
          }
          return;
        }
        if (event.key === "Escape") {
          hideSuggestions(suggestionBox, input);
          clearError();
        }
      });

      input.addEventListener("focus", () => {
        renderSuggestions(input.value);
      });

      document.addEventListener("click", (event) => {
        if (!wrapper.contains(event.target)) {
          hideSuggestions(suggestionBox, input);
        }
      });

      form.addEventListener("submit", (event) => {
        clearError();
        if (input.value && !commitMatches(input.value)) {
          event.preventDefault();
          input.focus();
          return;
        }
        if (!selected.length) {
          showError("Add at least one recipient.");
          event.preventDefault();
          input.focus();
        }
      });

      form.addEventListener("dm:setRecipients", (event) => {
        const detail = event.detail || {};
        if (detail && Array.isArray(detail.handles)) {
          setRecipients(detail.handles);
        }
      });

      form.dmComposerApi = {
        setRecipients,
        addRecipient: addRecipientByHandle,
        clearRecipients,
        focusInput: () => input.focus(),
        focusBody: () => {
          const bodyField = form.querySelector("[data-dm-body]");
          if (bodyField) {
            bodyField.focus();
          } else {
            input.focus();
          }
        },
      };

      if (initialValue) {
        const bootstrapSegments = initialValue.split(",");
        bootstrapSegments.forEach((segment) => {
          const normalized = normalizeHandle(segment);
          const option = lookup.get(normalized);
          if (option) {
            addRecipient(option, { silent: true });
          }
        });
        updateHidden();
      }
    });
  }


  function initMailReplyTriggers() {
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-mail-reply]");
      if (!trigger) {
        return;
      }
      const handle = trigger.dataset.mailReply;
      if (!handle) {
        return;
      }
      const composer = document.querySelector("[data-dm-composer]");
      if (!composer) {
        return;
      }
      event.preventDefault();

      const composeShell = document.querySelector("[data-mail-compose]");
      if (composeShell && composeShell.tagName === "DETAILS") {
        composeShell.open = true;
        if (typeof composeShell.scrollIntoView === "function") {
          composeShell.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }

      if (composer.dmComposerApi && typeof composer.dmComposerApi.setRecipients === "function") {
        composer.dmComposerApi.setRecipients([handle]);
      } else {
        composer.dispatchEvent(new CustomEvent("dm:setRecipients", { detail: { handles: [handle] } }));
      }

      if (composer.dmComposerApi && typeof composer.dmComposerApi.focusBody === "function") {
        composer.dmComposerApi.focusBody();
      } else {
        const bodyField = composer.querySelector("[data-dm-body]") || composer.querySelector("textarea[name='body']");
        if (bodyField) {
          bodyField.focus();
        }
      }
    });
  }

  function initSelectSearch() {
    document.querySelectorAll("[data-select-search]").forEach((input) => {
      const targetSelector = input.dataset.selectSearch;
      const select = targetSelector ? document.querySelector(targetSelector) : null;
      const listId = input.getAttribute("list");
      const dataList = listId ? document.getElementById(listId) : null;
      if (!select || !dataList) {
        return;
      }
      const syncFromSelect = () => {
        const selectedOption = select.selectedOptions ? select.selectedOptions[0] : null;
        if (selectedOption && !input.value) {
          input.value = selectedOption.textContent || selectedOption.value;
        }
      };

      syncFromSelect();

      const options = Array.from(dataList.options).map((option) => ({
        label: (option.value || "").trim(),
        value: option.dataset.id || option.value,
        search: (option.value || "").trim().toLowerCase(),
      }));

      const updateSelection = (term) => {
        const value = term.trim().toLowerCase();
        if (!value) {
          select.value = "";
          return;
        }
        let match = options.find((item) => item.search.startsWith(value));
        if (!match) {
          match = options.find((item) => item.search.includes(value));
        }
        if (match) {
          select.value = match.value;
        } else {
          select.value = "";
        }
      };

      input.addEventListener("input", () => {
        updateSelection(input.value || "");
      });

      input.addEventListener("change", () => {
        updateSelection(input.value || "");
      });
    });
  }

  function initScrollLinks() {
    document.addEventListener("click", (event) => {
      const trigger = event.target.closest("[data-scroll-link]");
      if (!trigger) {
        return;
      }
      const target = trigger.dataset.scrollLink || trigger.getAttribute("href");
      if (!target || !target.startsWith("#")) {
        return;
      }
      event.preventDefault();
      smoothScrollTo(target);
    });
  }

  function initPaginationControls() {
    document.querySelectorAll("[data-pagination]").forEach((pager) => {
      const current = Number(pager.dataset.pageCurrent || "1");
      const total = Number(pager.dataset.pageTotal || "1");
      const anchor = pager.dataset.paginationTarget || "";
      const pageParam = pager.dataset.pageParam || "page";

      const navigate = (page) => {
        if (!Number.isFinite(page) || page < 1) {
          return;
        }
        const clamped = Math.max(1, Math.min(total || 1, page));
        const url = new URL(window.location.href);
        if (clamped === 1) {
          url.searchParams.delete(pageParam);
        } else {
          url.searchParams.set(pageParam, String(clamped));
        }
        const params = url.searchParams.toString();
        const destination = `${url.pathname}${params ? `?${params}` : ""}${anchor}`;
        window.location.href = destination;
      };

      pager.querySelectorAll("[data-page]").forEach((control) => {
        control.addEventListener("click", (event) => {
          if (control.classList.contains("is-disabled")) {
            event.preventDefault();
            return;
          }
          const target = control.dataset.page;
          if (!target) {
            return;
          }
          event.preventDefault();
          if (target === "first") {
            navigate(1);
          } else if (target === "prev") {
            navigate(current - 1);
          } else if (target === "next") {
            navigate(current + 1);
          } else if (target === "last") {
            navigate(total);
          } else {
            navigate(Number(target));
          }
        });
      });

      const select = pager.querySelector("[data-pagination-select]");
      if (select) {
        select.addEventListener("change", () => {
          const selected = Number(select.value || current);
          navigate(selected);
        });
      }
    });
  }

  function initCollapseControls() {
    document.querySelectorAll("[data-collapse]").forEach((group) => {
      const panel = group.querySelector("[data-collapse-panel]");
      const toggle = group.querySelector("[data-collapse-toggle]");
      if (!panel || !toggle) {
        return;
      }
      const storageKey = group.dataset.collapseKey ? `collapse:${group.dataset.collapseKey}` : null;
      const storedState = storageKey ? window.localStorage.getItem(storageKey) : null;
      const defaultState = storedState ? storedState !== "closed" : (group.dataset.collapseDefault || "open" ).toLowerCase() !== "closed";
      const labelOpen = toggle.dataset.labelOpen || toggle.textContent.trim() || "Collapse";
      const labelClosed = toggle.dataset.labelClosed || "Expand";

      const setExpandedState = (expanded) => {
        panel.hidden = !expanded;
        panel.classList.toggle("is-open", expanded);
        group.classList.toggle("is-open", expanded);
        toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
        toggle.textContent = expanded ? labelOpen : labelClosed;
        if (storageKey) {
          try {
            window.localStorage.setItem(storageKey, expanded ? "open" : "closed");
          } catch (error) {
            // ignore storage errors (private browsing etc.)
          }
        }
      };

      group.classList.add("collapse-initialized");
      setExpandedState(defaultState);

      toggle.addEventListener("click", (event) => {
        event.preventDefault();
        const expanded = panel.hidden === false;
        setExpandedState(!expanded);
      });
    });
  }

  function initModals() {
    const modals = document.querySelectorAll("[data-modal]");
    if (!modals.length) {
      return;
    }

    modals.forEach((modal) => {
      if (!modal.hasAttribute("hidden")) {
        modal.hidden = true;
      }
      modal.classList.remove("is-visible");
    });

    const closeModal = (modal) => {
      if (!modal) {
        return;
      }
      modal.classList.remove("is-visible");
      modal.addEventListener("transitionend", () => {
        modal.hidden = true;
      }, { once: true });
      body.classList.remove("modal-open");
    };

    const openModal = (id) => {
      if (!id) {
        return;
      }
      const modal = document.getElementById(id);
      if (!modal) {
        return;
      }
      modal.hidden = false;
      requestAnimationFrame(() => modal.classList.add("is-visible"));
      body.classList.add("modal-open");
    };

    document.addEventListener("click", (event) => {
      const opener = event.target.closest("[data-modal-open]");
      if (opener) {
        event.preventDefault();
        openModal(opener.dataset.modalOpen);
        return;
      }
      const closer = event.target.closest("[data-modal-close]");
      if (closer) {
        event.preventDefault();
        const modal = closer.closest("[data-modal]");
        closeModal(modal);
      }
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const modal = document.querySelector("[data-modal].is-visible");
        if (modal) {
          closeModal(modal);
        }
      }
    });
  }

  function initComposers() {
    const roots = document.querySelectorAll("[data-editor-root]");
    composerRegistry.length = 0;
    if (!roots.length) {
      return;
    }

    const unlockedEmoji = collectUnlockedEmoji();
    const viewerIsAdmin = document.body && document.body.dataset.viewerAdmin === "true";
    if (!unlockedEmoji.length && viewerIsAdmin) {
      unlockedEmoji.push("💬");
    }
    const cssEscape = (value) => {
      if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
        return CSS.escape(value);
      }
      return value.replace(/[\s]/g, "\\$&");
    };

    roots.forEach((root) => {
      const textarea = root.querySelector("[data-editor-textarea]") || root.querySelector("textarea");
      if (!textarea) {
        return;
      }
      const preview = root.querySelector("[data-editor-preview]");
      const previewBody = root.querySelector("[data-editor-preview-body]");
      const toggleButtons = root.querySelectorAll("[data-editor-toggle-preview]");
      const closeButton = root.querySelector("[data-editor-close-preview]");
      const toolbar = root.querySelector("[data-editor-toolbar]");
      const emojiDock = root.querySelector("[data-emoji-dock]");
      const emojiContainer = root.querySelector("[data-emoji-container]");
      const emojiPlaceholder = emojiDock ? emojiDock.querySelector(".emoji-placeholder") : null;
      const previewEndpoint = root.dataset.previewEndpoint || "";

      if (preview) {
        preview.hidden = true;
        preview.classList.remove("is-open");
      }

      let previewOpen = false;
      let previewTimer = null;
      const emojiSet = new Set();
      const maxEmoji = 12;

      const updatePlaceholder = () => {
        if (!emojiPlaceholder) {
          return;
        }
        emojiPlaceholder.hidden = emojiSet.size > 0;
      };

      const renderPreview = async () => {
        if (!preview || !previewBody) {
          return;
        }
        const content = (textarea.value || "").trim();
        if (!content) {
          previewBody.innerHTML = "<p class=\"preview-empty\">Nothing to preview yet.</p>";
          return;
        }
        previewBody.innerHTML = "<p class=\"preview-loading\">Rendering…</p>";
        if (!previewEndpoint) {
          const escaped = escapeHtml(content).split(/\n{2,}/).map((chunk) => `<p>${chunk.replace(/\n/g, "<br>")}</p>`).join("");
          previewBody.innerHTML = escaped || "<p class=\"preview-empty\">Nothing to preview yet.</p>";
          return;
        }
        try {
          const response = await fetch(previewEndpoint, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
              "X-CSRFToken": getCsrfToken(),
              Accept: "application/json",
            },
            body: new URLSearchParams({ content }),
            credentials: "same-origin",
          });
          if (!response.ok) {
            throw new Error(`Preview failed with status ${response.status}`);
          }
          const data = await response.json();
          const html = data && data.html ? data.html : "";
          previewBody.innerHTML = html || "<p class=\"preview-empty\">Nothing to preview yet.</p>";
        } catch (error) {
          previewBody.innerHTML = "<p class=\"preview-empty\">Preview unavailable. Try again.</p>";
          showToast("Preview failed to render. Try again shortly.", "warning");
        }
      };

      const schedulePreview = () => {
        if (!previewOpen) {
          return;
        }
        if (previewTimer) {
          window.clearTimeout(previewTimer);
        }
        previewTimer = window.setTimeout(() => {
          renderPreview();
        }, 320);
      };

      const openPreview = () => {
        previewOpen = true;
        if (preview) {
          preview.hidden = false;
          preview.classList.add("is-open");
        }
        renderPreview();
      };

      const closePreview = () => {
        previewOpen = false;
        if (preview) {
          preview.hidden = true;
          preview.classList.remove("is-open");
        }
      };

      toggleButtons.forEach((button) => {
        button.addEventListener("click", (event) => {
          event.preventDefault();
          if (previewOpen) {
            closePreview();
          } else {
            openPreview();
          }
        });
      });

      if (closeButton) {
        closeButton.addEventListener("click", (event) => {
          event.preventDefault();
          closePreview();
        });
      }

      const wrapSelection = (before, after, placeholder) => {
        const start = textarea.selectionStart ?? textarea.value.length;
        const end = textarea.selectionEnd ?? start;
        const value = textarea.value;
        const selected = value.slice(start, end);
        const insert = selected || placeholder;
        textarea.value = value.slice(0, start) + before + insert + after + value.slice(end);
        const cursorStart = start + before.length;
        const cursorEnd = cursorStart + insert.length;
        textarea.setSelectionRange(cursorStart, cursorEnd);
        textarea.focus();
        schedulePreview();
      };

      const prefixLines = (prefix, placeholder) => {
        const start = textarea.selectionStart ?? textarea.value.length;
        const end = textarea.selectionEnd ?? start;
        const value = textarea.value;
        const selection = value.slice(start, end) || placeholder;
        const transformed = selection.split(/\n/).map((line) => `${prefix}${line}`).join("\n");
        textarea.value = value.slice(0, start) + transformed + value.slice(end);
        const cursorStart = start;
        const cursorEnd = start + transformed.length;
        textarea.setSelectionRange(cursorStart, cursorEnd);
        textarea.focus();
        schedulePreview();
      };

      const applyFormat = (action) => {
        switch (action) {
          case "bold":
            wrapSelection("**", "**", "bold text");
            break;
          case "italic":
            wrapSelection("_", "_", "italic text");
            break;
          case "code": {
            const selection = textarea.value.slice(textarea.selectionStart ?? 0, textarea.selectionEnd ?? 0);
            if (selection.includes("\n")) {
              wrapSelection("```\n", "\n```", selection || "code block");
            } else {
              wrapSelection("`", "`", selection || "code");
            }
            break;
          }
          case "quote":
            prefixLines("> ", "quoted text");
            break;
          case "list":
            prefixLines("- ", "list item");
            break;
          default:
            break;
        }
      };

      if (toolbar) {
        toolbar.addEventListener("click", (event) => {
          const button = event.target.closest("[data-editor-action]");
          if (!button) {
            return;
          }
          event.preventDefault();
          applyFormat(button.dataset.editorAction);
        });
      }

      textarea.addEventListener("keydown", (event) => {
        if (!(event.metaKey || event.ctrlKey)) {
          return;
        }
        const key = event.key.toLowerCase();
        if (key === "b") {
          event.preventDefault();
          applyFormat("bold");
        } else if (key === "i") {
          event.preventDefault();
          applyFormat("italic");
        } else if (key === "q") {
          event.preventDefault();
          applyFormat("quote");
        }
      });

      textarea.addEventListener("input", schedulePreview);

      const insertEmoji = (symbol) => {
        const start = textarea.selectionStart ?? textarea.value.length;
        const end = textarea.selectionEnd ?? start;
        const value = textarea.value;
        textarea.value = value.slice(0, start) + symbol + value.slice(end);
        const cursor = start + symbol.length;
        textarea.setSelectionRange(cursor, cursor);
        textarea.focus();
        schedulePreview();
      };

      const addEmoji = (emoji, silent = false) => {
        const symbol = (emoji || "").trim();
        if (!symbol) {
          return false;
        }
        if (emojiSet.has(symbol)) {
          if (!silent && emojiContainer) {
            const existing = emojiContainer.querySelector(`button[data-emoji="${cssEscape(symbol)}"]`);
            if (existing) {
              existing.classList.add("is-highlight");
              existing.addEventListener("animationend", () => existing.classList.remove("is-highlight"), { once: true });
            }
          }
          return false;
        }
        if (emojiSet.size >= maxEmoji) {
          const first = emojiContainer ? emojiContainer.querySelector("button") : null;
          if (first) {
            emojiSet.delete(first.dataset.emoji || "");
            first.remove();
          }
        }
        emojiSet.add(symbol);
        if (emojiContainer) {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = symbol;
          button.dataset.emoji = symbol;
          button.addEventListener("click", (event) => {
            event.preventDefault();
            insertEmoji(symbol);
          });
          emojiContainer.appendChild(button);
        }
        updatePlaceholder();
        if (!silent) {
          showToast(`${symbol} added to the dock.`, "success");
        }
        return true;
      };

      unlockedEmoji.forEach((emoji) => addEmoji(emoji, true));
      updatePlaceholder();

      composerRegistry.push({ addEmoji });
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    requestAnimationFrame(() => body.classList.add("ui-ready"));

    const soundToggle = document.querySelector("[data-sound-toggle]");
    if (soundToggle) {
      syncSoundPreference(soundToggle);
      soundToggle.addEventListener("click", () => toggleSound(soundToggle));
    } else {
      syncSoundPreference(null);
    }

    // triggerAmbientChimes();
    setupThreadAutoUpdates();
    setupMentionHighlighting();
    initNotifications();
    initComposers();
    renderAchievementToasts();
    renderAchievementTicker();
    renderAchievementBroadcasts();
    const metricsDelta = parseJsonScript("progress-metrics-delta");
    if (metricsDelta && !Array.isArray(metricsDelta) && Object.keys(metricsDelta).length > 0) {
      window.dispatchEvent(new CustomEvent("metrics:update", { detail: metricsDelta }));
    }
    initCollapseControls();
    initPaginationControls();
    initScrollLinks();
    initModals();
    initEmojiPins();
    initMailThreads();
    initDmComposer();
    initMailReplyTriggers();
    initSelectSearch();
  });
})();
