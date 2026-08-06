(() => {
  const state = {
    email: localStorage.getItem("aio_email") || "",
    apiKey: localStorage.getItem("aio_key") || "",
    userId: Number(localStorage.getItem("aio_uid") || 0) || null,
    chatId: null,
    lastMsgId: 0,
    timer: null,
    mediaStream: null,
    recorder: null,
    chunks: [],
    recording: false,
    members: [],
    chats: [],
    tab: "chat",
    projectId: Number(localStorage.getItem("aio_project") || 0) || 1,
    projects: [],
    isOwner: false,
    unreadMentions: 0,
    mentionRows: [],
    boardCards: [],
  };

  // Distinct side-rail colors per person (stable by email)
  const MEMBER_COLORS = [
    "#5b9fd4", // blue
    "#d4a05b", // amber
    "#c75b8a", // rose
    "#5bc4a8", // teal
    "#9b7bd4", // violet
    "#d47a5b", // coral
    "#7bb05b", // olive
    "#5b8ad4", // indigo
  ];
  const AGENT_COLORS = {
    lead: "#6a6",
    research: "#4a9",
    writing: "#a84",
    coding: "#4af",
    code: "#4af",
    code_review: "#48a",
    review: "#48a",
    checklist: "#a6a",
  };

  const $ = (id) => document.getElementById(id);

  function hashStr(s) {
    let h = 0;
    const str = String(s || "");
    for (let i = 0; i < str.length; i++) h = ((h << 5) - h) + str.charCodeAt(i) | 0;
    return Math.abs(h);
  }

  function colorForMember(email) {
    if (!email) return "#555";
    // Prefer stable index from members list order when known
    const idx = state.members.findIndex((m) => m.email === email);
    if (idx >= 0) return MEMBER_COLORS[idx % MEMBER_COLORS.length];
    return MEMBER_COLORS[hashStr(email) % MEMBER_COLORS.length];
  }

  function colorForMessage(m) {
    if (m.agent) {
      const key = String(m.agent).toLowerCase();
      return AGENT_COLORS[key] || "#6a6";
    }
    return colorForMember(m.sender_email || m.sender);
  }

  function headers(json = true) {
    const h = { "X-API-Key": state.apiKey };
    if (state.email) h["X-User-Email"] = state.email;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { ...headers(!(opts.body instanceof FormData)), ...(opts.headers || {}) },
    });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = text; }
    if (!res.ok) throw new Error(typeof data === "object" ? (data.detail || JSON.stringify(data)) : data);
    return data;
  }

  function setVoiceStatus(msg) {
    $("voiceStatus").textContent = msg || "";
  }

  function syncMicUi() {
    const on = $("voiceToggle").checked;
    $("micBtn").disabled = !on;
    if (!on && state.recording) stopRecording(false);
  }

  function showApp(on) {
    $("login").classList.toggle("hidden", on);
    $("app").classList.toggle("hidden", !on);
  }

  async function login() {
    $("loginErr").textContent = "";
    const email = $("email").value.trim();
    const password = $("password").value;
    try {
      const data = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      }).then(async (r) => {
        const j = await r.json();
        if (!r.ok) throw new Error(j.detail || "login failed");
        return j;
      });
      state.email = data.email;
      state.apiKey = data.api_key;
      state.userId = data.user_id;
      localStorage.setItem("aio_email", state.email);
      localStorage.setItem("aio_key", state.apiKey);
      localStorage.setItem("aio_uid", String(state.userId));
      $("who").textContent = `${data.name} <${data.email}>`;
      showApp(true);
      await refreshSidebar();
      await refreshMentions();
      if (state.chatId) await selectChat(state.chatId);
    } catch (e) {
      localStorage.removeItem("aio_email");
      localStorage.removeItem("aio_key");
      localStorage.removeItem("aio_uid");
      state.apiKey = "";
      showApp(false);
      $("loginErr").textContent =
        String(e.message || e) +
        "\nUse email + password (demo: a@local.test / demo).";
      $("password").value = "demo";
    }
  }

  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll("#mainTabs .tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === tab);
    });
    $("chatView").classList.toggle("hidden", tab !== "chat");
    $("boardView").classList.toggle("hidden", tab !== "board");
    $("modelsView").classList.toggle("hidden", tab !== "models");
    $("analyticsView").classList.toggle("hidden", tab !== "analytics");
    if (tab === "board") void loadBoard();
    if (tab === "models") void loadModels();
    if (tab === "analytics") void loadAnalytics();
  }

  const AGENT_LABELS = {
    research: "Research",
    writing: "Writing",
    coding: "Code",
    code_review: "Review",
    checklist: "Checklist",
  };

  // Dry coworker notes - no callsigns / units / marketing bios
  const AGENT_PROFILES = {
    research: {
      mention: "/research",
      job: "looks things up",
      line: "I keep digging until the answer stops wiggling. If I can’t find it, I say that out loud.",
      accent: "#4a9",
    },
    writing: {
      mention: "/write",
      job: "writes it down",
      line: "I cut fluff first. Goal is something a tired teammate can skim once.",
      accent: "#a84",
    },
    coding: {
      mention: "/code",
      job: "builds it",
      line: "Smallest change that runs. I won’t rewrite your whole file unless you ask.",
      accent: "#4af",
    },
    code_review: {
      mention: "/review",
      job: "checks the diff",
      line: "I read the patch like it’s already live. Secrets and sharp edges get called early.",
      accent: "#48a",
    },
    checklist: {
      mention: "/checklist",
      job: "breaks work into ticks",
      line: "I turn a foggy ask into numbered boxes. Boring on purpose.",
      accent: "#a6a",
    },
  };

  async function loadModels() {
    const data = await api("/workspace/agent-models");
    const hint = $("modelsHint");
    if (!data.openrouter_configured && !data.opencode_configured) {
      hint.textContent =
        "Set OPENROUTER_API_KEY for free models. Gemini (.env) still works.";
    } else if (data.openrouter_configured) {
      hint.textContent = "Who answers which @mention, and which model they use.";
    } else {
      hint.textContent = "OpenCode key set (may need billing). Prefer OpenRouter free models.";
    }
    if (!data.github_configured) {
      hint.textContent += " GITHUB_TOKEN optional for PRs.";
    }
    const form = $("modelsForm");
    form.innerHTML = "";
    (data.agents || Object.keys(AGENT_PROFILES)).forEach((agent) => {
      const profile = AGENT_PROFILES[agent] || {
        mention: `@${AGENT_LABELS[agent] || agent}`,
        job: agent,
        line: "",
        accent: AGENT_COLORS[agent] || "#666",
      };

      const row = document.createElement("div");
      row.className = "roster-row";
      row.style.setProperty("--agent-accent", profile.accent);

      const who = document.createElement("div");
      who.className = "roster-who";
      who.innerHTML = `
        <p class="mention">${profile.mention}</p>
        <p class="line">${profile.line}</p>
        <span class="job">${profile.job}</span>
      `;

      const brain = document.createElement("div");
      brain.className = "roster-model";
      const lab = document.createElement("label");
      lab.textContent = "model";
      lab.htmlFor = `model-${agent}`;
      const sel = document.createElement("select");
      sel.id = `model-${agent}`;
      sel.dataset.agent = agent;
      (data.models || []).forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.free ? `${m.label} - free` : m.label;
        if ((data.prefs || {})[agent] === m.id) opt.selected = true;
        sel.appendChild(opt);
      });
      const pref = (data.prefs || {})[agent];
      if (pref && ![...sel.options].some((o) => o.value === pref)) {
        const opt = document.createElement("option");
        opt.value = pref;
        opt.textContent = pref;
        opt.selected = true;
        sel.appendChild(opt);
      }
      brain.appendChild(lab);
      brain.appendChild(sel);

      row.appendChild(who);
      row.appendChild(brain);
      form.appendChild(row);
    });
  }

  async function saveModels() {
    const prefs = [...document.querySelectorAll("#modelsForm select")].map((sel) => ({
      agent_type: sel.dataset.agent,
      model_id: sel.value,
    }));
    try {
      await api("/workspace/agent-models", {
        method: "PATCH",
        body: JSON.stringify({ prefs }),
      });
      $("modelsStatus").textContent = "saved";
      await loadModels();
    } catch (e) {
      $("modelsStatus").textContent = String(e.message || e);
    }
  }

  function canDragCard(card) {
    if (state.isOwner) return true;
    return Number(card.assignee_user_id) === Number(state.userId);
  }

  async function loadBoard() {
    const board = await api(`/projects/${state.projectId}/board`);
    const root = $("boardColumns");
    root.innerHTML = "";
    let jobsToday = "";
    try {
      const sum = await api(`/projects/${state.projectId}/jobs/summary`);
      jobsToday = `jobs: ${sum.total}`;
    } catch (_) { /* ignore */ }
    $("boardFooter").textContent = jobsToday;

    const allCards = [];
    (board.columns || []).forEach((col) => {
      (col.cards || []).forEach((card) => {
        allCards.push({ ...card, status: col.id });
      });
    });
    state.boardCards = allCards;

    (board.columns || []).forEach((col) => {
      const el = document.createElement("div");
      el.className = "board-col";
      el.dataset.status = col.id;
      const h = document.createElement("h3");
      h.textContent = `${col.id} (${(col.cards || []).length})`;
      el.appendChild(h);
      const cards = document.createElement("div");
      cards.className = "cards";
      cards.dataset.status = col.id;

      cards.addEventListener("dragover", (ev) => {
        ev.preventDefault();
        el.classList.add("drag-over");
      });
      cards.addEventListener("dragleave", () => el.classList.remove("drag-over"));
      cards.addEventListener("drop", async (ev) => {
        ev.preventDefault();
        el.classList.remove("drag-over");
        const oid = Number(ev.dataTransfer.getData("text/oid"));
        const status = col.id;
        if (!oid) return;
        try {
          await api(`/projects/${state.projectId}/objectives/${oid}`, {
            method: "PATCH",
            body: JSON.stringify({ status }),
          });
          await loadBoard();
        } catch (e) {
          setVoiceStatus(String(e.message || e));
        }
      });

      (col.cards || []).forEach((card) => {
        const c = document.createElement("div");
        c.className = "board-card";
        c.draggable = canDragCard(card);
        c.dataset.id = card.id;
        c.innerHTML = `<div class="t">${escapeHtml(card.title)}</div>
          <div class="meta">${escapeHtml(card.owner_email || "")} - ${card.progress_percent || 0}%</div>`;
        if (card.open_issue_count) {
          const b = document.createElement("span");
          b.className = "badge";
          b.textContent = `${card.open_issue_count} blocker`;
          c.appendChild(b);
        }
        if ((card.claimed_paths || []).length) {
          const p = document.createElement("div");
          p.className = "meta";
          p.textContent = "claims: " + card.claimed_paths.join(", ");
          c.appendChild(p);
        }
        c.addEventListener("dragstart", (ev) => {
          if (!canDragCard(card)) {
            ev.preventDefault();
            return;
          }
          c.classList.add("dragging");
          ev.dataTransfer.setData("text/oid", String(card.id));
        });
        c.addEventListener("dragend", () => c.classList.remove("dragging"));
        c.onclick = () => showBoardPanel(card);
        cards.appendChild(c);
      });
      el.appendChild(cards);
      root.appendChild(el);
    });
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function showBoardPanel(card) {
    const panel = $("boardPanel");
    panel.classList.remove("hidden");
    $("boardPanelBody").innerHTML = `
      <h3>#${card.id} ${escapeHtml(card.title)}</h3>
      <p class="meta">owner: ${escapeHtml(card.owner_email || "")}</p>
      <p class="meta">status: ${escapeHtml(card.status)}</p>
      <p class="meta">checklist: ${card.checklist_closed}/${card.checklist_total}</p>
      <p class="meta">issues: ${card.open_issue_count}</p>
      ${card.github_pr_url ? `<p><a href="${escapeHtml(card.github_pr_url)}" target="_blank" rel="noopener">Open PR</a></p>` : ""}
      <p><button type="button" id="openInChat">open in chat</button></p>
    `;
    const btn = document.getElementById("openInChat");
    if (btn) {
      btn.onclick = () => {
        setTab("chat");
        setVoiceStatus(`objective #${card.id}: ${card.title}`);
      };
    }
  }

  async function loadAnalytics() {
    const body = $("analyticsBody");
    if (!state.isOwner) {
      body.innerHTML = `<p class="dash-muted">Owner only.</p>`;
      return;
    }
    try {
      if (!state.projectId) {
        const chats = state.chats || [];
        const withProj = chats.find((c) => c.project_id);
        if (withProj) state.projectId = withProj.project_id;
      }
      if (!state.projectId) {
        body.innerHTML = `<p class="dash-muted">No project yet.</p>`;
        return;
      }
      const data = await api(`/projects/${state.projectId}/analytics`);
      const s = data.summary || {};
      const fmt = (n) => Number(n || 0).toLocaleString();
      const peopleRows = (data.people || [])
        .map((p) => {
          const models = (p.models || []).slice(0, 4).map(escapeHtml).join(", ")
            + ((p.models || []).length > 4 ? ` +${p.models.length - 4}` : "");
          return `<tr>
            <td>${escapeHtml(p.name || p.email)}<div class="dash-sub">${escapeHtml(p.email)} - ${escapeHtml(p.role)}</div></td>
            <td class="num">${fmt(p.jobs)}</td>
            <td class="num">${fmt(p.tokens)}</td>
            <td class="models-cell">${models || "n/a"}</td>
          </tr>`;
        })
        .join("");
      const modelRows = (data.models || [])
        .map(
          (m) => `<tr>
            <td>${escapeHtml(m.model)}<div class="dash-sub">${escapeHtml(m.backend || "")}</div></td>
            <td class="num">${fmt(m.runs)}</td>
            <td class="num">${fmt(m.tokens)}</td>
            <td class="num">${fmt(m.success)}/${fmt(m.fail)}</td>
          </tr>`
        )
        .join("");
      const taskRows = (data.open_tasks || [])
        .map(
          (t) => `<tr>
            <td>#${t.id} ${escapeHtml(t.title)}</td>
            <td>${escapeHtml(t.status)}</td>
            <td>${escapeHtml(t.assignee_email || "unassigned")}</td>
          </tr>`
        )
        .join("");
      const memberOpts = (state.members || [])
        .map((m) => `<option value="${m.user_id}">${escapeHtml(m.email)}</option>`)
        .join("");
      const taskList = data.all_tasks || data.open_tasks || [];
      const taskOpts = taskList
        .map((t) => {
          const who = t.assignee_email ? ` (${t.assignee_email})` : "";
          return `<option value="${t.id}">#${t.id} ${escapeHtml(t.title)}${escapeHtml(who)} - ${escapeHtml(t.status)}</option>`;
        })
        .join("");

      body.innerHTML = `
        <div class="dash">
          <header class="dash-head">
            <div>
              <h2>Dashboard</h2>
              <p class="dash-muted">Project manager view - numbers only.</p>
            </div>
          </header>
          <div class="dash-stats">
            <div class="dash-stat"><div class="n">${fmt(s.members)}</div><div class="l">people</div></div>
            <div class="dash-stat"><div class="n">${fmt(s.open_tasks)}</div><div class="l">open tasks</div></div>
            <div class="dash-stat"><div class="n">${fmt(s.jobs_done)}</div><div class="l">jobs done</div></div>
            <div class="dash-stat"><div class="n">${fmt(s.jobs_failed)}</div><div class="l">failed</div></div>
            <div class="dash-stat accent"><div class="n">${fmt(s.tokens_total)}</div><div class="l">tokens</div></div>
            <div class="dash-stat"><div class="n">${fmt(s.model_count)}</div><div class="l">models</div></div>
          </div>

          <section class="dash-section">
            <h3>Assign task</h3>
            <form id="dashAssignForm" class="dash-assign">
              <select id="dashAssignTask" required ${taskOpts ? "" : "disabled"}>
                ${taskOpts || `<option value="">no tasks yet</option>`}
              </select>
              <select id="dashAssignUser" required>${memberOpts}</select>
              <button type="submit" ${taskOpts ? "" : "disabled"}>assign</button>
            </form>
            <pre id="dashAssignStatus" class="dash-status"></pre>
          </section>

          <section class="dash-section">
            <h3>People</h3>
            <table class="dash-table">
              <thead><tr><th>person</th><th class="num">jobs</th><th class="num">tokens</th><th>models</th></tr></thead>
              <tbody>${peopleRows || `<tr><td colspan="4" class="dash-muted">no usage yet</td></tr>`}</tbody>
            </table>
          </section>

          <section class="dash-section">
            <h3>Models</h3>
            <table class="dash-table">
              <thead><tr><th>model</th><th class="num">runs</th><th class="num">tokens</th><th class="num">ok/fail</th></tr></thead>
              <tbody>${modelRows || `<tr><td colspan="4" class="dash-muted">no model runs yet</td></tr>`}</tbody>
            </table>
          </section>

          <section class="dash-section">
            <h3>Open tasks</h3>
            <table class="dash-table">
              <thead><tr><th>task</th><th>status</th><th>assignee</th></tr></thead>
              <tbody>${taskRows || `<tr><td colspan="3" class="dash-muted">nothing open</td></tr>`}</tbody>
            </table>
          </section>
        </div>
      `;

      const form = $("dashAssignForm");
      if (form) {
        form.onsubmit = async (ev) => {
          ev.preventDefault();
          const status = $("dashAssignStatus");
          const objective_id = Number($("dashAssignTask").value);
          const assignee_user_id = Number($("dashAssignUser").value);
          if (!objective_id) {
            status.textContent = "pick a task";
            return;
          }
          status.textContent = "...";
          try {
            const out = await api(`/projects/${state.projectId}/dashboard/assign`, {
              method: "POST",
              body: JSON.stringify({ objective_id, assignee_user_id }),
            });
            status.textContent = `assigned #${out.id} to ${out.assignee_email}`;
            await loadAnalytics();
          } catch (e) {
            status.textContent = e.message || String(e);
          }
        };
      }
    } catch (e) {
      body.innerHTML = `<p class="err">${escapeHtml(e.message || e)}</p>`;
    }
  }

  function renderChatLi(c, { allowDelete }) {
    const li = document.createElement("li");
    li.dataset.id = c.id;
    if (Number(c.id) === Number(state.chatId)) li.classList.add("active");

    const label = document.createElement("span");
    label.className = "label";
    label.textContent = c.kind === "private" ? "my private room" : `#${c.name}`;
    label.onclick = () => selectChat(c.id);
    li.appendChild(label);

    if (allowDelete && !(c.name === "general" && c.kind === "channel")) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "x";
      del.title = "delete chat";
      del.textContent = "x";
      del.onclick = (ev) => {
        ev.stopPropagation();
        void deleteChat(c.id, c.name);
      };
      li.appendChild(del);
    }
    return li;
  }

  async function refreshSidebar() {
    const chats = await api("/chats");
    const members = await api("/workspace/members");
    state.chats = chats;
    state.members = members;
    const withProj = chats.find((c) => c.project_id);
    if (withProj) state.projectId = withProj.project_id;
    const me = members.find((m) => Number(m.user_id) === Number(state.userId) || m.email === state.email);
    state.isOwner = !!(me && String(me.role || "").toLowerCase() === "owner");
    $("analyticsTab").classList.toggle("hidden", !state.isOwner);

    const team = chats.filter((c) => c.kind === "channel");
    const mine = chats.filter((c) => c.kind === "private");

    const teamList = $("teamList");
    teamList.innerHTML = "";
    team.forEach((c) => teamList.appendChild(renderChatLi(c, { allowDelete: true })));

    const myRoomList = $("myRoomList");
    myRoomList.innerHTML = "";
    mine.forEach((c) => myRoomList.appendChild(renderChatLi(c, { allowDelete: false })));

    if (state.chatId && !chats.some((c) => Number(c.id) === Number(state.chatId))) {
      state.chatId = null;
      $("chatTitle").textContent = "";
      $("messages").innerHTML = "";
    }

    // Prefer private room as default workspace
    if (!state.chatId) {
      const priv = mine[0] || team.find((c) => c.name === "general") || team[0] || chats[0];
      if (priv) await selectChat(priv.id);
    }

    if (!chats.length) {
      state.chatId = null;
      $("chatTitle").textContent = "(no chats)";
      $("messages").innerHTML = "";
    }

    const memberList = $("memberList");
    memberList.innerHTML = "";
    members.forEach((m) => {
      const li = document.createElement("li");
      li.className = "member";
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = colorForMember(m.email);
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = `${m.name || m.email} (${m.role})`;
      label.title = m.email || "";
      li.appendChild(swatch);
      li.appendChild(label);
      memberList.appendChild(li);
    });
  }

  function lanAppUrl() {
    const host = window.location.hostname;
    const port = window.location.port || "8000";
    // Prefer non-localhost so friends can use the same string
    if (host === "127.0.0.1" || host === "localhost") {
      return `http://<YOUR-LAN-IP>:${port}/app`;
    }
    return `${window.location.protocol}//${host}${port ? ":" + port : ""}/app`;
  }

  function setMessageBody(el, text) {
    el.textContent = "";
    const parts = String(text || "").split(/(https?:\/\/\S+|mailto:[^\s]+)/g);
    parts.forEach((part) => {
      if (/^https?:\/\//.test(part)) {
        const a = document.createElement("a");
        a.href = part;
        a.textContent = part;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        el.appendChild(a);
      } else if (/^mailto:/i.test(part)) {
        const a = document.createElement("a");
        a.href = part;
        a.textContent = part.replace(/^mailto:/i, "");
        el.appendChild(a);
      } else if (part) {
        el.appendChild(document.createTextNode(part));
      }
    });
  }

  function showInviteLink(url) {
    window.alert(
      `Single-use invite link (one person).\nAfter they register, run !invitation or click + again for the next person.\n\n${url}`
    );
    setVoiceStatus("invite link ready");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).catch(() => {});
    }
  }

  function renderMessages(rows, append) {
    const box = $("messages");
    if (!append) box.innerHTML = "";
    rows.forEach((m) => {
      const div = document.createElement("div");
      div.className = "msg" + (m.agent ? " agent" : " user") + (m.visibility === "whisper" ? " whisper" : "");
      div.dataset.msgId = String(m.id);
      const color = colorForMessage(m);
      div.style.borderLeftColor = color;
      const who = m.agent ? `@${m.agent}` : (m.sender || "user");
      const whisperTag = m.visibility === "whisper" ? " - only you" : "";
      let bodyText = m.body || "";
      const confirmMatch = bodyText.match(/\[\[confirm:([0-9,\s]+)\]\]/);
      const confirmIds = confirmMatch
        ? confirmMatch[1].split(",").map((s) => s.trim()).filter(Boolean)
        : [];
      bodyText = bodyText.replace(/\n?\[\[confirm:[0-9,\s]+\]\]\s*$/, "").trimEnd();
      div.innerHTML =
        `<div class="meta"><span class="who"></span>${whisperTag} <span class="msg-id"> - #${m.id}</span></div><div class="body"></div>`;
      const whoEl = div.querySelector(".who");
      whoEl.textContent = who;
      whoEl.style.color = color;
      setMessageBody(div.querySelector(".body"), bodyText);
      if (confirmIds.length) {
        const actions = document.createElement("div");
        actions.className = "confirm-actions";
        confirmIds.forEach((id) => {
          const row = document.createElement("div");
          row.className = "confirm-row";
          const label = document.createElement("span");
          label.textContent = `Objective #${id} met?`;
          const yes = document.createElement("button");
          yes.type = "button";
          yes.textContent = "Yes";
          yes.onclick = () => void sendBody(`!done ${id}`);
          const no = document.createElement("button");
          no.type = "button";
          no.textContent = "No";
          no.onclick = () => void sendBody(`!keep ${id}`);
          row.appendChild(label);
          row.appendChild(yes);
          row.appendChild(no);
          actions.appendChild(row);
        });
        div.appendChild(actions);
      }
      if (m.audio_url) {
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.src = m.audio_url;
        div.appendChild(audio);
        if ($("speakToggle").checked) {
          audio.play().catch(() => {});
        }
      }
      box.appendChild(div);
      state.lastMsgId = Math.max(state.lastMsgId, m.id);
    });
    box.scrollTop = box.scrollHeight;
  }

  async function selectChat(id) {
    state.chatId = id;
    state.lastMsgId = 0;
    const meta = (state.chats || []).find((c) => Number(c.id) === Number(id));
    const title = meta
      ? (meta.kind === "private" ? "my private room" : `team #${meta.name}`)
      : `chat #${id}`;
    $("chatTitle").textContent = title;
    const input = $("input");
    if (meta && meta.kind === "private") {
      input.placeholder = "/skills - !commands - notes stay quiet";
    } else {
      input.placeholder = "chat - @people - !commands (only you see)";
    }
    document.querySelectorAll("#teamList li, #myRoomList li").forEach((li) => {
      li.classList.toggle("active", Number(li.dataset.id) === Number(id));
    });
    const rows = await api(`/chats/${id}/messages?after_id=0`);
    renderMessages(rows, false);
  }

  async function refreshMentions() {
    const btn = $("mentionsBtn");
    const panel = $("mentionsPanel");
    if (!btn) return;
    try {
      const data = await api("/workspace/mentions");
      const n = data.unread || 0;
      const prev = state.unreadMentions || 0;
      state.unreadMentions = n;
      state.mentionRows = data.mentions || [];
      if (n > prev && n > 0) playPingSound();
      if (n > 0) {
        btn.textContent = `@${n}`;
        btn.classList.remove("hidden");
      } else {
        btn.classList.add("hidden");
        btn.textContent = "@0";
        if (panel) panel.classList.add("hidden");
      }
    } catch (_) {
      btn.classList.add("hidden");
    }
  }

  function playPingSound() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.setValueAtTime(880, ctx.currentTime);
      o.frequency.exponentialRampToValueAtTime(660, ctx.currentTime + 0.08);
      g.gain.setValueAtTime(0.0001, ctx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22);
      o.connect(g);
      g.connect(ctx.destination);
      o.start();
      o.stop(ctx.currentTime + 0.25);
      setTimeout(() => ctx.close().catch(() => {}), 400);
    } catch (_) { /* ignore */ }
  }

  function renderMentionsPanel() {
    const panel = $("mentionsPanel");
    if (!panel) return;
    const rows = state.mentionRows || [];
    panel.innerHTML = "";
    const head = document.createElement("div");
    head.className = "mp-head";
    head.innerHTML = `<span>Mentions</span>`;
    const mark = document.createElement("button");
    mark.type = "button";
    mark.textContent = "mark all read";
    mark.onclick = async (ev) => {
      ev.stopPropagation();
      await api("/workspace/mentions/read", { method: "POST", body: "{}" });
      panel.classList.add("hidden");
      await refreshMentions();
    };
    head.appendChild(mark);
    panel.appendChild(head);
    if (!rows.length) {
      const empty = document.createElement("div");
      empty.className = "mp-item";
      empty.textContent = "No unread mentions";
      panel.appendChild(empty);
      return;
    }
    rows.forEach((m) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mp-item";
      btn.innerHTML =
        `<div class="mp-from"></div><div class="mp-where"></div><div class="mp-snip"></div>`;
      btn.querySelector(".mp-from").textContent = m.from || "someone";
      btn.querySelector(".mp-where").textContent = `#${m.chat_name || m.chat_id} - msg #${m.message_id}`;
      btn.querySelector(".mp-snip").textContent = m.snippet || "";
      btn.onclick = () => void openMention(m);
      panel.appendChild(btn);
    });
  }

  async function openMention(m) {
    const panel = $("mentionsPanel");
    if (panel) panel.classList.add("hidden");
    try {
      await api("/workspace/mentions/read", {
        method: "POST",
        body: JSON.stringify({ ids: [m.id] }),
      });
    } catch (_) {
      // older API marks all - still fine
      try {
        await api("/workspace/mentions/read", { method: "POST", body: "{}" });
      } catch (_) { /* ignore */ }
    }
    setTab("chat");
    await refreshSidebar();
    await selectChat(m.chat_id);
    // scroll/highlight target message
    const el = document.querySelector(`.msg[data-msg-id="${m.message_id}"]`);
    if (el) {
      el.classList.add("highlight-ping");
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => el.classList.remove("highlight-ping"), 3500);
    } else {
      setVoiceStatus(`opened #${m.chat_name || m.chat_id} - message #${m.message_id}`);
    }
    await refreshMentions();
  }

  async function poll() {
    if (!state.chatId || !state.apiKey) return;
    try {
      const rows = await api(`/chats/${state.chatId}/messages?after_id=${state.lastMsgId}`);
      if (rows.length) renderMessages(rows, true);
      await refreshMentions();
    } catch (_) { /* ignore transient */ }
  }

  async function afterMessageMeta(data) {
    if (data.cleared) {
      state.lastMsgId = 0;
      $("messages").innerHTML = "";
      if (data.replies && data.replies.length) renderMessages(data.replies, false);
      return;
    }
    if (data.deleted_chat_id) {
      if (Number(state.chatId) === Number(data.deleted_chat_id)) {
        state.chatId = null;
      }
      await refreshSidebar();
      setVoiceStatus(`deleted chat #${data.deleted_chat_id}`);
      return;
    }
    await poll();
    if (data.created_chat_id) {
      await refreshSidebar();
      await selectChat(data.created_chat_id);
    }
  }

  async function sendBody(body) {
    if (!body || !state.chatId) return;
    const expectsLlm = looksLikeAgentWork(body);
    if (expectsLlm) startLlmWait(body);
    else setComposerBusy(true);
    try {
      const data = await api(`/chats/${state.chatId}/messages`, {
        method: "POST",
        body: JSON.stringify({ body, speak: $("speakToggle").checked }),
      });
      await afterMessageMeta(data);
    } finally {
      stopLlmWait();
      setComposerBusy(false);
    }
  }

  function skillNameFromBody(body) {
    const t = String(body || "").trim();
    const m = t.match(/^\/(code|research|write|web|review|checklist)\b/i);
    if (m) return m[1].toLowerCase();
    const m2 = t.match(/^(?:force\s+)?(code|research|write|review)\b/i);
    return m2 ? m2[1].toLowerCase() : "";
  }

  function looksLikeAgentWork(body) {
    const t = String(body || "").trim();
    if (!t) return false;
    // Any private-room skill invocation
    if (/^\/(code|research|write|web|review|checklist)\b/i.test(t)) return true;
    if (/^(force\s+)?(code|research|write|review)\b/i.test(t)) return true;
    // Any other leading slash in private room (unknown skill still hits server)
    const meta = currentChatMeta();
    if (meta && meta.kind === "private" && t.startsWith("/")) return true;
    return false;
  }

  let llmWaitTimer = null;
  let llmWaitStarted = 0;
  let llmWaitExpectedMs = 60000;
  let llmPendingEl = null;

  function setComposerBusy(on) {
    $("composer").classList.toggle("busy", !!on);
    const sendBtn = $("sendBtn");
    if (sendBtn) sendBtn.disabled = !!on;
    const input = $("input");
    if (input) input.disabled = !!on;
  }

  function estimateWaitMs(body) {
    const skill = skillNameFromBody(body);
    if (skill === "code") return 120000;
    if (skill === "research" || skill === "web" || skill === "review") return 90000;
    return 60000;
  }

  function showPendingBubble(skill) {
    removePendingBubble();
    const box = $("messages");
    if (!box) return;
    const div = document.createElement("div");
    div.className = "msg agent llm-pending";
    div.id = "llmPendingMsg";
    const label = skill ? `/${skill}` : "agent";
    div.innerHTML =
      `<div class="meta"><span class="who">${label}</span> <span class="msg-id">working</span></div>` +
      `<div class="body">Thinking… generating a reply</div>`;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    llmPendingEl = div;
  }

  function removePendingBubble() {
    if (llmPendingEl && llmPendingEl.parentNode) {
      llmPendingEl.parentNode.removeChild(llmPendingEl);
    }
    llmPendingEl = null;
    const stale = document.getElementById("llmPendingMsg");
    if (stale) stale.remove();
  }

  function startLlmWait(body) {
    stopLlmWait(false);
    setComposerBusy(true);
    llmWaitStarted = Date.now();
    llmWaitExpectedMs = estimateWaitMs(body);
    const skill = skillNameFromBody(body);
    const box = $("llmWait");
    const bar = $("llmWaitBar");
    const label = $("llmWaitLabel");
    const eta = $("llmWaitEta");
    const hint = $("llmWaitHint");
    if (!box || !bar) return;
    box.classList.remove("hidden");
    label.textContent = skill
      ? `Running /${skill} — model is working…`
      : "Agent working — model is generating…";
    if (hint) {
      hint.textContent = skill
        ? `Please wait while /${skill} finishes (often 30–90s)`
        : "Please wait — the model is generating a reply";
    }
    bar.style.width = "4%";
    eta.textContent = "starting…";
    showPendingBubble(skill);
    llmWaitTimer = setInterval(() => {
      const elapsed = Date.now() - llmWaitStarted;
      const t = Math.min(1, elapsed / llmWaitExpectedMs);
      const pct = Math.min(92, 4 + t * 88);
      bar.style.width = `${pct}%`;
      const left = Math.max(0, Math.ceil((llmWaitExpectedMs - elapsed) / 1000));
      if (elapsed < llmWaitExpectedMs) {
        eta.textContent = left > 5 ? `~${left}s left` : "almost done…";
      } else {
        eta.textContent = "still working…";
        label.textContent = skill
          ? `/${skill} is taking longer than usual…`
          : "Taking longer than usual…";
      }
    }, 250);
  }

  function stopLlmWait(animateDone = true) {
    if (llmWaitTimer) {
      clearInterval(llmWaitTimer);
      llmWaitTimer = null;
    }
    removePendingBubble();
    const box = $("llmWait");
    const bar = $("llmWaitBar");
    if (bar && animateDone) bar.style.width = "100%";
    if (box) {
      const hide = () => {
        box.classList.add("hidden");
        if (bar) bar.style.width = "0%";
      };
      if (animateDone) setTimeout(hide, 220);
      else hide();
    }
  }

  async function send(ev) {
    ev.preventDefault();
    const body = $("input").value.trim();
    if (!body || !state.chatId) return;
    if ($("composer").classList.contains("busy")) return;
    $("input").value = "";
    try {
      await sendBody(body);
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  async function newChat() {
    const name = window.prompt("Team chat name", "untitled");
    if (name === null) return;
    const cleaned = name.trim() || "untitled";
    try {
      const chat = await api("/chats", {
        method: "POST",
        body: JSON.stringify({ name: cleaned, kind: "channel" }),
      });
      await refreshSidebar();
      await selectChat(chat.id);
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  async function deleteChat(id, name) {
    if (!window.confirm(`Delete chat #${id} "${name}"?`)) return;
    try {
      await api(`/chats/${id}`, { method: "DELETE" });
      if (Number(state.chatId) === Number(id)) {
        state.chatId = null;
        $("messages").innerHTML = "";
      }
      await refreshSidebar();
      setVoiceStatus(`deleted chat #${id}`);
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  async function inviteMember() {
    try {
      const data = await api("/workspace/invite-link");
      if (data.invite_url) showInviteLink(data.invite_url);
      else setVoiceStatus("no invite link");
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  async function startRecording() {
    if (state.recording || !$("voiceToggle").checked) return;
    setVoiceStatus("requesting mic...");
    try {
      state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setVoiceStatus("mic blocked - allow microphone access");
      return;
    }
    state.chunks = [];
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : "";
    state.recorder = mime
      ? new MediaRecorder(state.mediaStream, { mimeType: mime })
      : new MediaRecorder(state.mediaStream);
    state.recorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size) state.chunks.push(ev.data);
    };
    state.recorder.onstop = () => {
      void finishRecording();
    };
    state.recorder.start();
    state.recording = true;
    $("micBtn").classList.add("recording");
    $("micBtn").textContent = "stop";
    setVoiceStatus("listening... click stop when done");
  }

  function stopRecording(transcribe = true) {
    if (!state.recording || !state.recorder) return;
    state._skipTranscribe = !transcribe;
    state.recording = false;
    $("micBtn").classList.remove("recording");
    $("micBtn").textContent = "mic";
    if (state.recorder.state !== "inactive") state.recorder.stop();
    if (state.mediaStream) {
      state.mediaStream.getTracks().forEach((t) => t.stop());
      state.mediaStream = null;
    }
  }

  async function finishRecording() {
    const skip = state._skipTranscribe;
    state._skipTranscribe = false;
    const blob = new Blob(state.chunks, { type: state.recorder?.mimeType || "audio/webm" });
    state.chunks = [];
    state.recorder = null;
    if (skip) {
      setVoiceStatus("");
      return;
    }
    if (!blob.size) {
      setVoiceStatus("no audio captured");
      return;
    }
    setVoiceStatus("transcribing...");
    try {
      const form = new FormData();
      form.append("file", blob, "voice.webm");
      const data = await api("/stt", { method: "POST", body: form });
      const text = (data.text || "").trim();
      if (!text) {
        setVoiceStatus("empty transcript - try again");
        return;
      }
      $("input").value = text;
      setVoiceStatus(`heard: ${text}`);
      await sendBody(text);
      $("input").value = "";
      setVoiceStatus("");
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  $("loginBtn").onclick = login;
  $("logoutBtn").onclick = () => {
    localStorage.removeItem("aio_email");
    localStorage.removeItem("aio_key");
    localStorage.removeItem("aio_uid");
    state.apiKey = "";
    state.userId = null;
    stopRecording(false);
    showApp(false);
  };
  $("composer").onsubmit = send;
  $("newChatBtn").onclick = newChat;
  $("inviteBtn").onclick = inviteMember;
  $("voiceToggle").onchange = syncMicUi;
  $("micBtn").onclick = () => {
    if (state.recording) stopRecording(true);
    else startRecording();
  };
  document.querySelectorAll("#mainTabs .tab").forEach((btn) => {
    btn.onclick = () => setTab(btn.dataset.tab);
  });
  $("boardPanelClose").onclick = () => $("boardPanel").classList.add("hidden");
  $("modelsSaveBtn").onclick = () => void saveModels();
  const mentionsBtn = $("mentionsBtn");
  if (mentionsBtn) {
    mentionsBtn.onclick = (ev) => {
      ev.stopPropagation();
      const panel = $("mentionsPanel");
      if (!panel) return;
      const open = panel.classList.contains("hidden");
      if (open) {
        renderMentionsPanel();
        panel.classList.remove("hidden");
      } else {
        panel.classList.add("hidden");
      }
    };
  }
  document.addEventListener("click", (ev) => {
    const panel = $("mentionsPanel");
    const btn = $("mentionsBtn");
    if (!panel || panel.classList.contains("hidden")) return;
    if (panel.contains(ev.target) || (btn && btn.contains(ev.target))) return;
    panel.classList.add("hidden");
  });

  const STATUS_OPTS = ["todo", "doing", "blocked", "done", "agent_backlog", "in_review"];

  const COMMAND_CATALOG = [
    { insert: "!add ", label: "!add", blurb: "new board card", args: ["<title>"] },
    { insert: "!list", label: "!list", blurb: "show my cards", args: [] },
    { insert: "!set ", label: "!set", blurb: "move card status", args: ["<id>", "<status>"] },
    { insert: "!done ", label: "!done", blurb: "mark card done", args: ["<id>"] },
    { insert: "!remove ", label: "!remove", blurb: "delete a card", args: ["<id>"] },
    { insert: "!assign ", label: "!assign", blurb: "give card away", args: ["<id>", "<name>"] },
    { insert: "!link ", label: "!link", blurb: "attach branch/PR", args: ["<id>", "branch|pr", "<value>"] },
    { insert: "!claim ", label: "!claim", blurb: "lock a file", args: ["<path>"] },
    { insert: "!release ", label: "!release", blurb: "free a file", args: ["<path>"] },
    { insert: "!go", label: "!go", blurb: "run despite claim", args: [] },
    { insert: "!issue ", label: "!issue", blurb: "log a blocker", args: ["<text>"] },
    { insert: "!issues", label: "!issues", blurb: "show blockers", args: [] },
    { insert: "!resolve ", label: "!resolve", blurb: "close blocker", args: ["<id>"] },
    { insert: "!invitation", label: "!invitation", blurb: "new single-use invite", args: [] },
    { insert: "!status ", label: "!status", blurb: "member catch-up", args: ["<name>"] },
    { insert: "!clear", label: "!clear", blurb: "wipe this chat", args: [] },
    { insert: "!help", label: "!help", blurb: "list commands", args: [] },
  ];

  const SKILL_CATALOG = [
    { insert: "/research ", label: "/research", blurb: "dig facts & sources", args: ["<ask>"] },
    { insert: "/web ", label: "/web", blurb: "look things up", args: ["<ask>"] },
    { insert: "/code ", label: "/code", blurb: "build or patch", args: ["<ask>"] },
    { insert: "/write ", label: "/write", blurb: "draft clear prose", args: ["<ask>"] },
    { insert: "/review ", label: "/review", blurb: "check the diff", args: ["<ask>"] },
    { insert: "/checklist ", label: "/checklist", blurb: "break into ticks", args: ["<ask>"] },
  ];

  const pickerState = {
    open: false,
    items: [],
    index: 0,
    triggerIndex: 0,
    mode: null, // "prefix" | "arg"
  };

  function currentChatMeta() {
    return (state.chats || []).find((c) => Number(c.id) === Number(state.chatId));
  }

  function peopleCandidates(prefix) {
    const p = (prefix || "").toLowerCase();
    const out = [];
    if (!p || "team".startsWith(p)) {
      out.push({ label: "@team", blurb: "ping whole team", insert: "@team " });
    }
    (state.members || []).forEach((m) => {
      const handle = (m.name || "").trim() || (m.email || "").split("@")[0];
      if (!handle) return;
      if (!p || handle.toLowerCase().startsWith(p)) {
        out.push({
          label: `@${handle}`,
          blurb: m.email || "ping this person",
          insert: `@${handle} `,
        });
      }
    });
    return out.slice(0, 12);
  }

  function filterCatalog(catalog, prefix) {
    const p = (prefix || "").toLowerCase();
    return catalog.filter((c) => {
      const token = c.label.slice(1).toLowerCase();
      return !p || token.startsWith(p) || c.label.toLowerCase().includes(p);
    }).slice(0, 16);
  }

  function findCommandSpec(v) {
    const m = String(v || "").match(/^(![a-z]+|\/[a-z]+)\b(.*)$/i);
    if (!m) return null;
    const label = m[1].toLowerCase();
    const rest = m[2] || "";
    const cat = [...COMMAND_CATALOG, ...SKILL_CATALOG].find(
      (c) => c.label.toLowerCase() === label
    );
    if (!cat) return null;
    return { cat, label, rest, args: cat.args || [] };
  }

  function parseArgTokens(rest) {
    // leading space means args started; split on whitespace
    if (!rest.startsWith(" ") && rest !== "") return null;
    const trimmed = rest.replace(/^\s+/, "");
    if (rest.startsWith(" ") && trimmed === "" && rest.length >= 1) {
      return { tokens: [], partial: "", completeCount: 0 };
    }
    if (!rest.startsWith(" ")) return null;
    const parts = trimmed.split(/\s+/);
    const endsWithSpace = /\s$/.test(rest);
    if (endsWithSpace) {
      return { tokens: parts.filter(Boolean), partial: "", completeCount: parts.filter(Boolean).length };
    }
    const partial = parts[parts.length - 1] || "";
    const complete = parts.slice(0, -1);
    return { tokens: complete, partial, completeCount: complete.length };
  }

  function updateGhostHint() {
    const input = $("input");
    const typedEl = $("composerTyped");
    const hintEl = $("composerHint");
    if (!input || !typedEl || !hintEl) return;
    const v = input.value;
    typedEl.textContent = v;
    const spec = findCommandSpec(v);
    if (!spec || !spec.args.length) {
      hintEl.textContent = "";
      return;
    }
    // Need at least the command recognized; show remaining arg placeholders
    const parsed = parseArgTokens(spec.rest);
    if (parsed === null) {
      // still typing command name, no ghost yet (or command without trailing space)
      if (v.toLowerCase() === spec.label) {
        hintEl.textContent = " " + spec.args.join(" ");
      } else {
        hintEl.textContent = "";
      }
      return;
    }
    const remaining = spec.args.slice(parsed.completeCount);
    if (!remaining.length) {
      hintEl.textContent = "";
      return;
    }
    // If mid-token, show suffix of current arg hint only when partial empty
    if (parsed.partial) {
      hintEl.textContent = "";
      return;
    }
    const pad = v.endsWith(" ") || v.toLowerCase() === spec.label ? "" : " ";
    hintEl.textContent = pad + remaining.join(" ");
  }

  function hideMentions() {
    pickerState.open = false;
    pickerState.items = [];
    pickerState.index = 0;
    const box = $("mentionBox");
    box.classList.add("hidden");
    box.innerHTML = "";
  }

  function setPickerIndex(i) {
    const items = pickerState.items;
    if (!items.length) return;
    pickerState.index = ((i % items.length) + items.length) % items.length;
    const box = $("mentionBox");
    const lis = [...box.querySelectorAll("li.picker-item")];
    lis.forEach((li, idx) => li.classList.toggle("active", idx === pickerState.index));
    const active = lis[pickerState.index];
    if (active) active.scrollIntoView({ block: "nearest" });
  }

  function applyPickerItem(it) {
    const input = $("input");
    if (!it || !input) return;
    const insert = String(it.insert || "");
    if (pickerState.mode === "arg") {
      const v = input.value;
      // Replace trailing partial token (or append after trailing spaces)
      const replaced = v.replace(/(\s+)(\S*)$/, (_, sp) => `${sp}${insert.replace(/^\s+/, "")}`);
      if (replaced === v && /\s$/.test(v)) {
        input.value = v + insert.replace(/^\s+/, "");
      } else if (replaced === v) {
        input.value = v.replace(/\s*$/, " ") + insert.replace(/^\s+/, "");
      } else {
        input.value = replaced;
      }
    } else {
      const v = input.value;
      input.value =
        (pickerState.triggerIndex >= 0 ? v.slice(0, pickerState.triggerIndex) : "") + insert;
    }
    hideMentions();
    input.focus();
    updateGhostHint();
    void refreshComposerAssist();
  }

  function showPicker(items, triggerIndex, mode) {
    const box = $("mentionBox");
    box.innerHTML = "";
    if (!items.length) {
      hideMentions();
      return;
    }
    pickerState.open = true;
    pickerState.items = items;
    pickerState.index = 0;
    pickerState.triggerIndex = triggerIndex;
    pickerState.mode = mode || "prefix";
    items.forEach((it, idx) => {
      const li = document.createElement("li");
      li.className = "picker-item" + (idx === 0 ? " active" : "");
      const main = document.createElement("span");
      main.className = "picker-label";
      main.textContent = it.label;
      const blurb = document.createElement("span");
      blurb.className = "picker-blurb";
      blurb.textContent = it.blurb || "";
      li.appendChild(main);
      if (it.blurb) li.appendChild(blurb);
      li.onmouseenter = () => setPickerIndex(idx);
      li.onclick = (ev) => {
        ev.preventDefault();
        applyPickerItem(it);
      };
      box.appendChild(li);
    });
    const foot = document.createElement("li");
    foot.className = "picker-footer";
    foot.textContent = "↑↓ navigate · Tab/Enter select · Esc close";
    box.appendChild(foot);
    box.classList.remove("hidden");
  }

  function activePrefix(v) {
    // Only while typing the token after @ ! / with no space yet
    const at = v.lastIndexOf("@");
    const bang = v.lastIndexOf("!");
    const slash = v.lastIndexOf("/");
    const idx = Math.max(at, bang, slash);
    if (idx < 0) return null;
    // Prefer the trigger that starts the last "token" (no space after it)
    const after = v.slice(idx + 1);
    if (/\s/.test(after)) return null;
    // If there's content before trigger without space (e.g. email), skip @ mid-word
    if (idx > 0 && !/\s/.test(v[idx - 1]) && v[idx] === "@") return null;
    return { ch: v[idx], idx, after };
  }

  async function ensureBoardCards() {
    if ((state.boardCards || []).length) return;
    if (!state.projectId) return;
    try {
      const board = await api(`/projects/${state.projectId}/board`);
      const all = [];
      (board.columns || []).forEach((col) => {
        (col.cards || []).forEach((card) => all.push({ ...card, status: col.id }));
      });
      state.boardCards = all;
    } catch (_) {
      /* ignore */
    }
  }

  function objectiveArgItems(partial) {
    const p = (partial || "").toLowerCase();
    return (state.boardCards || [])
      .filter((c) => {
        const id = String(c.id);
        const title = String(c.title || "").toLowerCase();
        return !p || id.startsWith(p) || title.includes(p);
      })
      .slice(0, 12)
      .map((c) => ({
        label: `#${c.id}`,
        blurb: `${c.status || ""} · ${c.title || ""}`.trim(),
        insert: `${c.id} `,
      }));
  }

  function statusArgItems(partial) {
    const p = (partial || "").toLowerCase();
    return STATUS_OPTS.filter((s) => !p || s.startsWith(p)).map((s) => ({
      label: s,
      blurb: "status",
      insert: `${s} `,
    }));
  }

  function memberArgItems(partial) {
    const p = (partial || "").toLowerCase();
    return (state.members || [])
      .map((m) => {
        const handle = (m.name || "").trim() || (m.email || "").split("@")[0];
        return { handle, email: m.email };
      })
      .filter((m) => m.handle && (!p || m.handle.toLowerCase().startsWith(p)))
      .slice(0, 12)
      .map((m) => ({
        label: m.handle,
        blurb: m.email || "",
        insert: `${m.handle} `,
      }));
  }

  async function maybeShowArgPicker() {
    const v = $("input").value;
    const spec = findCommandSpec(v);
    if (!spec || !spec.args.length) return false;
    const parsed = parseArgTokens(spec.rest);
    if (!parsed) return false;
    const argIdx = parsed.completeCount;
    if (argIdx >= spec.args.length) {
      hideMentions();
      return false;
    }
    const hint = spec.args[argIdx];
    const partial = parsed.partial;

    if (hint === "<id>") {
      await ensureBoardCards();
      const items = objectiveArgItems(partial);
      if (!items.length) return false;
      showPicker(items, -1, "arg");
      return true;
    }
    if (hint === "<status>") {
      const items = statusArgItems(partial);
      if (!items.length) return false;
      showPicker(items, -1, "arg");
      return true;
    }
    if (hint === "<name>") {
      const items = memberArgItems(partial);
      if (!items.length) return false;
      showPicker(items, -1, "arg");
      return true;
    }
    if (hint === "branch|pr") {
      const opts = ["branch", "pr"]
        .filter((s) => !partial || s.startsWith(partial.toLowerCase()))
        .map((s) => ({ label: s, blurb: "link type", insert: `${s} ` }));
      if (!opts.length) return false;
      showPicker(opts, -1, "arg");
      return true;
    }
    // Free text args: no picker, ghost only
    hideMentions();
    return false;
  }

  async function refreshComposerAssist() {
    updateGhostHint();
    const v = $("input").value;
    const hit = activePrefix(v);
    if (hit) {
      const meta = currentChatMeta();
      if (hit.ch === "@") {
        showPicker(peopleCandidates(hit.after), hit.idx, "prefix");
        return;
      }
      if (hit.ch === "!") {
        showPicker(filterCatalog(COMMAND_CATALOG, hit.after), hit.idx, "prefix");
        return;
      }
      if (hit.ch === "/") {
        if (!meta || meta.kind !== "private") {
          hideMentions();
          setVoiceStatus("skills (/) only work in your private room");
          return;
        }
        setVoiceStatus("");
        showPicker(filterCatalog(SKILL_CATALOG, hit.after), hit.idx, "prefix");
        return;
      }
    }
    // Argument stage for completed command tokens
    const showed = await maybeShowArgPicker();
    if (!showed && !activePrefix(v)) {
      // keep closed unless arg picker opened
      if (!pickerState.open) hideMentions();
    }
  }

  $("input").addEventListener("input", () => {
    void refreshComposerAssist();
  });

  $("input").addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (pickerState.open) {
        ev.preventDefault();
        hideMentions();
      }
      return;
    }

    if (pickerState.open && pickerState.items.length) {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        setPickerIndex(pickerState.index + 1);
        return;
      }
      if (ev.key === "ArrowUp") {
        ev.preventDefault();
        setPickerIndex(pickerState.index - 1);
        return;
      }
      if (ev.key === "Tab" || ev.key === "Enter") {
        // Enter: accept picker instead of sending when open
        if (ev.key === "Enter" || ev.key === "Tab") {
          ev.preventDefault();
          applyPickerItem(pickerState.items[pickerState.index]);
          return;
        }
      }
    }

    // Tab with ghost hint only (no picker): jump caret to end visually / accept nothing hard
    if (ev.key === "Tab" && !pickerState.open) {
      const hintEl = $("composerHint");
      const hint = (hintEl && hintEl.textContent) || "";
      if (hint.trim()) {
        ev.preventDefault();
        // Open arg picker for next slot if possible
        void (async () => {
          const showed = await maybeShowArgPicker();
          if (!showed) {
            // Soft nudge: ensure trailing space so ghost shows next arg
            const input = $("input");
            if (input && !/\s$/.test(input.value)) {
              input.value += " ";
              updateGhostHint();
              await maybeShowArgPicker();
            }
          }
        })();
      }
    }
  });

  syncMicUi();

  if (state.email && state.apiKey) {
    $("email").value = state.email;
    // Session restore: already have api key from prior login / register
    showApp(true);
    $("who").textContent = state.email;
    refreshSidebar()
      .then(() => refreshMentions())
      .then(() => (state.chatId ? selectChat(state.chatId) : null))
      .catch((e) => {
        localStorage.removeItem("aio_email");
        localStorage.removeItem("aio_key");
        localStorage.removeItem("aio_uid");
        state.apiKey = "";
        showApp(false);
        $("loginErr").textContent = String(e.message || e);
      });
  }
  state.timer = setInterval(poll, 1500);
})();
