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
    boardTimer: null,
    boardLoading: false,
    boardFingerprint: "",
    projectId: Number(localStorage.getItem("aio_project") || 0) || 1,
    projects: [],
    isOwner: false,
    unreadMentions: 0,
    mentionRows: [],
    boardCards: [],
    pendingAttachments: [],
    lastSyncAt: null,
    editingMsgId: null,
    // chatId -> { skill } while that room's LLM request is in flight
    llmJobs: {},
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
    ask: "#4a9",
    deepresearch: "#3a8",
    research: "#4a9",
    writing: "#a84",
    coding: "#4af",
    code: "#4af",
    code_review: "#48a",
    review: "#48a",
    checklist: "#a6a",
    status: "#7a9",
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
    if (state.boardTimer) {
      clearInterval(state.boardTimer);
      state.boardTimer = null;
    }
    if (tab === "board") {
      void loadBoard();
      // Live refresh so agent_backlog → in_review shows without a manual reload
      state.boardTimer = setInterval(() => {
        if (state.tab === "board" && state.projectId && !state.boardLoading) {
          void loadBoard({ quiet: true });
        }
      }, 3000);
    }
    if (tab === "models") void loadModels();
    if (tab === "analytics") void loadAnalytics();
  }

  const AGENT_LABELS = {
    ask: "Ask",
    deepresearch: "DeepResearch",
    research: "Ask",
    writing: "Writing",
    coding: "Code",
    code_review: "Review",
    checklist: "Checklist",
    status: "Status",
  };

  // Dry coworker notes - no callsigns / units / marketing bios
  const AGENT_PROFILES = {
    ask: {
      mention: "/ask",
      job: "answers questions",
      line: "Plain answers, no ceremony. Attach a file and ask what it is - I'll read it.",
      accent: "#4a9",
    },
    deepresearch: {
      mention: "/deepresearch",
      job: "deep research briefs",
      line: "Long-form analysis with tables, tradeoffs, and next steps. Use when /ask is too thin.",
      accent: "#3a8",
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
    status: {
      mention: "/status",
      job: "catches you up",
      line: "I read the board, issues, and channel trail so quiet workers still show up.",
      accent: "#7a9",
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

  function linkBadge(cls, href, text) {
    const a = document.createElement("a");
    a.className = `badge ${cls}`;
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = text;
    a.onclick = (ev) => ev.stopPropagation();
    a.addEventListener("mousedown", (ev) => ev.stopPropagation());
    return a;
  }

  function shortBranch(branch) {
    const b = String(branch || "");
    return b.length > 22 ? `${b.slice(0, 20)}…` : b;
  }

  function boardFingerprint(board, jobsToday) {
    return JSON.stringify({
      jobs: jobsToday,
      cols: (board.columns || []).map((col) => [
        col.id,
        (col.cards || []).map((c) => [
          c.id,
          c.title,
          c.progress_percent,
          c.github_pr_url || "",
          c.github_pr_number || 0,
          c.repo_url || "",
          c.github_branch || "",
          c.can_merge ? 1 : 0,
          c.open_issue_count || 0,
          (c.claimed_paths || []).join(","),
          c.owner_email || "",
        ]),
      ]),
    });
  }

  async function loadBoard(opts) {
    const quiet = !!(opts && opts.quiet);
    if (!state.projectId) return;
    if (state.boardLoading) return;
    state.boardLoading = true;
    try {
      const board = await api(`/projects/${state.projectId}/board`);
      let jobsToday = "";
      try {
        const sum = await api(`/projects/${state.projectId}/jobs/summary`);
        jobsToday = `jobs: ${sum.total}`;
      } catch (_) { /* ignore */ }
      const backlogCards =
        ((board.columns || []).find((c) => c.id === "agent_backlog") || {}).cards || [];
      const workingNote = backlogCards.length
        ? ` · agent working on ${backlogCards.length} card${backlogCards.length === 1 ? "" : "s"}`
        : "";
      $("boardFooter").textContent = `${jobsToday}${workingNote}`;

      const fp = boardFingerprint(board, jobsToday + workingNote);
      if (quiet && fp === state.boardFingerprint) {
        return;
      }
      state.boardFingerprint = fp;

      const root = $("boardColumns");
      root.innerHTML = "";

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
            if (status === "agent_backlog") {
              setVoiceStatus(
                `Objective #${oid}: agent started - stay on Board, it updates live`
              );
            }
            state.boardFingerprint = "";
            await api(`/projects/${state.projectId}/objectives/${oid}`, {
              method: "PATCH",
              body: JSON.stringify({ status }),
            });
            await loadBoard();
          } catch (e) {
            setVoiceStatus(String(e.message || e));
            state.boardFingerprint = "";
            await loadBoard();
          }
        });

        (col.cards || []).forEach((card) => {
          const c = document.createElement("div");
          c.className = "board-card";
          if (col.id === "agent_backlog") c.classList.add("agent-working");
          c.draggable = canDragCard(card);
          c.dataset.id = card.id;
          c.innerHTML = `<div class="t">${escapeHtml(card.title)}</div>
          <div class="meta">${escapeHtml(card.owner_email || "")} - ${card.progress_percent || 0}%</div>`;
          if (col.id === "agent_backlog") {
            const w = document.createElement("span");
            w.className = "badge working";
            w.textContent = "agent working…";
            c.appendChild(w);
          }
          if (card.pr_url || card.github_pr_url) {
            c.appendChild(
              linkBadge(
                "pr-link",
                card.pr_url || card.github_pr_url,
                card.pr_number || card.github_pr_number
                  ? `PR #${card.pr_number || card.github_pr_number}`
                  : "PR"
              )
            );
          } else if (col.id === "in_review") {
            const none = document.createElement("span");
            none.className = "badge muted";
            none.textContent = "no PR yet";
            c.appendChild(none);
          }
          if (card.repo_url) {
            c.appendChild(linkBadge("repo-link", card.repo_url, "repo"));
          }
          if (card.branch_url) {
            c.appendChild(
              linkBadge("branch-link", card.branch_url, shortBranch(card.github_branch))
            );
          }
          if (card.open_issue_count) {
            const b = document.createElement("span");
            b.className = "badge";
            b.textContent = `${card.open_issue_count} blocker`;
            c.appendChild(b);
          }
          if (card.can_merge && state.isOwner) {
            c.appendChild(mergeButton({ ...card, status: col.id }));
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
    } catch (e) {
      if (!quiet) setVoiceStatus(String(e.message || e));
    } finally {
      state.boardLoading = false;
    }
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  function looksLikeMarkdown(text) {
    const t = String(text || "");
    if (!t) return false;
    return /(^|\n)\s{0,3}#{1,4}\s|(^|\n)```|(^|\n)\s*[-*+]\s|(^|\n)\s*\d+\.\s|(^|\n)>\s|\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\[[^\]]+\]\([^)]+\)|(^|\n)\|.+\|/.test(t);
  }

  function formatInlineMarkdown(raw) {
    let s = escapeHtml(raw);
    // code first so we don't format inside ticks
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
    s = s.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+|mailto:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    s = s.replace(
      /(^|[\s(])(https?:\/\/[^\s<]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
    );
    s = s.replace(
      /(^|[\s(])(mailto:[^\s<]+)/gi,
      (_, pre, href) => `${pre}<a href="${href}">${href.replace(/^mailto:/i, "")}</a>`
    );
    return s;
  }

  function renderMarkdownHtml(text) {
    const src = String(text || "").replace(/\r\n/g, "\n");
    const blocks = [];
    const fenceRe = /```([a-zA-Z0-9_-]*)\n?([\s\S]*?)```/g;
    let last = 0;
    let m;
    while ((m = fenceRe.exec(src)) !== null) {
      if (m.index > last) blocks.push({ type: "md", text: src.slice(last, m.index) });
      blocks.push({ type: "code", lang: m[1] || "", code: m[2].replace(/\n$/, "") });
      last = m.index + m[0].length;
    }
    if (last < src.length) blocks.push({ type: "md", text: src.slice(last) });

    const flushList = (out, kind, items) => {
      if (!items.length) return;
      const tag = kind === "ol" ? "ol" : "ul";
      out.push(
        `<${tag} class="md-list">${items
          .map((it) => `<li>${formatInlineMarkdown(it)}</li>`)
          .join("")}</${tag}>`
      );
      items.length = 0;
    };

    const isTableRow = (line) => /^\s*\|.+\|\s*$/.test(line);
    const isTableSep = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
    const splitCells = (line) =>
      line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((c) => c.trim());

    const flushTable = (out, rows) => {
      if (rows.length < 2) return false;
      const head = splitCells(rows[0]);
      const bodyRows = rows.slice(2).filter((r) => isTableRow(r));
      if (!head.length) return false;
      const thead = `<thead><tr>${head
        .map((c) => `<th>${formatInlineMarkdown(c)}</th>`)
        .join("")}</tr></thead>`;
      const tbody = `<tbody>${bodyRows
        .map((r) => {
          const cells = splitCells(r);
          return `<tr>${head
            .map((_, i) => `<td>${formatInlineMarkdown(cells[i] || "")}</td>`)
            .join("")}</tr>`;
        })
        .join("")}</tbody>`;
      out.push(`<div class="md-table-wrap"><table class="md-table">${thead}${tbody}</table></div>`);
      return true;
    };

    const out = [];
    blocks.forEach((block) => {
      if (block.type === "code") {
        const lang = block.lang ? ` data-lang="${escapeAttr(block.lang)}"` : "";
        out.push(
          `<pre class="md-pre"${lang}><code>${escapeHtml(block.code)}</code></pre>`
        );
        return;
      }

      const lines = block.text.replace(/^\n+|\n+$/g, "").split("\n");
      let listKind = null;
      let listItems = [];
      let para = [];
      let tableBuf = [];

      const flushPara = () => {
        if (!para.length) return;
        const text = para.join("\n").trim();
        para = [];
        if (!text) return;
        out.push(`<p class="md-p">${formatInlineMarkdown(text).replace(/\n/g, "<br />")}</p>`);
      };

      const flushTableBuf = () => {
        if (!tableBuf.length) return;
        if (!flushTable(out, tableBuf)) {
          tableBuf.forEach((row) => para.push(row));
          flushPara();
        }
        tableBuf = [];
      };

      lines.forEach((line) => {
        const trimmed = line.trimEnd();
        if (!trimmed.trim()) {
          flushTableBuf();
          flushList(out, listKind, listItems);
          listKind = null;
          flushPara();
          return;
        }

        if (isTableRow(trimmed) || (tableBuf.length && isTableSep(trimmed))) {
          flushList(out, listKind, listItems);
          listKind = null;
          flushPara();
          tableBuf.push(trimmed);
          return;
        }
        flushTableBuf();

        if (/^\s*(-{3,}|_{3,}|\*{3,})\s*$/.test(trimmed)) {
          flushList(out, listKind, listItems);
          listKind = null;
          flushPara();
          out.push('<hr class="md-hr" />');
          return;
        }

        const heading = trimmed.match(/^\s*(#{1,4})\s+(.+)$/);
        if (heading) {
          flushList(out, listKind, listItems);
          listKind = null;
          flushPara();
          const level = heading[1].length;
          out.push(
            `<h${level} class="md-h">${formatInlineMarkdown(heading[2].trim())}</h${level}>`
          );
          return;
        }

        const ul = trimmed.match(/^\s*[-*+]\s+(.+)$/);
        if (ul) {
          flushPara();
          if (listKind && listKind !== "ul") {
            flushList(out, listKind, listItems);
          }
          listKind = "ul";
          listItems.push(ul[1]);
          return;
        }

        const ol = trimmed.match(/^\s*\d+\.\s+(.+)$/);
        if (ol) {
          flushPara();
          if (listKind && listKind !== "ol") {
            flushList(out, listKind, listItems);
          }
          listKind = "ol";
          listItems.push(ol[1]);
          return;
        }

        const quote = trimmed.match(/^\s*>\s?(.*)$/);
        if (quote) {
          flushList(out, listKind, listItems);
          listKind = null;
          flushPara();
          out.push(
            `<blockquote class="md-quote">${formatInlineMarkdown(quote[1])}</blockquote>`
          );
          return;
        }

        flushList(out, listKind, listItems);
        listKind = null;
        para.push(trimmed);
      });

      flushTableBuf();
      flushList(out, listKind, listItems);
      flushPara();
    });
    return out.join("");
  }

  function openMergeModal(card) {
    const modal = $("mergeModal");
    const status = $("mergeModalStatus");
    const confirmBtn = $("mergeConfirm");
    const cancelBtn = $("mergeCancel");
    status.hidden = true;
    status.textContent = "";
    confirmBtn.disabled = false;
    const prNum = card.pr_number || card.github_pr_number;
    $("mergeModalBody").innerHTML = `
      <p class="meta">#${card.id} ${escapeHtml(card.title)}</p>
      <p class="meta">pr: <a href="${escapeHtml(card.pr_url || card.github_pr_url || "")}" target="_blank" rel="noopener noreferrer">#${prNum}</a></p>
      <p class="meta">branch: ${escapeHtml(card.github_branch || "-")}</p>
      <p class="meta">method: squash</p>
    `;
    modal.classList.remove("hidden");

    const close = () => {
      modal.classList.add("hidden");
      confirmBtn.onclick = null;
      cancelBtn.onclick = null;
    };
    cancelBtn.onclick = close;
    confirmBtn.onclick = async () => {
      confirmBtn.disabled = true;
      status.hidden = false;
      status.textContent = "merging…";
      try {
        const out = await api(
          `/projects/${state.projectId}/objectives/${card.id}/merge`,
          { method: "POST", body: JSON.stringify({ confirm: true }) }
        );
        close();
        setVoiceStatus(`Merged PR #${prNum} into ${out.base || "main"} - card is done`);
        state.boardFingerprint = "";
        await loadBoard();
      } catch (e) {
        confirmBtn.disabled = false;
        status.textContent = String(e.message || e);
      }
    };
  }

  function mergeButton(card) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "merge-btn";
    b.textContent = "Merge & done";
    b.onclick = (ev) => {
      ev.stopPropagation();
      openMergeModal(card);
    };
    return b;
  }

  function githubPanelHtml(card) {
    const rows = [];
    const link = (href, text) =>
      `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)}</a>`;
    if (card.repo_url) rows.push(`<p class="meta">repo: ${link(card.repo_url, card.repo_url.replace("https://github.com/", ""))}</p>`);
    const prUrl = card.pr_url || card.github_pr_url;
    const prNum = card.pr_number || card.github_pr_number;
    if (prUrl) {
      rows.push(`<p class="meta">pr: ${link(prUrl, prNum ? `#${prNum}` : "open PR")}</p>`);
    } else if (card.status === "in_review") {
      rows.push(`<p class="meta">pr: none yet</p>`);
    }
    if (card.branch_url) {
      rows.push(`<p class="meta">branch: ${link(card.branch_url, card.github_branch)}</p>`);
    } else if (card.github_branch) {
      rows.push(`<p class="meta">branch: ${escapeHtml(card.github_branch)}</p>`);
    }
    rows.push(`<p class="meta">workspace: <code>data/workspaces/obj-${card.id}</code></p>`);
    return rows.join("");
  }

  function showBoardPanel(card) {
    const panel = $("boardPanel");
    panel.classList.remove("hidden");
    const subs = (card.subtasks || [])
      .map(
        (t) =>
          `<li class="${t.done ? "done" : ""}">${t.done ? "✓" : "○"} ${escapeHtml(t.title)}</li>`
      )
      .join("");
    const desc = (card.description || "").trim();
    $("boardPanelBody").innerHTML = `
      <h3>#${card.id} ${escapeHtml(card.title)}</h3>
      ${desc ? `<p class="obj-desc">${escapeHtml(desc)}</p>` : ""}
      <p class="meta">owner: ${escapeHtml(card.owner_email || "")}</p>
      <p class="meta">status: ${escapeHtml(card.status)}</p>
      <p class="meta">subtasks: ${card.checklist_closed}/${card.checklist_total}</p>
      ${subs ? `<ul class="obj-subtasks">${subs}</ul>` : ""}
      <p class="meta">issues: ${card.open_issue_count}</p>
      ${githubPanelHtml(card)}
      <p id="panelMergeSlot"></p>
      <p><button type="button" id="openInChat">open in chat</button></p>
    `;
    if (card.can_merge && state.isOwner) {
      const slot = document.getElementById("panelMergeSlot");
      if (slot) slot.appendChild(mergeButton(card));
    }
    const btn = document.getElementById("openInChat");
    if (btn) {
      btn.onclick = () => {
        setTab("chat");
        setVoiceStatus(`objective #${card.id}: ${card.title}`);
      };
    }
  }

  function dismissSetupCard(card) {
    if (card && card.parentNode) card.parentNode.removeChild(card);
  }

  function mountSetupCard(parent, objectiveId, titleHint) {
    const card = document.createElement("div");
    card.className = "setup-card";
    card.dataset.objectiveId = String(objectiveId);
    const title = titleHint || `Objective #${objectiveId}`;
    card.innerHTML = `
      <div class="setup-head">
        <div class="setup-kicker">New objective</div>
        <div class="setup-title">${escapeHtml(title)}</div>
        <div class="setup-hint">Optional - add a short brief and subtasks, or skip.</div>
      </div>
      <label class="setup-label">Description
        <textarea class="setup-desc" rows="3" placeholder="What does done look like?"></textarea>
      </label>
      <div class="setup-subs-head">
        <span>Subtasks</span>
        <button type="button" class="setup-add-sub">+ add</button>
      </div>
      <ul class="setup-subs"></ul>
      <div class="setup-actions">
        <button type="button" class="setup-save">Save</button>
        <button type="button" class="setup-skip">Skip</button>
      </div>
      <div class="setup-status" hidden></div>
    `;
    const list = card.querySelector(".setup-subs");
    const addRow = (value) => {
      const li = document.createElement("li");
      li.className = "setup-sub-row";
      const input = document.createElement("input");
      input.type = "text";
      input.className = "setup-sub-input";
      input.placeholder = "Subtask…";
      input.value = value || "";
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "setup-sub-rm";
      rm.textContent = "×";
      rm.onclick = () => li.remove();
      li.appendChild(input);
      li.appendChild(rm);
      list.appendChild(li);
      input.focus();
    };
    card.querySelector(".setup-add-sub").onclick = () => addRow("");
    card.querySelector(".setup-skip").onclick = async () => {
      const status = card.querySelector(".setup-status");
      const pid = state.projectId;
      if (!pid) {
        dismissSetupCard(card);
        return;
      }
      status.hidden = false;
      status.textContent = "Closing…";
      try {
        await api(`/projects/${pid}/objectives/${objectiveId}/setup`, {
          method: "PUT",
          body: JSON.stringify({ dismiss: true }),
        });
        dismissSetupCard(card);
      } catch (e) {
        status.textContent = e.message || String(e);
      }
    };
    card.querySelector(".setup-save").onclick = async () => {
      const status = card.querySelector(".setup-status");
      const desc = card.querySelector(".setup-desc").value;
      const subtasks = [...card.querySelectorAll(".setup-sub-input")]
        .map((el) => el.value.trim())
        .filter(Boolean);
      const pid = state.projectId;
      if (!pid) {
        status.hidden = false;
        status.textContent = "No project - open board once, then retry.";
        return;
      }
      status.hidden = false;
      status.textContent = "Saving…";
      try {
        await api(`/projects/${pid}/objectives/${objectiveId}/setup`, {
          method: "PUT",
          body: JSON.stringify({ description: desc, subtasks }),
        });
        status.textContent = "Saved.";
        setTimeout(() => dismissSetupCard(card), 450);
        setVoiceStatus(`objective #${objectiveId} updated`);
      } catch (e) {
        status.textContent = e.message || String(e);
      }
    };
    parent.appendChild(card);
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

    if allowDelete && !(c.name === "general" && c.kind === "channel") {
      const isDefaultPrivate =
        c.kind === "private" && String(c.name || "").toLowerCase().startsWith("private -");
      const ownerId = Number(c.owner_user_id || 0);
      const mine = ownerId && ownerId === Number(state.userId);
      const orphanPublic =
        state.isOwner && c.kind === "channel" && !ownerId;
      if (isDefaultPrivate || (!mine && !orphanPublic)) {
        /* protected or not yours */
      } else {
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
    mine.forEach((c) => myRoomList.appendChild(renderChatLi(c, { allowDelete: true })));

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
      if (state.isOwner && Number(m.user_id) !== Number(state.userId)) {
        const kick = document.createElement("button");
        kick.type = "button";
        kick.className = "x";
        kick.title = "remove member";
        kick.textContent = "x";
        kick.onclick = (ev) => {
          ev.stopPropagation();
          void kickMember(m);
        };
        li.appendChild(kick);
      }
      memberList.appendChild(li);
    });
  }

  async function kickMember(m) {
    const name = m.name || m.email;
    if (!window.confirm(`Remove ${name} from the workspace? This deletes their account.`)) return;
    try {
      await api(`/workspace/members/${m.user_id}`, { method: "DELETE" });
      setVoiceStatus(`removed ${name}`);
      await refreshSidebar();
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
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
    const raw = String(text || "");
    if (looksLikeMarkdown(raw) || raw.includes("\n")) {
      el.classList.add("md");
      el.innerHTML = renderMarkdownHtml(raw);
      return;
    }
    el.classList.remove("md");
    el.textContent = "";
    const parts = raw.split(/(https?:\/\/\S+|mailto:[^\s]+)/g);
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

  function showInviteLink(data) {
    const url = typeof data === "string" ? data : data.invite_url;
    const uses = Number((data && data.max_uses) || 1);
    const msg =
      uses <= 1
        ? `Invite link (1 use).\nAfter someone registers it expires - run !invite or !invite 5 for more seats.\n\n${url}`
        : `Invite link (${uses} uses).\nSeat count drops as people register.\n\n${url}`;
    window.alert(msg);
    setVoiceStatus("invite link ready");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).catch(() => {});
    }
  }

  const CHAT_TZ = "Asia/Dubai";

  function parseMsgDate(iso) {
    if (!iso) return null;
    let s = String(iso).trim();
    // SQLite/UTC rows often arrive naive ("2026-08-06 09:23:29") - treat as UTC
    if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
      s = s.replace(" ", "T");
      if (!s.endsWith("Z")) s += "Z";
    }
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function dubaiParts(d) {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: CHAT_TZ,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      weekday: "long",
    }).formatToParts(d);
    const get = (type) => (parts.find((p) => p.type === type) || {}).value;
    return {
      year: Number(get("year")),
      month: Number(get("month")),
      day: Number(get("day")),
      weekday: get("weekday"),
      hour: get("hour"),
      minute: get("minute"),
      dayPeriod: get("dayPeriod"),
    };
  }

  function dayKey(d) {
    const p = dubaiParts(d);
    return `${p.year}-${p.month}-${p.day}`;
  }

  function formatMsgTime(d) {
    return d.toLocaleTimeString("en-GB", {
      timeZone: CHAT_TZ,
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
  }

  function formatDayLabel(d) {
    const msg = dubaiParts(d);
    const now = dubaiParts(new Date());
    const msgUtc = Date.UTC(msg.year, msg.month - 1, msg.day);
    const nowUtc = Date.UTC(now.year, now.month - 1, now.day);
    const diffDays = Math.round((nowUtc - msgUtc) / 86400000);
    if (diffDays === 0) return "Today";
    if (diffDays === 1) return "Yesterday";
    if (diffDays > 1 && diffDays < 7) {
      return d.toLocaleDateString("en-GB", {
        timeZone: CHAT_TZ,
        weekday: "long",
        month: "short",
        day: "numeric",
      });
    }
    const opts = { timeZone: CHAT_TZ, month: "short", day: "numeric" };
    if (msg.year !== now.year) opts.year = "numeric";
    return d.toLocaleDateString("en-GB", opts);
  }

  function lastRenderedDayKey(box) {
    const nodes = box.querySelectorAll(".msg[data-day]");
    if (!nodes.length) return null;
    return nodes[nodes.length - 1].dataset.day || null;
  }

  function appendDaySeparator(box, d) {
    const sep = document.createElement("div");
    sep.className = "msg-day";
    sep.setAttribute("role", "separator");
    const label = document.createElement("span");
    label.textContent = formatDayLabel(d);
    sep.appendChild(label);
    box.appendChild(sep);
  }

  function isOwnMessage(m) {
    if (m.agent) return false;
    if (m.sender_user_id != null && state.userId != null) {
      return Number(m.sender_user_id) === Number(state.userId);
    }
    if (m.sender_email && state.email) {
      return String(m.sender_email).toLowerCase() === String(state.email).toLowerCase();
    }
    return false;
  }

  function recomputeLastMsgId() {
    const box = $("messages");
    let max = 0;
    if (box) {
      box.querySelectorAll(".msg[data-msg-id]").forEach((el) => {
        max = Math.max(max, Number(el.dataset.msgId) || 0);
      });
    }
    state.lastMsgId = max;
  }

  function cleanOrphanDaySeparators(box) {
    if (!box) return;
    const kids = [...box.children];
    kids.forEach((el, i) => {
      if (!el.classList.contains("msg-day")) return;
      const next = kids[i + 1];
      if (!next || next.classList.contains("msg-day")) el.remove();
    });
    const last = box.lastElementChild;
    if (last && last.classList.contains("msg-day")) last.remove();
  }

  /** Remove messages after editedId (ChatGPT branch truncate) and any explicit ids. */
  function removeMessagesFromDom(removedIds, afterId) {
    const box = $("messages");
    if (!box) return;
    const extra = new Set((removedIds || []).map(Number));
    const floor = afterId != null ? Number(afterId) : null;
    [...box.querySelectorAll(".msg[data-msg-id]")].forEach((el) => {
      const id = Number(el.dataset.msgId);
      if (extra.has(id) || (floor != null && id > floor)) el.remove();
    });
    cleanOrphanDaySeparators(box);
    recomputeLastMsgId();
  }

  function focusMessageEl(el) {
    if (!el) return;
    el.classList.add("highlight-ping");
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => el.classList.remove("highlight-ping"), 2500);
  }

  function fillMessageContent(div, m) {
    const color = colorForMessage(m);
    div.style.borderLeftColor = color;
    const who = m.agent ? `@${m.agent}` : (m.sender || "user");
    const whisperTag = m.visibility === "whisper" ? " - only you" : "";
    const when = parseMsgDate(m.created_at);
    const timeText = when ? formatMsgTime(when) : "";
    const edited = !m.deleted_at && m.edited_at;
    const mine = isOwnMessage(m) && !m.deleted_at;

    div.className =
      "msg" +
      (m.agent ? " agent" : " user") +
      (m.visibility === "whisper" ? " whisper" : "") +
      (mine ? " mine" : "") +
      (m.deleted_at ? " deleted" : "");

    let bodyText = m.deleted_at ? "" : (m.body || "");
    const confirmMatch = bodyText.match(/\[\[confirm:([0-9,\s]+)\]\]/);
    const confirmIds = confirmMatch
      ? confirmMatch[1].split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    const setupMatch = bodyText.match(/\[\[setup:(\d+)\]\]/);
    const setupId = setupMatch ? setupMatch[1] : null;
    bodyText = bodyText
      .replace(/\n?\[\[confirm:[0-9,\s]+\]\]\s*$/, "")
      .replace(/\n?\[\[setup:\d+\]\]\s*$/, "")
      .trimEnd();
    const titleFromBody = (bodyText.match(/Added objective #\d+:\s*(.+?)(?:\s*\(yours\))?$/m) || [])[1];

    div.innerHTML =
      `<div class="meta"><span class="who"></span>${whisperTag}` +
      `<span class="msg-time"></span>` +
      `<span class="msg-edited"></span>` +
      `<span class="msg-id"> · #${m.id}</span></div>` +
      `<div class="body"></div>`;

    const whoEl = div.querySelector(".who");
    whoEl.textContent = who;
    whoEl.style.color = color;
    const timeEl = div.querySelector(".msg-time");
    if (timeText) {
      timeEl.textContent = ` · ${timeText}`;
      timeEl.title = when.toLocaleString("en-GB", { timeZone: CHAT_TZ });
    }
    const editedEl = div.querySelector(".msg-edited");
    if (edited) {
      editedEl.textContent = " · edited";
      editedEl.title = parseMsgDate(m.edited_at)
        ? parseMsgDate(m.edited_at).toLocaleString("en-GB", { timeZone: CHAT_TZ })
        : "edited";
    }

    const bodyEl = div.querySelector(".body");
    if (m.deleted_at) {
      bodyEl.classList.add("msg-deleted-label");
      bodyEl.textContent = "message deleted";
    } else {
      setMessageBody(bodyEl, bodyText);
      if (setupId) {
        mountSetupCard(div, setupId, titleFromBody ? `#${setupId} ${titleFromBody}` : `Objective #${setupId}`);
      }
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
      }
      if (m.attachments && m.attachments.length) {
        mountMessageAttachments(div, m.attachments);
      }
    }

    if (mine) {
      const tools = document.createElement("div");
      tools.className = "msg-tools";
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "msg-tool";
      editBtn.textContent = "edit";
      editBtn.onclick = (ev) => {
        ev.stopPropagation();
        beginEditMessage(div, m);
      };
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "msg-tool danger";
      delBtn.textContent = "delete";
      delBtn.onclick = (ev) => {
        ev.stopPropagation();
        void deleteOwnMessage(m.id);
      };
      tools.appendChild(editBtn);
      tools.appendChild(delBtn);
      div.appendChild(tools);
    }
  }

  function beginEditMessage(div, m) {
    if (!state.chatId || m.deleted_at) return;
    if (state.editingMsgId && Number(state.editingMsgId) !== Number(m.id)) {
      // cancel other edit by re-polling that bubble later - force single edit
      const prev = document.querySelector(`.msg[data-msg-id="${state.editingMsgId}"]`);
      if (prev && prev._editCancel) prev._editCancel();
    }
    state.editingMsgId = m.id;
    div.classList.add("editing");
    const bodyEl = div.querySelector(".body");
    const tools = div.querySelector(".msg-tools");
    if (tools) tools.classList.add("hidden");
    const original = m.body || "";
    const form = document.createElement("div");
    form.className = "msg-edit-form";
    const input = document.createElement("textarea");
    input.className = "msg-edit-input";
    input.rows = Math.min(8, Math.max(2, String(original).split("\n").length));
    input.value = original;
    const row = document.createElement("div");
    row.className = "msg-edit-actions";
    const save = document.createElement("button");
    save.type = "button";
    save.textContent = "save";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "cancel";
    row.appendChild(save);
    row.appendChild(cancel);
    form.appendChild(input);
    form.appendChild(row);

    const finish = () => {
      state.editingMsgId = null;
      div.classList.remove("editing");
      div._editCancel = null;
    };

    div._editCancel = () => {
      finish();
      fillMessageContent(div, m);
    };

    cancel.onclick = () => div._editCancel();
    save.onclick = async () => {
      const next = input.value.trim();
      if (!next) {
        setVoiceStatus("message cannot be empty");
        return;
      }
      const chatId = state.chatId;
      const expectsLlm = looksLikeAgentWork(next);
      if (expectsLlm) beginLlmJob(chatId, next);
      save.disabled = true;
      cancel.disabled = true;
      try {
        const data = await api(`/chats/${chatId}/messages/${m.id}`, {
          method: "PATCH",
          body: JSON.stringify({ body: next }),
        });
        const updated = data.message || data;
        const removedIds = data.removed_ids || [];
        const replies = data.replies || [];
        finish();
        if (Number(state.chatId) === Number(chatId)) {
          removeMessagesFromDom(removedIds, updated.id);
          fillMessageContent(div, updated);
          if (replies.length) renderMessages(replies, true);
          else recomputeLastMsgId();
          focusMessageEl(div);
          state.lastSyncAt = new Date().toISOString();
        }
      } catch (e) {
        setVoiceStatus(String(e.message || e));
        save.disabled = false;
        cancel.disabled = false;
      } finally {
        if (expectsLlm) endLlmJob(chatId);
        syncComposerForActiveChat();
      }
    };
    input.onkeydown = (ev) => {
      if (ev.key === "Escape") {
        ev.preventDefault();
        div._editCancel();
      }
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
        ev.preventDefault();
        save.click();
      }
    };

    if (bodyEl) bodyEl.replaceWith(form);
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }

  async function deleteOwnMessage(messageId) {
    if (!state.chatId) return;
    if (!window.confirm("Delete this message for everyone?")) return;
    try {
      const data = await api(`/chats/${state.chatId}/messages/${messageId}`, {
        method: "DELETE",
      });
      const removed = [messageId, ...((data && data.removed_ids) || [])];
      removeMessagesFromDom(removed, null);
      state.lastSyncAt = new Date().toISOString();
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  function renderMessages(rows, append) {
    const box = $("messages");
    if (!append) box.innerHTML = "";
    let prevDay = append ? lastRenderedDayKey(box) : null;
    let shouldStick = !append;
    rows.forEach((m) => {
      const existing = append
        ? box.querySelector(`.msg[data-msg-id="${m.id}"]`)
        : null;
      if (m.deleted_at) {
        if (existing) existing.remove();
        cleanOrphanDaySeparators(box);
        recomputeLastMsgId();
        return;
      }
      if (existing) {
        // Don't clobber an in-progress edit
        if (Number(state.editingMsgId) === Number(m.id)) return;
        fillMessageContent(existing, m);
        state.lastMsgId = Math.max(state.lastMsgId, m.id);
        return;
      }
      const when = parseMsgDate(m.created_at);
      const dk = when ? dayKey(when) : null;
      if (dk && dk !== prevDay) {
        appendDaySeparator(box, when);
        prevDay = dk;
      }
      const div = document.createElement("div");
      div.dataset.msgId = String(m.id);
      if (dk) div.dataset.day = dk;
      fillMessageContent(div, m);
      box.appendChild(div);
      state.lastMsgId = Math.max(state.lastMsgId, m.id);
      shouldStick = true;
    });
    if (shouldStick) box.scrollTop = box.scrollHeight;
  }

  function mountMessageAttachments(div, attachments) {
    const wrap = document.createElement("div");
    wrap.className = "msg-attachments";
    attachments.forEach((a) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "msg-attach";
      chip.title = a.filename || "attachment";
      const label = document.createElement("span");
      label.textContent = a.filename || `file #${a.id}`;
      chip.appendChild(label);
      const isImg = String(a.content_type || "").startsWith("image/");
      if (isImg && a.url) {
        const img = document.createElement("img");
        img.alt = a.filename || "";
        chip.insertBefore(img, label);
        void authBlobUrl(a.url)
          .then((url) => {
            img.src = url;
          })
          .catch(() => {
            img.remove();
          });
      }
      chip.onclick = () => void openAttachment(a);
      wrap.appendChild(chip);
    });
    div.appendChild(wrap);
  }

  async function authBlobUrl(path) {
    const res = await fetch(path, { headers: headers(false) });
    if (!res.ok) throw new Error("download failed");
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  async function openAttachment(a) {
    try {
      const url = await authBlobUrl(a.url);
      const w = window.open(url, "_blank", "noopener,noreferrer");
      if (!w) {
        const link = document.createElement("a");
        link.href = url;
        link.download = a.filename || "download";
        link.click();
      }
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    }
  }

  function renderPendingAttachments() {
    const box = $("attachPending");
    if (!box) return;
    const rows = state.pendingAttachments || [];
    box.innerHTML = "";
    if (!rows.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    rows.forEach((a) => {
      const chip = document.createElement("div");
      chip.className = "attach-chip";
      const name = document.createElement("span");
      name.className = "name";
      name.textContent = a.filename;
      const rm = document.createElement("button");
      rm.type = "button";
      rm.textContent = "×";
      rm.title = "remove";
      rm.onclick = () => {
        state.pendingAttachments = state.pendingAttachments.filter((x) => x.id !== a.id);
        renderPendingAttachments();
      };
      chip.appendChild(name);
      chip.appendChild(rm);
      box.appendChild(chip);
    });
  }

  async function uploadPendingFiles(fileList) {
    if (!state.chatId || !fileList || !fileList.length) return;
    const room = 5 - (state.pendingAttachments || []).length;
    if (room <= 0) {
      setVoiceStatus("at most 5 attachments per message");
      return;
    }
    const files = Array.from(fileList).slice(0, room);
    setComposerBusy(true);
    try {
      for (const file of files) {
        const form = new FormData();
        form.append("file", file, file.name);
        const att = await api(`/chats/${state.chatId}/attachments`, {
          method: "POST",
          body: form,
        });
        state.pendingAttachments.push(att);
      }
      renderPendingAttachments();
    } catch (e) {
      setVoiceStatus(String(e.message || e));
    } finally {
      setComposerBusy(false);
    }
  }

  async function selectChat(id) {
    state.chatId = id;
    state.lastMsgId = 0;
    state.lastSyncAt = null;
    state.editingMsgId = null;
    state.pendingAttachments = [];
    renderPendingAttachments();
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
    state.lastSyncAt = new Date().toISOString();
    renderMessages(rows, false);
    // Per-room LLM lock: other chats stay typable while a private skill runs
    syncComposerForActiveChat();
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
      const since = state.lastSyncAt ? `&since=${encodeURIComponent(state.lastSyncAt)}` : "";
      const rows = await api(
        `/chats/${state.chatId}/messages?after_id=${state.lastMsgId}${since}`
      );
      state.lastSyncAt = new Date().toISOString();
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

  async function sendBody(body, attachmentIds) {
    const chatId = state.chatId;
    const ids = attachmentIds || [];
    if ((!body && !ids.length) || !chatId) return;
    const expectsLlm = !!(body && looksLikeAgentWork(body));
    if (expectsLlm) {
      beginLlmJob(chatId, body);
    } else if (Number(state.chatId) === Number(chatId)) {
      setComposerBusy(true);
    }
    try {
      const data = await api(`/chats/${chatId}/messages`, {
        method: "POST",
        body: JSON.stringify({
          body: body || "",
          speak: false,
          attachment_ids: ids,
        }),
      });
      if (Number(state.chatId) === Number(chatId)) {
        await afterMessageMeta(data);
      } else if (data.created_chat_id || data.deleted_chat_id) {
        await refreshSidebar();
      }
    } finally {
      if (expectsLlm) endLlmJob(chatId);
      else setComposerBusy(false);
      syncComposerForActiveChat();
    }
  }

  function skillNameFromBody(body) {
    const t = String(body || "").trim();
    const m = t.match(/^\/(ask|deepresearch|deep-research|code|research|write|web|review|checklist|status)\b/i);
    if (m) return m[1].toLowerCase().replace(/-/g, "");
    const m2 = t.match(/^(?:force\s+)?(code|ask|deepresearch|research|write|review)\b/i);
    return m2 ? m2[1].toLowerCase() : "";
  }

  function looksLikeAgentWork(body) {
    const t = String(body || "").trim();
    if (!t) return false;
    if (/^\/clear\b/i.test(t) || /^!clear\b/i.test(t)) return false;
    // /status works in any room; other skills are private-only
    if (/^\/status\b/i.test(t)) return true;
    if (/^\/(ask|deepresearch|deep-research|code|research|write|web|review|checklist)\b/i.test(t)) return true;
    if (/^(force\s+)?(code|ask|deepresearch|research|write|review)\b/i.test(t)) return true;
    const meta = currentChatMeta();
    if (meta && meta.kind === "private" && t.startsWith("/")) return true;
    return false;
  }

  let llmPendingEl = null;

  function activeLlmJob() {
    if (!state.chatId) return null;
    return (state.llmJobs || {})[Number(state.chatId)] || null;
  }

  function setComposerBusy(on) {
    $("composer").classList.toggle("busy", !!on);
    const sendBtn = $("sendBtn");
    if (sendBtn) sendBtn.disabled = !!on;
    const input = $("input");
    if (!input) return;
    input.disabled = !!on;
    // Disabling the input drops focus - put it back so you can keep typing
    if (!on) {
      requestAnimationFrame(() => {
        if (!input.disabled) input.focus();
      });
    }
  }

  function syncComposerForActiveChat() {
    const job = activeLlmJob();
    setComposerBusy(!!job);
    renderLlmWaitUi(job);
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
      `<div class="body"><span class="llm-dots">Thinking</span> - generating a reply` +
      `<div class="llm-pending-track"><div class="llm-pending-bar"></div></div></div>`;
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

  function renderLlmWaitUi(job) {
    const box = $("llmWait");
    const bar = $("llmWaitBar");
    const label = $("llmWaitLabel");
    const hint = $("llmWaitHint");
    if (!box || !bar) return;
    if (!job) {
      removePendingBubble();
      box.classList.add("hidden");
      bar.classList.remove("indeterminate");
      return;
    }
    const skill = job.skill || "";
    box.classList.remove("hidden");
    bar.classList.add("indeterminate");
    if (label) {
      label.textContent = skill
        ? `Running /${skill} - model is working…`
        : "Agent working - model is generating…";
    }
    if (hint) {
      hint.textContent = skill
        ? `/${skill} in progress in this room - switch chats to keep talking elsewhere`
        : "Model working in this room - switch chats to keep talking elsewhere";
    }
    showPendingBubble(skill);
  }

  function beginLlmJob(chatId, body) {
    const id = Number(chatId);
    if (!state.llmJobs) state.llmJobs = {};
    state.llmJobs[id] = { skill: skillNameFromBody(body) || "" };
    if (Number(state.chatId) === id) syncComposerForActiveChat();
  }

  function endLlmJob(chatId) {
    const id = Number(chatId);
    if (state.llmJobs) delete state.llmJobs[id];
    if (Number(state.chatId) === id) syncComposerForActiveChat();
  }

  async function send(ev) {
    ev.preventDefault();
    const body = $("input").value.trim();
    const ids = (state.pendingAttachments || []).map((a) => a.id);
    if ((!body && !ids.length) || !state.chatId) return;
    // Only block sends in the room that already has an LLM job running
    if (activeLlmJob()) return;
    $("input").value = "";
    state.pendingAttachments = [];
    renderPendingAttachments();
    try {
      await sendBody(body, ids);
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
      const email = window.prompt(
        "Colleague email (empty = link only). Cancel = abort.",
        ""
      );
      if (email === null) return;
      const raw = window.prompt("How many seats? (1-50)", "1");
      if (raw === null) return;
      const n = Math.max(1, Math.min(50, parseInt(raw, 10) || 1));
      const trimmed = (email || "").trim();
      let data;
      if (trimmed && !trimmed.startsWith("@")) {
        data = await api(`/workspace/invite-email`, {
          method: "POST",
          body: JSON.stringify({ email: trimmed, max_uses: n }),
        });
        if (data.outlook && data.outlook.ok) {
          setVoiceStatus(`emailed ${trimmed}`);
        } else if (data.email_error) {
          setVoiceStatus(`link ready, email failed: ${data.email_error}`);
        }
      } else {
        data = await api(`/workspace/invite-link?max_uses=${n}`, { method: "POST" });
      }
      if (data.invite_url) showInviteLink(data);
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
  const attachBtn = $("attachBtn");
  const attachInput = $("attachInput");
  if (attachBtn && attachInput) {
    attachBtn.onclick = () => attachInput.click();
    attachInput.onchange = () => {
      void uploadPendingFiles(attachInput.files);
      attachInput.value = "";
    };
  }
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
    { insert: "!invite ", label: "!invite", blurb: "invite link (optional seats)", args: ["[N]"] },
    { insert: "!clear", label: "!clear", blurb: "clear chat (you only in #general)", args: [] },
    { insert: "!help", label: "!help", blurb: "list commands", args: [] },
  ];

  const SKILL_CATALOG = [
    { insert: "/ask ", label: "/ask", blurb: "just ask anything", args: ["<ask>"] },
    { insert: "/deepresearch ", label: "/deepresearch", blurb: "deep dive with tables", args: ["<ask>"] },
    { insert: "/code ", label: "/code", blurb: "build or patch", args: ["<ask>"] },
    { insert: "/write ", label: "/write", blurb: "draft clear prose", args: ["<ask>"] },
    { insert: "/review ", label: "/review", blurb: "check the diff", args: ["<ask>"] },
    { insert: "/checklist ", label: "/checklist", blurb: "break into ticks", args: ["<ask>"] },
    { insert: "/status ", label: "/status", blurb: "AI member catch-up", args: ["<name>"] },
    { insert: "/clear", label: "/clear", blurb: "clear chat (you only in #general)", args: [] },
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

  function isCompleteCatalogToken(catalog, after) {
    const p = (after || "").toLowerCase();
    if (!p) return false;
    return catalog.some((c) => c.label.slice(1).toLowerCase() === p);
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
        // Full command typed (!list, !invite, !set) - close prefix; args need a space
        if (isCompleteCatalogToken(COMMAND_CATALOG, hit.after)) {
          hideMentions();
          return;
        }
        showPicker(filterCatalog(COMMAND_CATALOG, hit.after), hit.idx, "prefix");
        return;
      }
      if (hit.ch === "/") {
        const channelSlash = [
          { insert: "/status ", label: "/status", blurb: "AI member catch-up", args: ["<name>"] },
          { insert: "/clear", label: "/clear", blurb: "clear for you only", args: [] },
        ];
        if (!meta || meta.kind !== "private") {
          if (isCompleteCatalogToken(channelSlash, hit.after)) {
            hideMentions();
            return;
          }
          showPicker(filterCatalog(channelSlash, hit.after), hit.idx, "prefix");
          return;
        }
        setVoiceStatus("");
        if (isCompleteCatalogToken(SKILL_CATALOG, hit.after)) {
          hideMentions();
          return;
        }
        showPicker(filterCatalog(SKILL_CATALOG, hit.after), hit.idx, "prefix");
        return;
      }
    }
    // Argument stage for completed command tokens (after trailing space)
    const showed = await maybeShowArgPicker();
    if (!showed) hideMentions();
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
