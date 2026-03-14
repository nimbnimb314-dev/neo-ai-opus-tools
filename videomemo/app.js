(() => {
  "use strict";

  const demoTranscript = [
    { time: 8, text: "今日は河川敷スタート、朝の光がきれい。" },
    { time: 26, text: "メモ、引きのショットはオープニングに使えそう。" },
    { time: 41, text: "ここで自転車がフレームイン、テンポが良い。" },
    { time: 67, text: "チェック、風切り音が強いのでBGM前提で考える。" },
    { time: 94, text: "memo この振り向きカットはサムネ候補。" },
    { time: 128, text: "街路樹の影が面白い。色味は少し暖かくしたい。" },
    { time: 166, text: "メモ、転倒シーン前後はスロー候補。" },
    { time: 207, text: "ラストは空に抜ける構図。ここは余韻を残したい。" },
  ];

  const storageKey = "videomemo-session";

  const videoInput = document.getElementById("video-input");
  const modelPathInput = document.getElementById("model-path-input");
  const languageSelect = document.getElementById("language-select");
  const videoPlayer = document.getElementById("video-player");
  const videoEmpty = document.getElementById("video-empty");
  const transcriptInput = document.getElementById("transcript-input");
  const keywordsInput = document.getElementById("keywords-input");
  const loadDemoBtn = document.getElementById("load-demo-btn");
  const resetSessionBtn = document.getElementById("reset-session-btn");
  const transcribeBtn = document.getElementById("transcribe-btn");
  const jobStatus = document.getElementById("job-status");
  const addMarkerBtn = document.getElementById("add-marker-btn");
  const saveMemoBtn = document.getElementById("save-memo-btn");
  const deleteMemoBtn = document.getElementById("delete-memo-btn");
  const copyFullBtn = document.getElementById("copy-full-btn");
  const downloadFullBtn = document.getElementById("download-full-btn");
  const downloadMemoBtn = document.getElementById("download-memo-btn");
  const downloadCsvBtn = document.getElementById("download-csv-btn");
  const downloadXmlBtn = document.getElementById("download-xml-btn");
  const jobProgress = document.getElementById("job-progress");
  const jobProgressBar = document.getElementById("job-progress-bar");
  const jobProgressValue = document.getElementById("job-progress-value");
  const timelineTrack = document.getElementById("timeline-track");
  const timelineProgress = document.getElementById("timeline-progress");
  const timelineMarkers = document.getElementById("timeline-markers");
  const transcriptRibbon = document.getElementById("transcript-ribbon");
  const transcriptList = document.getElementById("transcript-list");
  const memoList = document.getElementById("memo-list");
  const pageLinks = document.querySelector(".page-links");
  const memoTimeInput = document.getElementById("memo-time-input");
  const memoTextInput = document.getElementById("memo-text-input");
  const currentTimeLabel = document.getElementById("current-time-label");
  const timelineEnd = document.getElementById("timeline-end");
  const videoName = document.getElementById("video-name");
  const transcriptCount = document.getElementById("transcript-count");
  const autoMarkerCount = document.getElementById("auto-marker-count");
  const memoCount = document.getElementById("memo-count");

  function initialAppMode() {
    const params = new URLSearchParams(window.location.search);
    return params.get("mode") === "desktop";
  }

  const state = {
    duration: 0,
    transcriptEntries: [],
    autoMarkers: [],
    manualMemos: [],
    selectedMemoId: null,
    currentTime: 0,
    memoDraftText: "",
    videoFileName: "",
    videoObjectUrl: "",
    videoFile: null,
    jobId: null,
    jobState: "idle",
    serverAvailable: false,
    pollTimer: null,
    appMode: initialAppMode(),
    heartbeatTimer: null,
    currentProgress: 0,
    displayedProgress: 0,
    lastServerProgress: 0,
    lastServerUpdatedAt: "",
    lastProgressTickAt: 0,
  };

  function formatTime(totalSeconds) {
    const safe = Math.max(0, Math.floor(totalSeconds || 0));
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const seconds = safe % 60;
    if (hours > 0) {
      return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
    }
    return [minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
  }

  function escapeXml(value) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&apos;");
  }

  function csvEscape(value) {
    return `"${String(value).replace(/"/g, '""')}"`;
  }

  function downloadText(filename, content, type = "text/plain;charset=utf-8") {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  function parseTimecode(raw) {
    const input = raw.trim();
    if (!input) return null;
    const parts = input.split(":").map((part) => part.trim());
    if (!parts.every((part) => /^\d+$/.test(part))) return null;
    if (parts.length === 2) {
      return Number(parts[0]) * 60 + Number(parts[1]);
    }
    if (parts.length === 3) {
      return Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]);
    }
    return null;
  }

  function parseTranscript(text) {
    return text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => {
        const match = line.match(/^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$/);
        if (!match) return null;
        const time = parseTimecode(match[1]);
        if (time == null) return null;
        return {
          id: `tx-${time}-${match[2].slice(0, 12)}`,
          time,
          label: match[1],
          text: match[2].trim(),
        };
      })
      .filter(Boolean)
      .sort((left, right) => left.time - right.time);
  }

  function keywords() {
    return keywordsInput.value
      .split(",")
      .map((keyword) => keyword.trim())
      .filter(Boolean);
  }

  function detectAutoMarkers(entries) {
    const words = keywords();
    if (words.length === 0) return [];
    return entries
      .filter((entry) => words.some((word) => entry.text.toLowerCase().includes(word.toLowerCase())))
      .map((entry) => ({
        id: `auto-${entry.id}`,
        time: entry.time,
        text: entry.text,
        source: "auto",
      }));
  }

  function timelineDuration() {
    const transcriptLast = state.transcriptEntries.at(-1)?.time || 0;
    const manualLast = state.manualMemos.reduce((max, memo) => Math.max(max, memo.time), 0);
    const base = Math.max(state.duration || 0, transcriptLast, manualLast);
    return Math.max(base, 1);
  }

  function clearPollTimer() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function startHeartbeat() {
    if (state.heartbeatTimer) {
      return;
    }

    const ping = () =>
      fetch("/api/ping", {
        method: "POST",
        cache: "no-store",
        keepalive: true,
      }).catch(() => {});

    ping();
    state.heartbeatTimer = window.setInterval(ping, 5000);
  }

  function setJobProgress(percent, visible = true, stalled = false) {
    const safe = Math.max(0, Math.min(100, Math.round(percent || 0)));
    state.currentProgress = safe;
    jobProgress.hidden = !visible;
    jobProgress.classList.toggle("is-stalled", stalled);
    jobProgressBar.style.width = `${safe}%`;
    jobProgressValue.textContent = `${safe}%`;
  }

  function syncDisplayedProgress(job) {
    const now = Date.now();
    const serverPercent = Math.max(0, Math.round(job.progress_percent || 0));
    const serverUpdatedAt = job.updated_at || "";

    if (serverPercent !== state.lastServerProgress || serverUpdatedAt !== state.lastServerUpdatedAt) {
      state.lastServerProgress = serverPercent;
      state.lastServerUpdatedAt = serverUpdatedAt;
      state.lastProgressTickAt = now;
      state.displayedProgress = Math.max(state.displayedProgress, serverPercent);
      setJobProgress(state.displayedProgress, true, false);
      return { percent: state.displayedProgress, estimated: false };
    }

    const stalled = job.status === "processing" && now - state.lastProgressTickAt >= 3000;
    if (stalled) {
      state.lastProgressTickAt = now;
      state.displayedProgress = Math.min(95, Math.max(state.displayedProgress, serverPercent) + 1);
    } else {
      state.displayedProgress = Math.max(state.displayedProgress, serverPercent);
    }

    const estimated = state.displayedProgress > serverPercent;
    setJobProgress(state.displayedProgress, true, estimated);
    return { percent: state.displayedProgress, estimated };
  }

  function saveSession() {
    const payload = {
      transcriptText: transcriptInput.value,
      keywords: keywordsInput.value,
      memoDraftText: state.memoDraftText,
      manualMemos: state.manualMemos,
      selectedMemoId: state.selectedMemoId,
      modelPath: modelPathInput.value,
      language: languageSelect.value,
    };
    localStorage.setItem(storageKey, JSON.stringify(payload));
  }

  function loadSession() {
    const raw = localStorage.getItem(storageKey);
    if (!raw) {
      transcriptInput.value = demoTranscript.map((entry) => `${formatTime(entry.time)} ${entry.text}`).join("\n");
      return;
    }

    try {
      const parsed = JSON.parse(raw);
      transcriptInput.value = parsed.transcriptText || "";
      keywordsInput.value = parsed.keywords || keywordsInput.value;
      state.memoDraftText = parsed.memoDraftText || "";
      state.manualMemos = Array.isArray(parsed.manualMemos) ? parsed.manualMemos : [];
      state.selectedMemoId = parsed.selectedMemoId || null;
      modelPathInput.value = parsed.modelPath || "";
      languageSelect.value = parsed.language || "ja";
    } catch (_error) {
      transcriptInput.value = demoTranscript.map((entry) => `${formatTime(entry.time)} ${entry.text}`).join("\n");
    }
  }

  function setJobStatus(text, type = "") {
    jobStatus.textContent = text;
    jobStatus.classList.remove("is-ready", "is-processing", "is-error");
    if (type) {
      jobStatus.classList.add(type);
    }
  }

  function updateMeta() {
    videoName.textContent = state.videoFileName || "未選択";
    transcriptCount.textContent = `${state.transcriptEntries.length} 行`;
    autoMarkerCount.textContent = `${state.autoMarkers.length} 件`;
    memoCount.textContent = `${state.manualMemos.length} 件`;
    timelineEnd.textContent = formatTime(timelineDuration());
    currentTimeLabel.textContent = formatTime(state.currentTime);
  }

  function applyRuntimeMode() {
    if (pageLinks) {
      pageLinks.hidden = state.appMode;
    }
  }

  function selectedMemo() {
    return state.manualMemos.find((memo) => memo.id === state.selectedMemoId) || null;
  }

  function syncMemoForm() {
    const memo = selectedMemo();
    if (memo) {
      memoTimeInput.value = formatTime(memo.time);
      if (document.activeElement !== memoTextInput) {
        memoTextInput.value = memo.text;
      }
      deleteMemoBtn.disabled = false;
      return;
    }

    memoTimeInput.value = formatTime(state.currentTime);
    if (document.activeElement !== memoTextInput) {
      memoTextInput.value = state.memoDraftText;
    }
    deleteMemoBtn.disabled = true;
  }

  function seekTo(seconds) {
    const bounded = Math.max(0, Math.min(seconds, timelineDuration()));
    state.currentTime = bounded;
    if (Number.isFinite(videoPlayer.duration) && videoPlayer.duration > 0) {
      videoPlayer.currentTime = Math.min(bounded, videoPlayer.duration);
    }
    renderAll();
  }

  function progressPercent(seconds) {
    return `${(Math.max(0, Math.min(seconds, timelineDuration())) / timelineDuration()) * 100}%`;
  }

  function createActionButton(label, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ghost-button";
    button.textContent = label;
    button.addEventListener("click", handler);
    return button;
  }

  function renderTimeline() {
    timelineMarkers.innerHTML = "";
    timelineProgress.style.width = progressPercent(state.currentTime);

    const markerItems = [
      ...state.autoMarkers.map((marker) => ({ ...marker, kind: "auto" })),
      ...state.manualMemos.map((memo) => ({ ...memo, kind: "manual" })),
    ].sort((left, right) => left.time - right.time);

    for (const marker of markerItems) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `marker ${marker.kind === "auto" ? "marker-auto" : "marker-manual"}`;
      if (marker.kind === "manual" && marker.id === state.selectedMemoId) {
        button.classList.add("active");
      }
      button.style.left = progressPercent(marker.time);
      button.title = `${formatTime(marker.time)} ${marker.text || ""}`.trim();
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        if (marker.kind === "manual") {
          state.selectedMemoId = marker.id;
        }
        seekTo(marker.time);
      });
      timelineMarkers.appendChild(button);
    }
  }

  function renderTranscriptRibbon() {
    transcriptRibbon.innerHTML = "";
    if (state.transcriptEntries.length === 0) {
      transcriptRibbon.innerHTML = '<div class="empty-note">文字起こし結果が入ると、時間付きカードがここに並びます。</div>';
      return;
    }

    for (const entry of state.transcriptEntries) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "ribbon-chip";
      if (Math.abs(entry.time - state.currentTime) < 4) {
        chip.classList.add("active");
      }
      chip.innerHTML = `<span class="chip-time">${formatTime(entry.time)}</span><span class="chip-text">${entry.text}</span>`;
      chip.addEventListener("click", () => seekTo(entry.time));
      transcriptRibbon.appendChild(chip);
    }
  }

  function renderTranscriptList() {
    transcriptList.innerHTML = "";
    if (state.transcriptEntries.length === 0) {
      transcriptList.innerHTML = '<div class="empty-note">動画を文字起こしするか、文字起こし欄へ手入力してください。</div>';
      return;
    }

    const autoIndex = new Set(state.autoMarkers.map((marker) => marker.time));
    for (const entry of state.transcriptEntries) {
      const item = document.createElement("article");
      item.className = "list-item";
      const tag = autoIndex.has(entry.time) ? '<span class="list-item-tag tag-auto">Auto</span>' : "";
      item.innerHTML = `
        <div class="list-item-header">
          <p class="list-item-title">${formatTime(entry.time)}</p>
          ${tag}
        </div>
        <p>${entry.text}</p>
      `;
      const actions = document.createElement("div");
      actions.className = "list-item-actions";
      actions.appendChild(createActionButton("ここへ移動", () => seekTo(entry.time)));
      actions.appendChild(
        createActionButton("メモ化", () => {
          createMemo(entry.time, entry.text);
        })
      );
      item.appendChild(actions);
      transcriptList.appendChild(item);
    }
  }

  function renderMemoList() {
    memoList.innerHTML = "";
    if (state.manualMemos.length === 0) {
      memoList.innerHTML = '<div class="empty-note">手動メモはまだありません。現在位置にメモ追加から作成できます。</div>';
      return;
    }

    const sorted = [...state.manualMemos].sort((left, right) => left.time - right.time);
    for (const memo of sorted) {
      const item = document.createElement("article");
      item.className = "list-item";
      item.innerHTML = `
        <div class="list-item-header">
          <p class="list-item-title">${formatTime(memo.time)}</p>
          <span class="list-item-tag tag-manual">Memo</span>
        </div>
        <p>${memo.text || "メモ未入力"}</p>
      `;
      const actions = document.createElement("div");
      actions.className = "list-item-actions";
      actions.appendChild(
        createActionButton("編集", () => {
          state.selectedMemoId = memo.id;
          seekTo(memo.time);
        })
      );
      actions.appendChild(createActionButton("再生位置へ", () => seekTo(memo.time)));
      item.appendChild(actions);
      memoList.appendChild(item);
    }
  }

  function renderAll() {
    renderTimeline();
    renderTranscriptRibbon();
    renderTranscriptList();
    renderMemoList();
    syncMemoForm();
    updateMeta();
  }

  function refreshDerivedState() {
    state.transcriptEntries = parseTranscript(transcriptInput.value);
    state.autoMarkers = detectAutoMarkers(state.transcriptEntries);
    state.manualMemos = state.manualMemos
      .map((memo) => ({
        ...memo,
        time: Number(memo.time) || 0,
        text: memo.text || "",
      }))
      .sort((left, right) => left.time - right.time);

    if (!selectedMemo() && state.manualMemos.length > 0) {
      state.selectedMemoId = state.manualMemos[0].id;
    }

    if (state.manualMemos.length === 0) {
      state.selectedMemoId = null;
    }

    saveSession();
    renderAll();
  }

  function createMemo(time = state.currentTime, seedText = "") {
    const memo = {
      id: `memo-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
      time: Math.floor(time),
      text: seedText || state.memoDraftText,
    };
    state.manualMemos.push(memo);
    state.selectedMemoId = memo.id;
    state.memoDraftText = "";
    refreshDerivedState();
  }

  function saveMemo() {
    const existing = selectedMemo();
    if (!existing) {
      createMemo(state.currentTime, memoTextInput.value.trim());
      return;
    }
    existing.text = memoTextInput.value.trim();
    refreshDerivedState();
  }

  function deleteMemo() {
    if (!state.selectedMemoId) return;
    state.manualMemos = state.manualMemos.filter((memo) => memo.id !== state.selectedMemoId);
    state.selectedMemoId = state.manualMemos[0]?.id || null;
    if (!state.selectedMemoId) {
      state.memoDraftText = "";
    }
    refreshDerivedState();
  }

  function transcriptAsText() {
    return state.transcriptEntries.map((entry) => `${formatTime(entry.time)} ${entry.text}`).join("\n");
  }

  function memosAsText() {
    return [...state.manualMemos]
      .sort((left, right) => left.time - right.time)
      .map((memo) => `${formatTime(memo.time)} ${memo.text}`)
      .join("\n");
  }

  function memosAsCsv() {
    const header = ["time", "note", "source"].map(csvEscape).join(",");
    const rows = [...state.manualMemos]
      .sort((left, right) => left.time - right.time)
      .map((memo) => [formatTime(memo.time), memo.text, "manual"].map(csvEscape).join(","));
    return [header, ...rows].join("\n");
  }

  function memosAsXml() {
    const markers = [...state.manualMemos]
      .sort((left, right) => left.time - right.time)
      .map((memo, index) => {
        return [
          "    <marker>",
          `      <name>Memo ${index + 1}</name>`,
          `      <comment>${escapeXml(memo.text)}</comment>`,
          `      <in>${Math.floor(memo.time * 30)}</in>`,
          `      <out>${Math.floor(memo.time * 30)}</out>`,
          "    </marker>",
        ].join("\n");
      })
      .join("\n");

    return [
      '<?xml version="1.0" encoding="UTF-8"?>',
      "<xmeml version=\"5\">",
      "  <sequence>",
      "    <name>VideoMemo Markers</name>",
      "    <rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>",
      "    <markers>",
      markers,
      "    </markers>",
      "  </sequence>",
      "</xmeml>",
    ].join("\n");
  }

  async function copyFullTranscript() {
    const content = transcriptAsText();
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
      copyFullBtn.textContent = "コピー済み";
      window.setTimeout(() => {
        copyFullBtn.textContent = "全文コピー";
      }, 1400);
    } catch (_error) {
      downloadText("videomemo-transcript.txt", content);
    }
  }

  async function fetchConfig() {
    try {
      const response = await fetch("/api/config", { cache: "no-store" });
      if (!response.ok) {
        throw new Error("server unavailable");
      }
      const payload = await response.json();
      state.serverAvailable = Boolean(payload.ok);
      state.appMode = state.appMode || Boolean(payload.app_mode);
      applyRuntimeMode();
      if (state.appMode) {
        startHeartbeat();
      }
      if (payload.default_model_path && !modelPathInput.value) {
        modelPathInput.value = payload.default_model_path;
      }
      if (payload.default_language && !localStorage.getItem(storageKey)) {
        languageSelect.value = payload.default_language;
      }
      if (!payload.ffmpeg_available) {
        setJobStatus("ffmpeg が見つかりません。ローカル環境へインストールしてください。", "is-error");
        return;
      }
      setJobStatus("ローカル処理の準備完了。動画を選んで文字起こしできます。", "is-ready");
    } catch (_error) {
      state.serverAvailable = false;
      const launchedAsApp = state.appMode || window.location.protocol === "file:";
      const message = launchedAsApp
        ? "VideoMemo を起動できません。`start_app.bat` または `python desktop_app.py` で開いてください。"
        : "ローカル処理に接続できません。`python server.py` を起動してから使ってください。";
      setJobStatus(message, "is-error");
    }
  }

  async function fetchTranscript(jobId) {
    const response = await fetch(`/api/transcript/${jobId}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "Failed to fetch transcript.");
    }
    transcriptInput.value = payload.transcript_text || "";
    refreshDerivedState();
  }

  async function pollJob(jobId) {
    try {
      const response = await fetch(`/api/status/${jobId}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "Failed to get job status.");
      }

      const job = payload.job;
      state.jobState = job.status;
      const progressState = syncDisplayedProgress(job);

      if (job.status === "completed") {
        clearPollTimer();
        await fetchTranscript(jobId);
        state.displayedProgress = 100;
        state.lastServerProgress = 100;
        setJobProgress(100, true);
        setJobStatus(`文字起こし完了。${job.transcript_count} 行を読み込みました。`, "is-ready");
        transcribeBtn.disabled = false;
        return;
      }

      if (job.status === "error") {
        clearPollTimer();
        setJobStatus(`文字起こし失敗: ${job.error}`, "is-error");
        transcribeBtn.disabled = false;
        return;
      }

      const stage = job.progress_stage || "文字起こし中";
      const suffix = progressState.estimated ? " 目安" : "";
      setJobStatus(`${stage}... ${progressState.percent}%${suffix}`, "is-processing");
      state.pollTimer = window.setTimeout(() => {
        pollJob(jobId);
      }, 1000);
    } catch (error) {
      clearPollTimer();
      setJobStatus(`状態取得に失敗: ${error.message}`, "is-error");
      transcribeBtn.disabled = false;
    }
  }

  async function startTranscription() {
    if (!state.serverAvailable) {
      await fetchConfig();
      if (!state.serverAvailable) {
        return;
      }
    }

    if (!state.videoFile) {
      setJobStatus("先に動画ファイルを選択してください。", "is-error");
      return;
    }

    if (!modelPathInput.value.trim()) {
      setJobStatus("Whisper モデルパスが必要です。既定モデルを使うか入力してください。", "is-error");
      return;
    }

    clearPollTimer();
    transcribeBtn.disabled = true;
    state.displayedProgress = 1;
    state.lastServerProgress = 0;
    state.lastServerUpdatedAt = "";
    state.lastProgressTickAt = Date.now();
    setJobProgress(1, true);
    setJobStatus("アップロードしています...", "is-processing");

    try {
      const response = await fetch("/api/upload", {
        method: "POST",
        headers: {
          "Content-Type": "application/octet-stream",
          "X-Filename": state.videoFile.name,
          "X-Model-Path": modelPathInput.value.trim(),
          "X-Language": languageSelect.value,
        },
        body: state.videoFile,
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || "Failed to start transcription.");
      }

      state.jobId = payload.job.id;
      state.jobState = payload.job.status;
      state.displayedProgress = Math.max(2, Math.round(payload.job.progress_percent || 2));
      state.lastServerProgress = Math.round(payload.job.progress_percent || 0);
      state.lastServerUpdatedAt = payload.job.updated_at || "";
      state.lastProgressTickAt = Date.now();
      setJobProgress(state.displayedProgress, true);
      setJobStatus("文字起こしジョブを開始しました。進捗を確認しています...", "is-processing");
      saveSession();
      pollJob(state.jobId);
    } catch (error) {
      transcribeBtn.disabled = false;
      setJobStatus(`開始に失敗: ${error.message}`, "is-error");
    }
  }

  function resetSession() {
    clearPollTimer();
    if (state.videoObjectUrl) {
      URL.revokeObjectURL(state.videoObjectUrl);
    }
    state.manualMemos = [];
    state.selectedMemoId = null;
    state.memoDraftText = "";
    state.duration = 0;
    state.currentTime = 0;
    state.videoFileName = "";
    state.videoObjectUrl = "";
    state.videoFile = null;
    state.jobId = null;
    state.jobState = "idle";
    state.displayedProgress = 0;
    state.lastServerProgress = 0;
    state.lastServerUpdatedAt = "";
    state.lastProgressTickAt = 0;
    transcriptInput.value = "";
    keywordsInput.value = "メモ,memo,mark,チェック";
    modelPathInput.value = "";
    languageSelect.value = "ja";
    videoInput.value = "";
    videoPlayer.removeAttribute("src");
    videoPlayer.load();
    videoEmpty.hidden = false;
    localStorage.removeItem(storageKey);
    transcribeBtn.disabled = false;
    setJobProgress(0, false);
    setJobStatus("ローカル処理を確認中...", "");
    fetchConfig();
    refreshDerivedState();
  }

  function loadDemo() {
    transcriptInput.value = demoTranscript.map((entry) => `${formatTime(entry.time)} ${entry.text}`).join("\n");
    if (!state.videoFileName) {
      state.videoFileName = "demo-timeline";
    }
    refreshDerivedState();
  }

  function handleTimelineJump(clientX) {
    const rect = timelineTrack.getBoundingClientRect();
    const ratio = (clientX - rect.left) / rect.width;
    seekTo(timelineDuration() * Math.max(0, Math.min(ratio, 1)));
  }

  function bindEvents() {
    videoInput.addEventListener("change", () => {
      const file = videoInput.files?.[0];
      if (!file) return;

      if (state.videoObjectUrl) {
        URL.revokeObjectURL(state.videoObjectUrl);
      }

      state.videoFile = file;
      state.videoFileName = file.name;
      state.videoObjectUrl = URL.createObjectURL(file);
      videoPlayer.src = state.videoObjectUrl;
      videoEmpty.hidden = true;
      updateMeta();
    });

    videoPlayer.addEventListener("loadedmetadata", () => {
      state.duration = Number.isFinite(videoPlayer.duration) ? Math.floor(videoPlayer.duration) : 0;
      updateMeta();
      renderTimeline();
    });

    videoPlayer.addEventListener("timeupdate", () => {
      state.currentTime = Math.floor(videoPlayer.currentTime || 0);
      renderTimeline();
      renderTranscriptRibbon();
      syncMemoForm();
      updateMeta();
    });

    transcriptInput.addEventListener("input", refreshDerivedState);
    keywordsInput.addEventListener("input", refreshDerivedState);
    modelPathInput.addEventListener("input", saveSession);
    languageSelect.addEventListener("change", saveSession);
    memoTextInput.addEventListener("input", () => {
      const memo = selectedMemo();
      if (memo) {
        memo.text = memoTextInput.value;
      } else {
        state.memoDraftText = memoTextInput.value;
      }
      saveSession();
    });
    loadDemoBtn.addEventListener("click", loadDemo);
    resetSessionBtn.addEventListener("click", resetSession);
    transcribeBtn.addEventListener("click", startTranscription);
    addMarkerBtn.addEventListener("click", () => createMemo());
    saveMemoBtn.addEventListener("click", saveMemo);
    deleteMemoBtn.addEventListener("click", deleteMemo);
    copyFullBtn.addEventListener("click", copyFullTranscript);
    downloadFullBtn.addEventListener("click", () => downloadText("videomemo-transcript.txt", transcriptAsText()));
    downloadMemoBtn.addEventListener("click", () => downloadText("videomemo-memos.txt", memosAsText()));
    downloadCsvBtn.addEventListener("click", () => downloadText("videomemo-memos.csv", memosAsCsv(), "text/csv;charset=utf-8"));
    downloadXmlBtn.addEventListener("click", () => downloadText("videomemo-markers.xml", memosAsXml(), "application/xml;charset=utf-8"));

    timelineTrack.addEventListener("click", (event) => handleTimelineJump(event.clientX));
    timelineTrack.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handleTimelineJump(timelineTrack.getBoundingClientRect().left + timelineTrack.clientWidth / 2);
      }
    });
  }

    loadSession();
  applyRuntimeMode();
  bindEvents();
  refreshDerivedState();
  syncMemoForm();
  window.addEventListener("beforeunload", () => {
    if (!state.appMode) {
      return;
    }
    navigator.sendBeacon("/api/ping", new Blob([]));
  });
  fetchConfig();
  setJobProgress(0, false);
})();
