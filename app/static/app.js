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
    projectId: 1,
    isOwner: false,
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
    return colorForMember(m.sender);
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
    const api_key = $("apiKey").value.trim();
    try {
      const data = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, api_key }),
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
      if (state.chatId) await selectChat(state.chatId);
    } catch (e) {
      localStorage.removeItem("aio_email");
      localStorage.removeItem("aio_key");
      localStorage.removeItem("aio_uid");
      state.apiKey = "";
      showApp(false);
      $("loginErr").textContent =
        String(e.message || e) +
        "\nUse email + demo-key-a (e.g. a@local.test / demo-key-a).";
      $("apiKey").value = "demo-key-a";
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

  // Dry coworker notes — no callsigns / units / marketing bios
  const AGENT_PROFILES = {
    research: {
      mention: "@Research",
      job: "looks things up",
      line: "I keep digging until the answer stops wiggling. If I can’t find it, I say that out loud.",
      accent: "#4a9",
    },
    writing: {
      mention: "@Writing",
      job: "writes it down",
      line: "I cut fluff first. Goal is something a tired teammate can skim once.",
      accent: "#a84",
    },
    coding: {
      mention: "@Code",
      job: "builds it",
      line: "Smallest change that runs. I won’t rewrite your whole file unless you ask.",
      accent: "#4af",
    },
    code_review: {
      mention: "@Review",
      job: "checks the diff",
      line: "I read the patch like it’s already live. Secrets and sharp edges get called early.",
      accent: "#48a",
    },
    checklist: {
      mention: "@Checklist",
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
        opt.textContent = m.free ? `${m.label} · free` : m.label;
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
          <div class="meta">${escapeHtml(card.owner_email || "")} · ${card.progress_percent || 0}%</div>`;
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
    try {
      const data = await api(`/projects/${state.projectId}/analytics`);
      const rows = (data.metrics_by_backend || [])
        .map(
          (r) =>
            `<tr><td>${escapeHtml(r.backend)}</td><td>${escapeHtml(r.model || "")}</td>` +
            `<td>${r.total}</td><td>${r.success}</td><td>${r.fail}</td></tr>`
        )
        .join("");
      body.innerHTML = `
        <h2>Jobs</h2>
        <p>total ${data.jobs_total} · done ${data.jobs_done} · failed ${data.jobs_failed}</p>
        <h2>Metrics by backend</h2>
        <table>
          <tr><th>backend</th><th>model</th><th>total</th><th>ok</th><th>fail</th></tr>
          ${rows || "<tr><td colspan=5>(none yet)</td></tr>"}
        </table>
      `;
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
      label.textContent = `${m.email} (${m.role})`;
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

  function showLoginCard(email, apiKey) {
    const url = lanAppUrl();
    const msg =
      `Added ${email}\n\n` +
      `They only need:\n` +
      `URL: ${url}\n` +
      `email: ${email}\n` +
      `api key: ${apiKey}\n\n` +
      `Api key is always the same for everyone.`;
    window.alert(msg);
    setVoiceStatus(`added ${email}`);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(`URL: ${url}\nemail: ${email}\napi key: ${apiKey}`).catch(() => {});
    }
  }

  function renderMessages(rows, append) {
    const box = $("messages");
    if (!append) box.innerHTML = "";
    rows.forEach((m) => {
      const div = document.createElement("div");
      div.className = "msg" + (m.agent ? " agent" : " user");
      div.style.borderLeftColor = colorForMessage(m);
      const who = m.agent ? `@${m.agent}` : (m.sender || "user");
      let bodyText = m.body || "";
      const confirmMatch = bodyText.match(/\[\[confirm:([0-9,\s]+)\]\]/);
      const confirmIds = confirmMatch
        ? confirmMatch[1].split(",").map((s) => s.trim()).filter(Boolean)
        : [];
      bodyText = bodyText.replace(/\n?\[\[confirm:[0-9,\s]+\]\]\s*$/, "").trimEnd();
      div.innerHTML = `<div class="meta">${who} · #${m.id}</div><div class="body"></div>`;
      div.querySelector(".body").textContent = bodyText;
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
          yes.onclick = () => void sendBody(`yes ${id}`);
          const no = document.createElement("button");
          no.type = "button";
          no.textContent = "No";
          no.onclick = () => void sendBody(`no ${id}`);
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
    document.querySelectorAll("#teamList li, #myRoomList li").forEach((li) => {
      li.classList.toggle("active", Number(li.dataset.id) === Number(id));
    });
    const rows = await api(`/chats/${id}/messages?after_id=0`);
    renderMessages(rows, false);
  }

  async function poll() {
    if (!state.chatId || !state.apiKey) return;
    try {
      const rows = await api(`/chats/${state.chatId}/messages?after_id=${state.lastMsgId}`);
      if (rows.length) renderMessages(rows, true);
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
    const data = await api(`/chats/${state.chatId}/messages`, {
      method: "POST",
      body: JSON.stringify({ body, speak: $("speakToggle").checked }),
    });
    await afterMessageMeta(data);
  }

  async function send(ev) {
    ev.preventDefault();
    const body = $("input").value.trim();
    if (!body || !state.chatId) return;
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
    const email = window.prompt("Invite email (workspace)");
    if (!email) return;
    try {
      const data = await api("/workspace/invite", {
        method: "POST",
        body: JSON.stringify({ email: email.trim() }),
      });
      await refreshSidebar();
      showLoginCard(data.email, data.api_key_issued);
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  async function startRecording() {
    if (state.recording || !$("voiceToggle").checked) return;
    setVoiceStatus("requesting mic…");
    try {
      state.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      setVoiceStatus("mic blocked — allow microphone access");
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
    setVoiceStatus("listening… click stop when done");
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
    setVoiceStatus("transcribing…");
    try {
      const form = new FormData();
      form.append("file", blob, "voice.webm");
      const data = await api("/stt", { method: "POST", body: form });
      const text = (data.text || "").trim();
      if (!text) {
        setVoiceStatus("empty transcript — try again");
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

  const AGENTS = ["Lead", "Research", "Writing", "Code", "Review", "Checklist", "team"];

  function mentionCandidates(prefix) {
    const p = (prefix || "").toLowerCase();
    const out = [];
    AGENTS.forEach((a) => {
      if (!p || a.toLowerCase().startsWith(p)) out.push({ label: `@${a}`, insert: `@${a} ` });
    });
    (state.members || []).forEach((m) => {
      const local = (m.email || "").split("@")[0];
      const name = m.name || local;
      if (!p || local.toLowerCase().startsWith(p) || name.toLowerCase().startsWith(p)) {
        out.push({ label: `@${local} (${m.email})`, insert: `@${local} ` });
      }
    });
    return out.slice(0, 12);
  }

  function hideMentions() {
    $("mentionBox").classList.add("hidden");
    $("mentionBox").innerHTML = "";
  }

  function showMentions(items) {
    const box = $("mentionBox");
    box.innerHTML = "";
    if (!items.length) {
      hideMentions();
      return;
    }
    items.forEach((it) => {
      const li = document.createElement("li");
      li.textContent = it.label;
      li.onclick = () => {
        const input = $("input");
        const v = input.value;
        const at = v.lastIndexOf("@");
        input.value = (at >= 0 ? v.slice(0, at) : v) + it.insert;
        hideMentions();
        input.focus();
      };
      box.appendChild(li);
    });
    box.classList.remove("hidden");
  }

  $("input").addEventListener("input", () => {
    const v = $("input").value;
    const at = v.lastIndexOf("@");
    if (at < 0) {
      hideMentions();
      return;
    }
    const after = v.slice(at + 1);
    if (/\s/.test(after)) {
      hideMentions();
      return;
    }
    showMentions(mentionCandidates(after));
  });
  $("input").addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") hideMentions();
  });

  syncMicUi();

  if (state.email && state.apiKey) {
    $("email").value = state.email;
    $("apiKey").value = state.apiKey;
    login();
  }
  state.timer = setInterval(poll, 1500);
})();
