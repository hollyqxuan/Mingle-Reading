const CHARS_PER_PAGE = 1150;
const REQUEST_TIMEOUT_MS = 300000;

const UPLOAD_STAGE_META = {
  queued: {
    title: "Upload queued",
    description: "The file has been accepted and is waiting for the background pipeline to start.",
  },
  "extract-source-text": {
    title: "Extracting source text",
    description: "Reading the uploaded file and extracting plain text from the source document.",
  },
  "segment-chapters": {
    title: "Segmenting chapters",
    description: "Detecting chapter boundaries and preparing paragraph-level source units.",
  },
  "construct-episodes": {
    title: "Constructing episodes",
    description: "Building constrained extraction packets from adjacent source paragraphs.",
  },
  "graph-episode-start": {
    title: "Processing episode",
    description: "Preparing the current packet for entity and fact extraction.",
  },
  "llm-skipped": {
    title: "LLM gate skipped this packet",
    description: "The gate judged this packet low-value, so the graph used the non-LLM path here.",
  },
  "llm-request-dispatched": {
    title: "Waiting for LLM extraction",
    description: "The packet prompt has been sent to the configured graph extraction model.",
  },
  "llm-response-received": {
    title: "LLM extraction received",
    description: "The extraction model has returned structured entity and fact candidates.",
  },
  "llm-request-failed": {
    title: "LLM extraction failed",
    description: "The graph extraction step failed for the current packet.",
  },
  "graph-episode-complete": {
    title: "Episode graph updated",
    description: "The current packet has been written back into the temporal graph state.",
  },
  "chapter-consolidation": {
    title: "Chapter consolidation",
    description: "Merging aliases, deduplicating relations, and reconciling chapter-level graph state.",
  },
  "graph-community-build": {
    title: "Building communities",
    description: "Clustering entities and relations into graph communities.",
  },
  "graph-saga-build": {
    title: "Building sagas",
    description: "Aggregating chapter-level developments into larger narrative sagas.",
  },
  "graph-timeline-build": {
    title: "Building chapter timeline",
    description: "Assembling the chapter timeline from visible episodes, entities, and facts.",
  },
  "graph-build-finished": {
    title: "Graph build finished",
    description: "The graph build is complete and is moving into persistence steps.",
  },
  "persist-book-record": {
    title: "Persisting book record",
    description: "Saving the parsed book record to local storage.",
  },
  "persist-graph-snapshot": {
    title: "Persisting graph snapshot",
    description: "Saving the full graph snapshot, including relations, communities, and sagas.",
  },
  "finalize-upload": {
    title: "Finalizing upload",
    description: "Wrapping up upload metadata and preparing the book for reading.",
  },
  completed: {
    title: "Temporal graph ready",
    description: "The upload, extraction, and temporal graph construction pipeline has finished.",
  },
  failed: {
    title: "Upload failed",
    description: "The upload pipeline failed before finishing the document and graph build.",
  },
};

const WORKFLOW_META = {
  personaQa: {
    title: "Literary agent answering",
    description: "Reading the visible book scope, retrieving graph context, and generating an answer through the literary agent.",
  },
  characterQa: {
    title: "Character agent answering",
    description: "Reading the visible character scope and generating a role-grounded answer.",
  },
  chapterSummary: {
    title: "Chapter summarization",
    description: "Collecting visible chapter context and generating a grounded chapter summary.",
  },
  characterProfile: {
    title: "Building character profile",
    description: "Gathering evidence, relationships, and current scope notes for the selected character.",
  },
};

const state = {
  books: [],
  personas: [],
  characterCandidates: [],
  activeBook: null,
  activeBookDetail: null,
  activeChapter: 1,
  activeParagraphIndex: null,
  activeChunkId: null,
  activePageIndex: 0,
  assistantMode: "persona",
  personaId: "persona_lu_xun",
  activeCharacterName: "",
  activeCharacterProfile: null,
  personaConversation: [],
  characterConversation: [],
  inlineBubblesByChunk: {},
  pendingWorkflow: null,
  graphViewVisible: false,
  graphViewScope: "chapter",
  graphRelationMode: "people",
  graphExpanded: false,
  graphViewData: null,
  graphViewLoading: false,
  graphViewError: "",
  memoryStatus: null,
  bubbleTimer: null,
  chapterEnteredAt: Date.now(),
  readingProgress: {
    book_id: "",
    chapter_id: 1,
    section_id: "sec-1",
    paragraph_id: "",
    token_offset: 0,
    scroll_offset: 0,
    dwell_seconds: 0,
    updated_at: "",
  },
  selectionContext: {
    book_id: "",
    selection_id: "",
    selected_text: "",
    context_text: "",
    selection_source: "passage",
    left_context: "",
    right_context: "",
    anchor: {
      chapter_id: 1,
      section_id: "sec-1",
      paragraph_id: "",
    },
  },
};

const LAST_OPENED_BOOK_KEY = "mingle-reading:last-opened-book";

let threeRuntimePromise = null;
let graph3DInstance = null;
let graphRenderSerial = 0;
let bubbleHoverSticky = false;

async function fetchJSON(url, options = {}) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(url, { ...options, signal: options.signal || controller.signal });
    if (!response.ok) {
      let detail = `Request failed: ${response.status}`;
      try {
        const payload = await response.json();
        detail = payload.detail || detail;
      } catch (_error) {
        // ignore
      }
      throw new Error(detail);
    }
    return response.json();
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(`Request exceeded ${REQUEST_TIMEOUT_MS / 1000}s.`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

function escapeHtml(text = "") {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function compactInlineText(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim();
}

function previewText(text, fallback = "Nothing is selected yet.", maxLength = 150) {
  const compact = compactInlineText(text);
  if (!compact) {
    return fallback;
  }
  return compact.length > maxLength ? `${compact.slice(0, maxLength).trim()}...` : compact;
}

function getQuestionContextText() {
  const manualSelection = compactInlineText(state.selectionContext.selected_text);
  if (manualSelection) {
    return manualSelection;
  }
  return compactInlineText(state.selectionContext.context_text);
}

function formatPersonaType(sourceType = "") {
  const labels = {
    literary_master: "文学导师",
    book_character: "书中角色",
    neutral: "中性导读",
  };
  return labels[sourceType] || sourceType || "导读";
}

function formatPersonaCitation(persona) {
  const citation = compactInlineText(persona?.citation || "");
  const lowerCitation = citation.toLowerCase();
  const hasInternalPath =
    lowerCitation.includes("backend/") ||
    lowerCitation.includes("frontend/") ||
    lowerCitation.includes("assets/") ||
    lowerCitation.includes(".md") ||
    lowerCitation.includes(".json");
  if (!citation || hasInternalPath) {
    if (persona?.source_type === "neutral") {
      return "中性导读模式：不套用作家声腔，优先依据当前已读正文回答。";
    }
    return `基于本地${persona?.name || "文学导师"}风格资料包；回答仍以当前已读正文为准。`;
  }
  return citation;
}

function setButtonLoading(buttonId, isLoading, loadingText = "") {
  const button = document.getElementById(buttonId);
  if (!button) {
    return;
  }
  if (!button.dataset.defaultText) {
    button.dataset.defaultText = button.textContent;
  }
  button.disabled = isLoading;
  button.textContent = isLoading ? loadingText : button.dataset.defaultText;
}

function getPersonaById(personaId) {
  return state.personas.find((persona) => persona.persona_id === personaId) || state.personas[0] || null;
}

function getCurrentPassages() {
  if (!state.activeBookDetail) {
    return [];
  }
  return (
    state.activeBookDetail.chapters[String(state.activeChapter)] ||
    state.activeBookDetail.chapters[state.activeChapter] ||
    []
  );
}

function getFirstReadableChapter() {
  if (!state.activeBookDetail) {
    return 1;
  }
  for (let chapter = 1; chapter <= state.activeBookDetail.chapter_count; chapter += 1) {
    const passages = state.activeBookDetail.chapters[String(chapter)] || state.activeBookDetail.chapters[chapter] || [];
    if (passages.length) {
      return chapter;
    }
  }
  return 1;
}

function getChapterTitle(chapter) {
  if (!state.activeBookDetail) {
    return `Chapter ${chapter}`;
  }

  const chapterTitles = state.activeBookDetail.chapter_titles || {};
  const rawTitle = chapterTitles[String(chapter)] || chapterTitles[chapter];
  if (typeof rawTitle === "string" && rawTitle.trim()) {
    return rawTitle.trim();
  }

  return `Chapter ${chapter}`;
}

function getChapterDisplayLabel(chapter) {
  const title = getChapterTitle(chapter);
  return title === `Chapter ${chapter}` ? title : `Chapter ${chapter}: ${title}`;
}

function getCurrentPages() {
  const passages = getCurrentPassages();
  if (!passages.length) {
    return [];
  }
  const pages = [];
  let currentPage = [];
  let currentSize = 0;

  passages.forEach((passage, index) => {
    const estimatedSize = (passage.text || "").length + 80;
    if (currentPage.length && currentSize + estimatedSize > CHARS_PER_PAGE) {
      pages.push(currentPage);
      currentPage = [];
      currentSize = 0;
    }
    currentPage.push({ ...passage, _index: index });
    currentSize += estimatedSize;
  });

  if (currentPage.length) {
    pages.push(currentPage);
  }

  return pages;
}

function getCurrentPageItems() {
  const pages = getCurrentPages();
  if (!pages.length) {
    return [];
  }
  state.activePageIndex = Math.max(0, Math.min(state.activePageIndex, pages.length - 1));
  return pages[state.activePageIndex];
}

function currentConversation() {
  return state.assistantMode === "persona" ? state.personaConversation : state.characterConversation;
}

function pushConversation(role, content, meta = {}) {
  const target = state.assistantMode === "persona" ? state.personaConversation : state.characterConversation;
  target.push({ role, content, ...meta });
}

function renderPendingWorkflow() {
  const indicator = document.getElementById("pending-indicator");
  const title = document.getElementById("pending-title");
  const label = document.getElementById("pending-label");
  const description = document.getElementById("pending-description");
  const bar = document.getElementById("pending-bar");
  const percent = document.getElementById("pending-percent");
  const caption = document.getElementById("pending-step-caption");

  if (!indicator || !title || !label || !description || !bar || !percent || !caption) {
    return;
  }

  if (!state.pendingWorkflow) {
    indicator.classList.remove("is-active", "is-indeterminate");
    title.textContent = "Idle";
    label.textContent = "idle";
    description.textContent =
      "After a document is uploaded, this panel will display real-time text extraction, packet construction, LLM extraction, and graph persistence progress.";
    bar.style.width = "0%";
    percent.textContent = "0%";
    caption.textContent = "Waiting for the next job to start.";
    return;
  }

  indicator.classList.add("is-active");
  indicator.classList.toggle("is-indeterminate", state.pendingWorkflow.indeterminate === true);
  title.textContent = state.pendingWorkflow.title;
  label.textContent = state.pendingWorkflow.label;
  description.textContent = state.pendingWorkflow.description;
  bar.style.width = state.pendingWorkflow.indeterminate ? "32%" : `${state.pendingWorkflow.percent}%`;
  percent.textContent = `${state.pendingWorkflow.percent}%`;
  caption.textContent = state.pendingWorkflow.caption || "Waiting for the next update.";
}

function setPendingState(active, label = "running", title = "Processing", description = "The task is starting.", percent = 12) {
  if (!active) {
    state.pendingWorkflow = null;
    renderPendingWorkflow();
    return;
  }
  state.pendingWorkflow = {
    label,
    title,
    description,
    percent,
    caption: "Preparing the task pipeline.",
    indeterminate: percent <= 12,
  };
  renderPendingWorkflow();
}

function startPendingWorkflow(key, label = "running") {
  const meta = WORKFLOW_META[key] || {
    title: "Processing",
    description: "The task is currently running.",
  };
  state.pendingWorkflow = {
    key,
    label,
    title: meta.title,
    description: meta.description,
    percent: 18,
    caption: "Task started.",
    indeterminate: true,
  };
  renderPendingWorkflow();
}

function finishPendingWorkflow(label = "done", title = "Completed", description = "The task finished successfully.") {
  if (!state.pendingWorkflow) {
    return;
  }
  state.pendingWorkflow = {
    ...state.pendingWorkflow,
    label,
    title,
    description,
    percent: 100,
    caption: description,
    indeterminate: false,
  };
  renderPendingWorkflow();
}

function releasePendingState(delayMs = 900) {
  window.setTimeout(() => {
    state.pendingWorkflow = null;
    renderPendingWorkflow();
  }, delayMs);
}

function formatUploadStageCopy(job) {
  const lines = [];
  const processed = Number(job.processed_snippets || 0);
  const total = Number(job.total_snippets || 0);
  const details = job.details || {};

  if (total) {
    lines.push(`Processed snippets: ${processed}/${total}`);
  }
  if (job.current_snippet_id) {
    lines.push(
      `Current snippet: ${job.current_snippet_id} (chapter ${job.current_chapter_index || "-"}, paragraph ${
        job.current_paragraph_index || "-"
      })`
    );
  }
  if (details.source_paragraph_count) {
    const indices = Array.isArray(details.source_paragraph_indices) ? details.source_paragraph_indices : [];
    const span =
      indices.length > 1
        ? `${indices[0]}-${indices[indices.length - 1]}`
        : indices.length === 1
          ? `${indices[0]}`
          : "-";
    lines.push(
      `Packet: paragraphs ${span}, count ${details.source_paragraph_count}, ${
        details.is_merged_packet ? "merged" : "single"
      }, ${details.packet_token_count || 0} chars`
    );
  }
  if (typeof details.score === "number") {
    const reasons = Array.isArray(details.reasons) ? details.reasons.join(", ") : "";
    lines.push(`Gate score: ${details.score}/${details.threshold ?? "-"}${reasons ? ` (${reasons})` : ""}`);
  }
  if (job.stage === "llm-request-dispatched") {
    lines.push(`LLM call dispatched. Provider: ${details.provider || "configured runtime"}`);
  }
  if (job.stage === "llm-response-received") {
    lines.push(`LLM returned ${details.entity_candidates || 0} entity candidates and ${details.fact_candidates || 0} fact candidates.`);
  }
  if (job.stage === "llm-request-failed" && details.error) {
    lines.push(`LLM error: ${details.error}`);
  }
  if (job.stage === "chapter-consolidation") {
    lines.push(
      `Chapter consolidation: entities ${details.active_entity_count || 0}, relations ${details.active_relation_count || 0}`
    );
  }
  if (job.stage === "persist-graph-snapshot") {
    lines.push(
      `Graph snapshot: entities ${details.entity_count || 0}, relations ${details.relation_count || 0}, communities ${
        details.community_count || 0
      }, sagas ${details.saga_count || 0}`
    );
  }
  return lines.join(" | ");
}

function applyUploadJobState(job) {
  const stage = job.stage || job.status || "queued";
  const meta = UPLOAD_STAGE_META[stage] || {
    title: job.title || "Temporal graph build",
    description: job.message || "The upload pipeline is running.",
  };
  state.pendingWorkflow = {
    key: "upload",
    label: stage,
    title: job.title || meta.title,
    description: job.message || meta.description,
    percent: Number(job.percent || 0),
    caption: formatUploadStageCopy(job) || meta.description,
    indeterminate: false,
  };
  renderPendingWorkflow();
}

async function waitForUploadJob(jobId) {
  while (true) {
    const job = await fetchJSON(`/api/upload-jobs/${jobId}`);
    applyUploadJobState(job);
    if (job.status === "completed") {
      return job;
    }
    if (job.status === "failed") {
      throw new Error(job.error || job.message || "Upload job failed.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 700));
  }
}

function resetSelection() {
  state.selectionContext = {
    book_id: state.activeBook || "",
    selection_id: "",
    selected_text: "",
    context_text: "",
    selection_source: "passage",
    left_context: "",
    right_context: "",
    anchor: {
      chapter_id: state.activeChapter,
      section_id: `sec-${state.activeChapter}`,
      paragraph_id: "",
    },
  };
}

function updateProgressFromPassage(passage) {
  const pages = getCurrentPages();
  const scrollOffset = pages.length ? Number(((state.activePageIndex + 1) / pages.length).toFixed(2)) : 0;
  state.readingProgress = {
    book_id: state.activeBook || "",
    chapter_id: state.activeChapter,
    section_id: `sec-${state.activeChapter}`,
    paragraph_id: String(passage.paragraph_index ?? ""),
    token_offset: (passage.text || "").length,
    scroll_offset: scrollOffset,
    dwell_seconds: Math.max(1, Math.floor((Date.now() - state.chapterEnteredAt) / 1000)),
    updated_at: new Date().toISOString(),
  };
}

function buildSelectionFromPassage(passage, index, passages, selectedText = "") {
  const previous = passages[index - 1];
  const next = passages[index + 1];
  const manualSelection = compactInlineText(selectedText);
  state.selectionContext = {
    book_id: state.activeBook || "",
    selection_id: `${manualSelection ? "text" : "passage"}_${passage.chunk_id || index + 1}`,
    selected_text: manualSelection,
    context_text: passage.text || "",
    selection_source: manualSelection ? "text" : "passage",
    left_context: previous ? previous.text || "" : "",
    right_context: next ? next.text || "" : "",
    anchor: {
      chapter_id: state.activeChapter,
      section_id: `sec-${state.activeChapter}`,
      paragraph_id: String(passage.paragraph_index ?? index + 1),
    },
  };
}

function renderPersonaDetails() {
  const persona = getPersonaById(state.personaId);
  if (!persona) {
    return;
  }
  document.getElementById("persona-type-badge").textContent = formatPersonaType(persona.source_type);
  document.getElementById("persona-name").textContent = persona.name;
  document.getElementById("persona-citation").textContent = formatPersonaCitation(persona);
  const traits = document.getElementById("persona-traits");
  traits.innerHTML = "";
  [...(persona.style_traits || []), ...(persona.reasoning_style || [])].slice(0, 6).forEach((item) => {
    const pill = document.createElement("span");
    pill.className = "pill";
    pill.textContent = item;
    traits.appendChild(pill);
  });
}

function renderCharacterCandidates() {
  const selects = [
    document.getElementById("character-select"),
    document.getElementById("assistant-character-select"),
  ].filter(Boolean);

  selects.forEach((select) => {
    const isAssistantSelect = select.id === "assistant-character-select";
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = state.characterCandidates.length ? "选择当前书中的人物" : "当前章节暂无人物候选";
    select.appendChild(empty);

    state.characterCandidates.forEach((candidate) => {
      const option = document.createElement("option");
      option.value = candidate.character_name;
      option.textContent = isAssistantSelect
        ? candidate.character_name
        : `${candidate.character_name} · ${candidate.mention_count || 0} 次`;
      option.title = candidate.preview || "";
      select.appendChild(option);
    });

    if (state.activeCharacterName) {
      const hasActiveOption = Array.from(select.options).some((option) => option.value === state.activeCharacterName);
      if (!hasActiveOption) {
        const custom = document.createElement("option");
        custom.value = state.activeCharacterName;
        custom.textContent = isAssistantSelect ? state.activeCharacterName : `${state.activeCharacterName} · 手动`;
        select.appendChild(custom);
      }
      select.value = state.activeCharacterName;
    }
  });
}

function syncCharacterSelectElement(select, characterName) {
  if (!select) {
    return;
  }
  const name = compactInlineText(characterName);
  const hasOption = Array.from(select.options).some((option) => option.value === name);
  if (name && !hasOption) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = select.id === "assistant-character-select" ? name : `${name} · 手动`;
    select.appendChild(option);
  }
  select.value = name;
}

function setSelectedCharacter(characterName, options = {}) {
  const name = compactInlineText(characterName);
  const changed = name !== state.activeCharacterName;
  state.activeCharacterName = name;
  if (changed && !options.keepProfile) {
    state.activeCharacterProfile = null;
  }

  const input = document.getElementById("character-input");
  if (input) {
    input.value = name;
  }
  syncCharacterSelectElement(document.getElementById("character-select"), name);
  syncCharacterSelectElement(document.getElementById("assistant-character-select"), name);

  if (!options.skipRender) {
    renderCharacterProfile();
    renderAssistantStatus();
    renderChatHistory();
  }
}

function renderCharacterProfile() {
  const container = document.getElementById("character-profile-card");
  if (!container) {
    return;
  }
  if (!state.activeCharacterProfile) {
    container.innerHTML = `<p class="muted">选择候选人物，或手动输入人物名后生成角色画像。</p>`;
    return;
  }

  const profile = state.activeCharacterProfile;
  const traits = (profile.core_traits || [])
    .map((trait) => `<span class="pill">${escapeHtml(trait)}</span>`)
    .join("");
  const relationships = (profile.relationships || [])
    .map((relation) => `<li>${escapeHtml(relation.target)} - ${escapeHtml(relation.description)}</li>`)
    .join("");

  container.innerHTML = `
    <h4 class="character-name">${escapeHtml(profile.character_name)}</h4>
    <p class="muted">${escapeHtml(profile.summary || "")}</p>
    ${
      profile.arc_summary
        ? `<p class="label">Memory arc</p><p class="muted">${escapeHtml(profile.arc_summary)}</p>`
        : ""
    }
    <div class="pill-row">${traits}</div>
    <p class="label">Current visible scope</p>
    <p class="muted">${escapeHtml(profile.current_scope || "No visible scope note available.")}</p>
    <p class="label">Signature tension</p>
    <p class="signature-tension">${escapeHtml(profile.signature_tension || "No signature tension note available.")}</p>
    <p class="label">Model</p>
    <p class="muted">${escapeHtml(profile.model_name || "")} · ${profile.evidence_count || 0} evidence · ${profile.visible_relationship_count || 0} relations</p>
    ${
      relationships
        ? `<p class="label">Relationships</p><ul class="plain-list relationship-list">${relationships}</ul>`
        : ""
    }
  `;
}

function renderBooks() {
  const list = document.getElementById("book-list");
  list.innerHTML = "";
  document.getElementById("book-count").textContent = `${state.books.length} books`;

  state.books.forEach((book) => {
    const item = document.createElement("li");
    item.className = "book-item";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `book-button ${state.activeBook === book.book_id ? "is-active" : ""}`;
    button.innerHTML = `
      <span class="book-title">${escapeHtml(book.title)}</span>
      <span class="book-meta">${escapeHtml(book.book_id)}</span>
    `;
    button.addEventListener("click", () => {
      closeDrawers();
      openBook(book.book_id);
    });
    item.appendChild(button);
    list.appendChild(item);
  });
}

function renderReaderHeader() {
  if (!state.activeBookDetail) {
    document.getElementById("book-title").textContent = "Choose a book to begin";
    document.getElementById("book-subtitle").textContent =
      "Upload a document to inspect chapters, reading progress, and the temporal knowledge graph.";
    document.getElementById("progress-text").textContent = "No reading progress is available yet.";
    document.getElementById("hero-chapter").textContent = "-";
    document.getElementById("hero-paragraph").textContent = "-";
    document.getElementById("hero-dwell").textContent = "0s";
    document.getElementById("reader-progress-fill").style.width = "0%";
    return;
  }

  const pages = getCurrentPages();
  const chapterCount = Math.max(1, state.activeBookDetail.chapter_count || 1);
  const pageRatio = pages.length ? (state.activePageIndex + 1) / pages.length : 0;
  const progressPercent = Math.min(100, Math.max(0, ((state.activeChapter - 1 + pageRatio) / chapterCount) * 100));
  document.getElementById("book-title").textContent = state.activeBookDetail.title;
  document.getElementById("book-subtitle").textContent = `${state.activeBookDetail.chapter_count} chapters · ${state.activeBookDetail.book_id}`;
  document.getElementById("progress-text").textContent = `Chapter ${state.readingProgress.chapter_id} · page ${state.activePageIndex + 1}/${pages.length || 0} · paragraph ${state.readingProgress.paragraph_id || "-"}`;
  document.getElementById("hero-chapter").textContent = getChapterDisplayLabel(state.activeChapter);
  document.getElementById("hero-paragraph").textContent =
    state.activeParagraphIndex === null ? "-" : `P${state.activeParagraphIndex}`;
  document.getElementById("hero-dwell").textContent = `${state.readingProgress.dwell_seconds || 0}s`;
  document.getElementById("reader-progress-fill").style.width = `${progressPercent.toFixed(1)}%`;
}

function renderChapterNav() {
  const container = document.getElementById("chapter-nav");
  container.innerHTML = "";

  if (!state.activeBookDetail) {
    container.innerHTML = `<p class="muted">No chapter outline is available until a book is opened.</p>`;
    return;
  }

  for (let chapter = 1; chapter <= state.activeBookDetail.chapter_count; chapter += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `chapter-button ${chapter === state.activeChapter ? "is-active" : ""}`;
    button.textContent = getChapterDisplayLabel(chapter);
    button.addEventListener("click", () => setActiveChapter(chapter));
    container.appendChild(button);
  }

  document.getElementById("toc-progress").textContent = `Reading ${getChapterDisplayLabel(state.activeChapter)}`;
}

function renderChapterSelects() {
  const chapterSelect = document.getElementById("chapter-select");
  const paragraphSelect = document.getElementById("paragraph-jump");
  chapterSelect.innerHTML = "";
  paragraphSelect.innerHTML = "";

  if (!state.activeBookDetail) {
    return;
  }

  for (let chapter = 1; chapter <= state.activeBookDetail.chapter_count; chapter += 1) {
    const option = document.createElement("option");
    option.value = String(chapter);
    option.textContent = getChapterDisplayLabel(chapter);
    chapterSelect.appendChild(option);
  }
  chapterSelect.value = String(state.activeChapter);

  getCurrentPassages().forEach((passage, index) => {
    const paragraphIndex = passage.paragraph_index ?? index + 1;
    const option = document.createElement("option");
    option.value = String(paragraphIndex);
    option.textContent = `Paragraph ${paragraphIndex}`;
    paragraphSelect.appendChild(option);
  });

  if (state.activeParagraphIndex !== null) {
    paragraphSelect.value = String(state.activeParagraphIndex);
  }
}

function renderSelectionPreview() {
  const label = document.getElementById("highlight-preview-label");
  const preview = document.getElementById("highlight-preview");
  const isManualSelection = Boolean(compactInlineText(state.selectionContext.selected_text));
  const text = isManualSelection ? state.selectionContext.selected_text : state.selectionContext.context_text;
  if (label) {
    label.textContent = isManualSelection ? "选中文字" : "当前段落";
  }
  if (preview) {
    preview.textContent = previewText(text, "尚未定位段落。");
  }
}

function renderAssistantStatus() {
  const node = document.getElementById("assistant-status");
  if (!node) {
    return;
  }
  if (state.assistantMode === "persona") {
    const persona = getPersonaById(state.personaId);
    node.textContent = persona
      ? `文学导师模式：${persona.name}`
      : "文学导师模式";
    return;
  }
  if (state.activeCharacterProfile) {
    node.textContent = `书中人物模式：${state.activeCharacterProfile.character_name}`;
    return;
  }
  if (state.activeCharacterName) {
    node.textContent = `书中人物模式：${state.activeCharacterName}`;
    return;
  }
  node.textContent = "书中人物模式：请先选择当前书中的人物。";
}

function renderAssistantMode() {
  document.getElementById("persona-mode-btn").classList.toggle("mode-chip-active", state.assistantMode === "persona");
  document.getElementById("character-mode-btn").classList.toggle("mode-chip-active", state.assistantMode === "character");
  document.getElementById("persona-select")?.classList.toggle("is-hidden", state.assistantMode !== "persona");
  document.getElementById("assistant-character-select")?.classList.toggle("is-hidden", state.assistantMode !== "character");
  renderAssistantStatus();
  renderChatHistory();
}

function renderChatHistory() {
  const historyNode = document.getElementById("chat-history");
  historyNode.innerHTML = "";
  const conversation = currentConversation();

  if (!conversation.length) {
    historyNode.innerHTML = `<p class="muted">No conversation yet. Ask a question to the current agent.</p>`;
    return;
  }

  conversation.forEach((turn) => {
    const article = document.createElement("article");
    article.className = `chat-message chat-message-${turn.role}`;
    const roleLabel =
      turn.role === "user"
        ? "User"
        : state.assistantMode === "persona"
          ? getPersonaById(state.personaId)?.name || "Literary Agent"
          : state.activeCharacterProfile?.character_name || state.activeCharacterName || "Character Agent";

    const citations = Array.isArray(turn.citations) ? turn.citations : [];
    const citationMarkup = citations.length
      ? `<details class="evidence-details"><summary>Evidence · ${citations.length}</summary>${citations
          .map(
            (citation) => `
              <blockquote>
                <strong>ch${escapeHtml(citation.chapter_index)} · p${escapeHtml(citation.paragraph_index || "-")}</strong>
                ${escapeHtml(citation.quote || citation.chunk_id || "").replace(/\n/g, "<br />")}
              </blockquote>
            `
          )
          .join("")}</details>`
      : "";
    const confidenceMarkup =
      typeof turn.confidence === "number"
        ? `<p class="answer-confidence">Confidence ${(turn.confidence * 100).toFixed(0)}% · unsupported ${turn.unsupported_claim_count || 0}</p>`
        : "";
    article.innerHTML = `
      <div class="chat-role">${escapeHtml(roleLabel)}</div>
      <div class="chat-content">${escapeHtml(turn.content || "").replace(/\n/g, "<br />")}</div>
      ${turn.role === "assistant" ? confidenceMarkup + citationMarkup : ""}
    `;
    historyNode.appendChild(article);
  });

  historyNode.scrollTop = historyNode.scrollHeight;
}

function updatePageIndicator() {
  const pages = getCurrentPages();
  const total = pages.length;
  const current = total ? state.activePageIndex + 1 : 0;
  document.getElementById("page-indicator").textContent = total ? `${current} / ${total}` : "- / -";
  document.getElementById("prev-page-btn").disabled = current <= 1;
  document.getElementById("next-page-btn").disabled = total === 0 || current >= total;
}

function graphNodeColor(type = "") {
  if (isCharacterNode({ type })) return "#1f6a73";
  if (type === "location") return "#5f7a48";
  if (type === "theme" || type === "concept") return "#a57b2d";
  if (type === "artifact" || type === "object") return "#8f5a3c";
  return "#7b6d59";
}

function isCharacterNode(node) {
  const type = String(node?.type || "").toLowerCase();
  return ["character", "person", "persona", "人物", "角色"].includes(type);
}

function relationCategory(edge) {
  const raw = `${edge?.relation_category || ""} ${edge?.state_family || ""} ${edge?.label || ""} ${edge?.fact || ""}`.toLowerCase();
  if (/family|parent|child|sibling|spouse|married|kin|mother|father|son|daughter|brother|sister|亲|父|母|子|女|兄|弟|姐|妹|夫|妻/.test(raw)) {
    return "family";
  }
  if (/conflict|enemy|oppose|fight|threat|kill|hate|rival|betray|冲突|敌|杀|恨|威胁|对抗|背叛/.test(raw)) {
    return "conflict";
  }
  if (/love|friend|ally|trust|help|protect|care|mentor|情|友|爱|信任|帮助|保护|师/.test(raw)) {
    return "affinity";
  }
  if (/speak|talk|meet|interact|see|ask|answer|spoke|交谈|相遇|看见|问|答/.test(raw)) {
    return "interaction";
  }
  if (/located|location|place|live|arrive|leave|位于|地点|居住|来到|离开/.test(raw)) {
    return "location";
  }
  if (/theme|symbol|concept|metaphor|主题|象征|隐喻/.test(raw)) {
    return "theme";
  }
  return "other";
}

function relationStyle(edge) {
  const styles = {
    family: { color: "#a85f38", label: "亲属/身份", dash: false },
    conflict: { color: "#b0413e", label: "冲突", dash: true },
    affinity: { color: "#1f6a73", label: "亲近/信任", dash: false },
    interaction: { color: "#7d6fb2", label: "互动", dash: false },
    location: { color: "#5f7a48", label: "地点", dash: true },
    theme: { color: "#a57b2d", label: "主题", dash: true },
    other: { color: "#7b6d59", label: "其他", dash: false },
  };
  const style = styles[relationCategory(edge)] || styles.other;
  return edge?.status && edge.status !== "active" ? { ...style, dash: true, color: "#9a8f86" } : style;
}

function formatRelationLabel(label = "") {
  const labels = {
    FAMILY_OF: "亲属",
    PARENT_OF: "父母",
    CHILD_OF: "子女",
    SPOUSE_OF: "伴侣",
    FRIEND_OF: "朋友",
    ALLY_OF: "同盟",
    CONFLICT_WITH: "冲突",
    LOVES: "情感",
    TRUSTS: "信任",
    HELPS: "帮助",
    PROTECTS: "保护",
    SPOKE_WITH: "交谈",
    MET_WITH: "相遇",
    LOCATED_IN: "位于",
    SYMBOLIZES: "象征",
  };
  return labels[label] || String(label || "关系").replaceAll("_", " ").toLowerCase();
}

function formatEntityType(type = "") {
  const labels = {
    character: "人物",
    person: "人物",
    location: "地点",
    theme: "主题",
    concept: "概念",
    artifact: "物件",
    object: "物件",
  };
  return labels[type] || type || "实体";
}

function graphNodeById(data) {
  return Object.fromEntries((data?.nodes || []).map((node) => [node.id, node]));
}

function getRenderableGraphData(data) {
  const nodeById = graphNodeById(data);
  const rawEdges = (data?.edges || []).filter((edge) => nodeById[edge.source] && nodeById[edge.target]);
  let edges = rawEdges;

  if (state.graphRelationMode === "people") {
    edges = rawEdges.filter((edge) => isCharacterNode(nodeById[edge.source]) && isCharacterNode(nodeById[edge.target]));
  }

  const visibleIds = new Set();
  edges.forEach((edge) => {
    visibleIds.add(edge.source);
    visibleIds.add(edge.target);
  });

  let nodes = (data?.nodes || []).filter((node) => visibleIds.has(node.id));
  if (!nodes.length && state.graphRelationMode === "people") {
    nodes = (data?.nodes || []).filter(isCharacterNode);
  }
  if (state.graphRelationMode === "all" && !nodes.length) {
    nodes = data?.nodes || [];
  }

  return { nodes, edges, nodeById };
}

function graphCaptionText(data, renderable) {
  const scopeText = state.graphViewScope === "chapter" ? "当前章节" : "当前段落";
  const modeText = state.graphRelationMode === "people" ? "人物关系" : "全部实体";
  return `${scopeText} · ${modeText} · ${renderable.nodes.length} 个节点 / ${renderable.edges.length} 条关系`;
}

function renderGraphPanel() {
  const panel = document.getElementById("graph-panel");
  const canvas = document.getElementById("graph-canvas");
  const detail = document.getElementById("graph-detail");
  const badge = document.getElementById("graph-stats-badge");
  const caption = document.getElementById("graph-caption");
  const toggleButton = document.getElementById("graph-toggle-btn");
  const passageScopeButton = document.getElementById("graph-scope-passage-btn");
  const chapterScopeButton = document.getElementById("graph-scope-chapter-btn");
  const peopleModeButton = document.getElementById("graph-mode-people-btn");
  const allModeButton = document.getElementById("graph-mode-all-btn");
  const expandButton = document.getElementById("graph-expand-btn");
  const insightDrawer = document.getElementById("insight-drawer");
  const isFullscreen = state.graphExpanded && state.graphViewVisible;

  passageScopeButton.classList.toggle("is-active", state.graphViewScope === "passage");
  chapterScopeButton.classList.toggle("is-active", state.graphViewScope === "chapter");
  peopleModeButton.classList.toggle("is-active", state.graphRelationMode === "people");
  allModeButton.classList.toggle("is-active", state.graphRelationMode === "all");
  panel.classList.toggle("is-hidden", !state.graphViewVisible);
  panel.classList.toggle("is-expanded", isFullscreen);
  insightDrawer?.classList.toggle("is-graph-fullscreen", isFullscreen);
  document.body.classList.toggle("graph-fullscreen-active", isFullscreen);
  toggleButton.textContent = state.graphViewVisible ? "Hide Map" : "Load Map";
  expandButton.textContent = isFullscreen ? "退出全屏" : "全屏";
  expandButton.setAttribute("aria-label", isFullscreen ? "退出图谱全屏" : "进入图谱全屏");

  if (!state.graphViewVisible) {
    disposeGraph3D();
    return;
  }

  if (state.graphViewLoading) {
    disposeGraph3D();
    badge.textContent = "loading";
    canvas.innerHTML = `<div class="graph-empty-state">正在加载关系图...</div>`;
    detail.textContent = "正在读取当前已读范围内的实体和关系。";
    return;
  }

  if (state.graphViewError) {
    disposeGraph3D();
    badge.textContent = "error";
    canvas.innerHTML = `<div class="graph-empty-state">${escapeHtml(state.graphViewError)}</div>`;
    detail.textContent = "图谱请求失败，请确认当前书籍已经完成 memory rebuild。";
    return;
  }

  const data = state.graphViewData;
  if (!data || !Array.isArray(data.nodes) || !data.nodes.length) {
    disposeGraph3D();
    badge.textContent = "0 nodes";
    canvas.innerHTML = `<div class="graph-empty-state">当前范围还没有可展示的关系节点。</div>`;
    detail.textContent = "可以切到当前章节，或先重建 memory 后再查看。";
    return;
  }

  const renderable = getRenderableGraphData(data);
  badge.textContent = `${renderable.nodes.length} nodes / ${renderable.edges.length} edges`;
  caption.textContent = graphCaptionText(data, renderable);

  if (!renderable.nodes.length) {
    disposeGraph3D();
    canvas.innerHTML = `<div class="graph-empty-state">当前范围还没有明确的人物关系。切到“全部实体”可以查看地点、主题和概念线索。</div>`;
    detail.textContent = "人物关系模式只显示人物和人物之间的关系，避免地点/主题把图谱搅乱。";
    return;
  }

  canvas.innerHTML = `
    <div class="graph-viewport-shell">
      <div id="graph-3d-view" class="graph-3d-view" role="img" aria-label="3D memory relationship map"></div>
      <div class="graph-legend">${renderGraphLegend(renderable.edges)}</div>
    </div>
  `;
  detail.textContent = "点击人物或关系查看出现位置、关系类型和证据线索。";
  renderGraph3D(renderable).catch((error) => {
    console.error(error);
    renderGraphFallback(renderable, String(error.message || error));
  });
}

function renderGraphLegend(edges) {
  const seen = new Set();
  const items = [];
  (edges || []).forEach((edge) => {
    const category = relationCategory(edge);
    if (seen.has(category)) {
      return;
    }
    seen.add(category);
    const style = relationStyle(edge);
    items.push(`<span><i style="--legend-color: ${style.color}"></i>${escapeHtml(style.label)}</span>`);
  });
  if (!items.length) {
    items.push(`<span><i style="--legend-color: #1f6a73"></i>人物</span>`);
  }
  return items.join("");
}

function disposeGraph3D() {
  if (!graph3DInstance) {
    return;
  }
  if (graph3DInstance.animationFrame) {
    cancelAnimationFrame(graph3DInstance.animationFrame);
  }
  graph3DInstance.resizeObserver?.disconnect();
  graph3DInstance.controls?.dispose?.();
  graph3DInstance.scene?.traverse?.((object) => {
    object.geometry?.dispose?.();
    if (Array.isArray(object.material)) {
      object.material.forEach((material) => material.dispose?.());
    } else {
      object.material?.dispose?.();
    }
    object.material?.map?.dispose?.();
  });
  graph3DInstance.renderer?.dispose?.();
  graph3DInstance = null;
}

async function loadThreeRuntime() {
  if (!threeRuntimePromise) {
    threeRuntimePromise = Promise.all([
      import("three"),
      import("three/addons/controls/OrbitControls.js"),
    ]).then(([THREE, controls]) => ({ THREE, OrbitControls: controls.OrbitControls }));
  }
  return threeRuntimePromise;
}

function graphLayout(nodes) {
  const radius = Math.max(72, Math.min(210, 54 + nodes.length * 16));
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const characterCount = nodes.filter(isCharacterNode).length;
  return Object.fromEntries(
    nodes.map((node, index) => {
      const total = Math.max(nodes.length, 2);
      const y = nodes.length === 1 ? 0 : (1 - (index / (total - 1)) * 2) * radius * 0.52;
      const ring = Math.sqrt(Math.max(0.12, 1 - (y / radius) ** 2)) * radius;
      const angle = index * goldenAngle;
      const characterPull = isCharacterNode(node) ? 0.72 : 1.08;
      const personOffset = isCharacterNode(node) && characterCount > 1 ? (index - characterCount / 2) * 6 : 0;
      return [
        node.id,
        {
          x: Math.cos(angle) * ring * characterPull,
          y: y + personOffset,
          z: Math.sin(angle) * ring * characterPull,
        },
      ];
    })
  );
}

function makeTextSprite(THREE, text, options = {}) {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  const fontSize = options.fontSize || 30;
  const paddingX = 20;
  const paddingY = 10;
  context.font = `700 ${fontSize}px "PingFang SC", "SF Pro Display", sans-serif`;
  const width = Math.min(640, Math.max(180, Math.ceil(context.measureText(text).width + paddingX * 2)));
  const height = fontSize + paddingY * 2;
  canvas.width = width * 2;
  canvas.height = height * 2;
  context.scale(2, 2);
  context.font = `700 ${fontSize}px "PingFang SC", "SF Pro Display", sans-serif`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillStyle = options.background || "rgba(255,255,255,0.78)";
  roundRect(context, 0, 0, width, height, 14);
  context.fill();
  context.fillStyle = options.color || "#2a261f";
  context.fillText(text, width / 2, height / 2 + 1);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(width * (options.scale || 0.34), height * (options.scale || 0.34), 1);
  return sprite;
}

function roundRect(context, x, y, width, height, radius) {
  context.beginPath();
  context.moveTo(x + radius, y);
  context.arcTo(x + width, y, x + width, y + height, radius);
  context.arcTo(x + width, y + height, x, y + height, radius);
  context.arcTo(x, y + height, x, y, radius);
  context.arcTo(x, y, x + width, y, radius);
  context.closePath();
}

function createCylinderBetween(THREE, start, end, radius, material, userData = {}) {
  const startVector = new THREE.Vector3(start.x, start.y, start.z);
  const endVector = new THREE.Vector3(end.x, end.y, end.z);
  const direction = new THREE.Vector3().subVectors(endVector, startVector);
  const length = direction.length();
  const geometry = new THREE.CylinderGeometry(radius, radius, length, 12, 1);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.copy(startVector).add(endVector).multiplyScalar(0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize());
  mesh.userData = userData;
  return mesh;
}

function addEdgeToScene(THREE, sceneGroup, pickables, edge, start, end) {
  const style = relationStyle(edge);
  const material = new THREE.MeshStandardMaterial({
    color: style.color,
    roughness: 0.62,
    metalness: 0.08,
    transparent: true,
    opacity: edge.status && edge.status !== "active" ? 0.48 : 0.78,
  });
  const weight = Math.max(1.3, Math.min(4.2, Number(edge.weight || 1) * 0.72));
  const startVector = new THREE.Vector3(start.x, start.y, start.z);
  const endVector = new THREE.Vector3(end.x, end.y, end.z);
  const direction = new THREE.Vector3().subVectors(endVector, startVector);
  const edgeObjects = [];

  if (style.dash) {
    const segments = 7;
    for (let index = 0; index < segments; index += 2) {
      const a = startVector.clone().add(direction.clone().multiplyScalar(index / segments));
      const b = startVector.clone().add(direction.clone().multiplyScalar(Math.min(index + 1, segments) / segments));
      edgeObjects.push(createCylinderBetween(THREE, a, b, weight, material.clone(), { type: "edge", edge }));
    }
  } else {
    edgeObjects.push(createCylinderBetween(THREE, start, end, weight, material, { type: "edge", edge }));
  }

  edgeObjects.forEach((object) => {
    sceneGroup.add(object);
    pickables.push(object);
  });

  if (edgeObjects.length && sceneGroup.children.length < 120) {
    const label = makeTextSprite(THREE, formatRelationLabel(edge.label), {
      fontSize: 22,
      color: style.color,
      background: "rgba(255,255,255,0.72)",
      scale: 0.23,
    });
    label.position.copy(startVector).add(endVector).multiplyScalar(0.5);
    label.position.y += 10;
    sceneGroup.add(label);
  }
}

async function renderGraph3D(renderable) {
  const serial = ++graphRenderSerial;
  disposeGraph3D();
  const container = document.getElementById("graph-3d-view");
  const detail = document.getElementById("graph-detail");
  if (!container) {
    return;
  }
  const { THREE, OrbitControls } = await loadThreeRuntime();
  if (serial !== graphRenderSerial) {
    return;
  }

  const bounds = container.getBoundingClientRect();
  const width = Math.max(320, Math.floor(bounds.width || 420));
  const height = Math.max(320, Math.floor(bounds.height || 420));
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xf8f7f2, 420, 980);
  const camera = new THREE.PerspectiveCamera(42, width / height, 1, 1800);
  camera.position.set(0, 120, 430);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(width, height);
  container.innerHTML = "";
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enablePan = true;
  controls.enableZoom = true;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.55;
  controls.minDistance = 110;
  controls.maxDistance = 760;

  scene.add(new THREE.AmbientLight(0xffffff, 1.15));
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.8);
  keyLight.position.set(180, 260, 240);
  scene.add(keyLight);
  const fillLight = new THREE.PointLight(0x1f6a73, 1.3, 900);
  fillLight.position.set(-220, -80, 240);
  scene.add(fillLight);

  const group = new THREE.Group();
  scene.add(group);
  const pickables = [];
  const layout = graphLayout(renderable.nodes);
  const nodeById = Object.fromEntries(renderable.nodes.map((node) => [node.id, node]));

  renderable.edges.forEach((edge) => {
    if (!layout[edge.source] || !layout[edge.target]) {
      return;
    }
    addEdgeToScene(THREE, group, pickables, edge, layout[edge.source], layout[edge.target]);
  });

  renderable.nodes.forEach((node) => {
    const position = layout[node.id];
    const size = Math.max(11, Math.min(26, 11 + Math.sqrt(Number(node.mention_count || 1)) * 3.2));
    const material = new THREE.MeshStandardMaterial({
      color: graphNodeColor(node.type),
      roughness: 0.42,
      metalness: isCharacterNode(node) ? 0.22 : 0.05,
      emissive: graphNodeColor(node.type),
      emissiveIntensity: isCharacterNode(node) ? 0.08 : 0.03,
    });
    const sphere = new THREE.Mesh(new THREE.SphereGeometry(size, 32, 24), material);
    sphere.position.set(position.x, position.y, position.z);
    sphere.userData = { type: "node", node };
    group.add(sphere);
    pickables.push(sphere);

    if (isCharacterNode(node)) {
      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(size + 4, 1.1, 8, 48),
        new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.66 })
      );
      ring.position.copy(sphere.position);
      ring.lookAt(camera.position);
      group.add(ring);
    }

    const label = makeTextSprite(THREE, node.label, {
      fontSize: isCharacterNode(node) ? 28 : 22,
      color: isCharacterNode(node) ? "#1f393d" : "#514a42",
      scale: isCharacterNode(node) ? 0.32 : 0.25,
    });
    label.position.set(position.x, position.y + size + 18, position.z);
    group.add(label);
  });

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  renderer.domElement.addEventListener("pointerdown", () => {
    controls.autoRotate = false;
  });
  renderer.domElement.addEventListener("click", (event) => {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(pickables, false)[0];
    if (!hit) {
      return;
    }
    if (hit.object.userData.type === "node") {
      showGraphNodeDetail(hit.object.userData.node);
    }
    if (hit.object.userData.type === "edge") {
      showGraphEdgeDetail(hit.object.userData.edge, nodeById);
    }
  });

  const resizeObserver = new ResizeObserver(() => {
    const nextBounds = container.getBoundingClientRect();
    const nextWidth = Math.max(320, Math.floor(nextBounds.width || 420));
    const nextHeight = Math.max(320, Math.floor(nextBounds.height || 420));
    camera.aspect = nextWidth / nextHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(nextWidth, nextHeight);
  });
  resizeObserver.observe(container);

  graph3DInstance = { THREE, scene, camera, renderer, controls, group, resizeObserver, animationFrame: null };
  function animate() {
    if (!graph3DInstance || graph3DInstance.renderer !== renderer) {
      return;
    }
    controls.update();
    renderer.render(scene, camera);
    graph3DInstance.animationFrame = requestAnimationFrame(animate);
  }
  animate();

  if (detail) {
    detail.textContent = "拖拽旋转图谱，滚轮缩放；点击人物或关系查看细节。";
  }
}

function renderGraphFallback(renderable, errorMessage) {
  disposeGraph3D();
  const canvas = document.getElementById("graph-canvas");
  if (!canvas) {
    return;
  }
  const nodes = renderable.nodes;
  const nodeById = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const width = 680;
  const height = 420;
  const radius = Math.max(110, Math.min(180, 40 + nodes.length * 12));
  const positions = {};
  nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(1, nodes.length);
    positions[node.id] = {
      x: width / 2 + Math.cos(angle) * radius,
      y: height / 2 + Math.sin(angle) * Math.min(radius, 140),
    };
  });
  const edgeMarkup = renderable.edges
    .map((edge) => {
      const source = positions[edge.source];
      const target = positions[edge.target];
      if (!source || !target) return "";
      const style = relationStyle(edge);
      return `<line class="graph-edge" stroke="${style.color}" stroke-width="${Math.max(1.5, edge.weight || 1)}" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" data-edge-id="${escapeHtml(edge.id)}"></line>`;
    })
    .join("");
  const nodeMarkup = nodes
    .map((node) => {
      const position = positions[node.id];
      const size = isCharacterNode(node) ? 22 : 15;
      return `<g class="graph-node" data-node-id="${escapeHtml(node.id)}"><circle class="graph-node-circle" cx="${position.x}" cy="${position.y}" r="${size}" fill="${graphNodeColor(node.type)}"></circle><text class="graph-node-label" x="${position.x}" y="${position.y + size + 14}">${escapeHtml(node.label)}</text></g>`;
    })
    .join("");
  canvas.innerHTML = `<svg class="graph-svg" viewBox="0 0 ${width} ${height}">${edgeMarkup}${nodeMarkup}</svg><p class="muted">3D 渲染加载失败，已切换到 2D 视图：${escapeHtml(errorMessage)}</p>`;
  canvas.querySelectorAll("[data-node-id]").forEach((nodeElement) => {
    nodeElement.addEventListener("click", (event) => {
      showGraphNodeDetail(nodeById[event.currentTarget.dataset.nodeId]);
    });
  });
  canvas.querySelectorAll("[data-edge-id]").forEach((edgeElement) => {
    edgeElement.addEventListener("click", (event) => {
      const edge = renderable.edges.find((item) => item.id === event.currentTarget.dataset.edgeId);
      showGraphEdgeDetail(edge, nodeById);
    });
  });
}

function showGraphNodeDetail(node) {
  const detail = document.getElementById("graph-detail");
  if (!detail || !node) {
    return;
  }
  detail.innerHTML = `
    <strong>${escapeHtml(node.label)}</strong> · ${escapeHtml(formatEntityType(node.type))}<br />
    提及：${node.mention_count || 0} 次<br />
    首次出现：第 ${(node.first_seen && node.first_seen.chapter) || "-"} 章，第 ${(node.first_seen && node.first_seen.paragraph) || "-"} 段<br />
    ${escapeHtml(node.summary || "暂无实体摘要。")}
  `;
}

function showGraphEdgeDetail(edge, nodeById = {}) {
  const detail = document.getElementById("graph-detail");
  if (!detail || !edge) {
    return;
  }
  const source = nodeById[edge.source]?.label || edge.source;
  const target = nodeById[edge.target]?.label || edge.target;
  const style = relationStyle(edge);
  detail.innerHTML = `
    <strong>${escapeHtml(source)} -> ${escapeHtml(target)}</strong><br />
    关系：<span style="color:${style.color}">${escapeHtml(formatRelationLabel(edge.label))}</span> · ${escapeHtml(style.label)}<br />
    位置：第 ${edge.valid_at_chapter || "-"} 章，第 ${edge.valid_at_paragraph || "-"} 段<br />
    证据：${(edge.citation_chunk_ids || []).slice(0, 4).map(escapeHtml).join(" / ") || "暂无证据 id"}<br />
    ${escapeHtml(edge.fact || "暂无关系事实描述。")}
  `;
}

async function refreshKnowledgeGraph() {
  if (!state.activeBook || !state.graphViewVisible) {
    return;
  }
  state.graphViewLoading = true;
  state.graphViewError = "";
  renderGraphPanel();
  try {
    const query = new URLSearchParams({
      chapter: String(state.activeChapter),
      paragraph: String(state.activeParagraphIndex || 0),
      limit: state.graphRelationMode === "people" ? "48" : "36",
      scope: state.graphViewScope,
    });
    state.graphViewData = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/memory/map?${query.toString()}`);
  } catch (error) {
    state.graphViewError = error.message;
  } finally {
    state.graphViewLoading = false;
    renderGraphPanel();
  }
}

async function toggleKnowledgeGraph() {
  state.graphViewVisible = !state.graphViewVisible;
  if (!state.graphViewVisible) {
    exitGraphFullscreen();
  }
  renderGraphPanel();
  if (state.graphViewVisible) {
    await refreshKnowledgeGraph();
  }
}

async function setKnowledgeGraphScope(scope) {
  if (scope !== "passage" && scope !== "chapter") {
    return;
  }
  state.graphViewScope = scope;
  renderGraphPanel();
  if (state.graphViewVisible) {
    await refreshKnowledgeGraph();
  }
}

async function setGraphRelationMode(mode) {
  if (mode !== "people" && mode !== "all") {
    return;
  }
  state.graphRelationMode = mode;
  if (state.graphViewVisible && state.graphViewData) {
    renderGraphPanel();
    return;
  }
  renderGraphPanel();
}

function zoomGraph3D(direction) {
  if (!graph3DInstance) {
    return;
  }
  const factor = direction > 0 ? 0.82 : 1.18;
  graph3DInstance.camera.position.multiplyScalar(factor);
  graph3DInstance.controls?.update?.();
}

function resetGraph3DView() {
  if (!graph3DInstance) {
    return;
  }
  graph3DInstance.camera.position.set(0, 120, 430);
  graph3DInstance.controls.target.set(0, 0, 0);
  graph3DInstance.controls.autoRotate = true;
  graph3DInstance.controls.update();
}

function exitGraphFullscreen() {
  state.graphExpanded = false;
  document.getElementById("insight-drawer")?.classList.remove("is-graph-fullscreen");
  document.getElementById("graph-panel")?.classList.remove("is-expanded");
  document.body.classList.remove("graph-fullscreen-active");
  const expandButton = document.getElementById("graph-expand-btn");
  if (expandButton) {
    expandButton.textContent = "全屏";
    expandButton.setAttribute("aria-label", "进入图谱全屏");
  }
}

function toggleGraphExpanded() {
  state.graphExpanded = !state.graphExpanded;
  if (state.graphExpanded) {
    setDrawer("insight-drawer", true);
    setInsightTab("map");
  }
  renderGraphPanel();
}

function bubblesForChunk(chunkId) {
  return (state.inlineBubblesByChunk[chunkId] || []).slice(0, 2);
}

function bubbleClassToken(value) {
  const token = String(value || "detail").toLowerCase();
  return /^[a-z0-9_-]+$/.test(token) ? token : "detail";
}

function createInlineBubbleMarkup(text, chunkId) {
  const source = String(text || "");
  const bubbles = bubblesForChunk(chunkId);
  if (!bubbles.length) {
    return escapeHtml(source);
  }

  const ranges = [];
  bubbles.forEach((bubble, index) => {
    const anchor = String(bubble.anchor_text || "").trim();
    if (!anchor) {
      return;
    }
    const start = source.indexOf(anchor);
    if (start < 0) {
      return;
    }
    const end = start + anchor.length;
    const overlaps = ranges.some((range) => start < range.end && end > range.start);
    if (!overlaps) {
      ranges.push({ start, end, bubble, index });
    }
  });

  if (!ranges.length) {
    return escapeHtml(source);
  }

  ranges.sort((a, b) => a.start - b.start);
  let html = "";
  let cursor = 0;
  ranges.forEach(({ start, end, bubble, index }) => {
    const bubbleType = bubbleClassToken(bubble.bubble_type || bubble.emphasis || "detail");
    html += escapeHtml(source.slice(cursor, start));
    html += `<span
      class="inline-bubble-highlight inline-bubble-${bubbleType}"
      tabindex="0"
      data-bubble-id="${escapeHtml(bubble.bubble_id || `${chunkId}-${index}`)}"
      data-bubble-label="${escapeHtml(bubble.label || "旁注")}"
      data-bubble-comment="${escapeHtml(bubble.comment || "")}"
      data-bubble-type="${escapeHtml(bubbleType)}"
    >${escapeHtml(source.slice(start, end))}</span>`;
    cursor = end;
  });
  html += escapeHtml(source.slice(cursor));
  return html;
}

function wireInlineBubbleToggles() {
  document.querySelectorAll(".inline-bubble-highlight").forEach((highlight) => {
    highlight.addEventListener("mouseenter", (event) => {
      bubbleHoverSticky = false;
      showBubbleHoverCard(event.currentTarget);
    });
    highlight.addEventListener("focus", (event) => {
      bubbleHoverSticky = false;
      showBubbleHoverCard(event.currentTarget);
    });
    highlight.addEventListener("mouseleave", () => {
      if (!bubbleHoverSticky) {
        closeInlineBubbleNotes();
      }
    });
    highlight.addEventListener("blur", () => {
      if (!bubbleHoverSticky) {
        closeInlineBubbleNotes();
      }
    });
    highlight.addEventListener("click", (event) => {
      const target = event.currentTarget;
      const card = document.getElementById("bubble-hover-card");
      const isSameOpen = card?.classList.contains("is-open") && card.dataset.bubbleId === target.dataset.bubbleId;
      if (isSameOpen && bubbleHoverSticky) {
        closeInlineBubbleNotes();
      } else {
        bubbleHoverSticky = true;
        showBubbleHoverCard(target);
      }
      event.stopPropagation();
    });
  });
}

function ensureBubbleHoverCard() {
  let card = document.getElementById("bubble-hover-card");
  if (!card) {
    card = document.createElement("div");
    card.id = "bubble-hover-card";
    card.className = "bubble-hover-card";
    document.body.appendChild(card);
  }
  return card;
}

function showBubbleHoverCard(anchor) {
  if (!anchor) {
    return;
  }
  const card = ensureBubbleHoverCard();
  card.dataset.bubbleId = anchor.dataset.bubbleId || "";
  card.innerHTML = `
    <strong>${escapeHtml(anchor.dataset.bubbleLabel || "旁注")}</strong>
    <span>${escapeHtml(anchor.dataset.bubbleComment || "")}</span>
  `;
  document.querySelectorAll(".inline-bubble-highlight.is-active").forEach((item) => item.classList.remove("is-active"));
  anchor.classList.add("is-active");
  card.classList.add("is-open");

  const rect = anchor.getBoundingClientRect();
  const margin = 12;
  const cardWidth = Math.min(300, Math.max(220, window.innerWidth - margin * 2));
  card.style.width = `${cardWidth}px`;
  card.style.left = "0px";
  card.style.top = "0px";
  const cardRect = card.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - cardWidth / 2;
  left = Math.min(window.innerWidth - cardWidth - margin, Math.max(margin, left));
  let top = rect.bottom + 8;
  if (top + cardRect.height > window.innerHeight - margin) {
    top = rect.top - cardRect.height - 8;
  }
  top = Math.min(window.innerHeight - cardRect.height - margin, Math.max(margin, top));
  card.style.left = `${left}px`;
  card.style.top = `${top}px`;
}

function closeInlineBubbleNotes() {
  bubbleHoverSticky = false;
  const card = document.getElementById("bubble-hover-card");
  if (card) {
    card.classList.remove("is-open");
    card.removeAttribute("data-bubble-id");
  }
  document.querySelectorAll(".inline-bubble-highlight.is-active").forEach((highlight) => highlight.classList.remove("is-active"));
}

function renderPassages() {
  const container = document.getElementById("passage-list");
  const pageItems = getCurrentPageItems();
  container.innerHTML = "";
  updatePageIndicator();

  if (!pageItems.length) {
    container.innerHTML = `<p class="muted">There is no visible content in the current chapter yet.</p>`;
    return;
  }

  const page = document.createElement("article");
  page.className = "reading-page";
  page.innerHTML = `
    <header class="reading-page-header">
      <span>Chapter ${state.activeChapter}</span>
      <span>Page ${state.activePageIndex + 1}</span>
    </header>
  `;

  pageItems.forEach((passage, index) => {
    const paragraphIndex = passage.paragraph_index ?? passage._index + 1;
    const wrapper = document.createElement("article");
    wrapper.className = `reading-paragraph ${paragraphIndex === state.activeParagraphIndex ? "is-selected" : ""}`;
    wrapper.dataset.paragraphIndex = String(paragraphIndex);
    wrapper.innerHTML = `
      <span class="paragraph-marker">${paragraphIndex}</span>
      <div class="reading-paragraph-text">${createInlineBubbleMarkup(passage.text || "", passage.chunk_id)}</div>
    `;
    wrapper.addEventListener("click", () => {
      const selectedText = getSelectedTextWithin(wrapper);
      selectPassage(passage, index, pageItems, { selectedText });
    });
    page.appendChild(wrapper);
  });

  container.appendChild(page);
  wireInlineBubbleToggles();
}

function getSelectedTextWithin(node) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) {
    return "";
  }
  const selectedText = compactInlineText(selection.toString());
  if (!selectedText) {
    return "";
  }
  const anchorNode = selection.anchorNode;
  const focusNode = selection.focusNode;
  if ((anchorNode && node.contains(anchorNode)) || (focusNode && node.contains(focusNode))) {
    return selectedText;
  }
  return "";
}

function selectPassage(passage, index, passages, options = {}) {
  state.activeParagraphIndex = passage.paragraph_index ?? passage._index + 1;
  state.activeChunkId = passage.chunk_id || null;
  updateProgressFromPassage(passage);
  buildSelectionFromPassage(passage, index, passages, options.selectedText || "");
  renderSelectionPreview();
  renderReaderHeader();
  renderPassages();
  fetchInlineBubbles({ selectedOnly: true }).catch((error) => console.error(error));
  if (state.graphViewVisible) {
    refreshKnowledgeGraph().catch((error) => console.error(error));
  }
}

function setPage(pageIndex) {
  const pages = getCurrentPages();
  if (!pages.length) {
    state.activePageIndex = 0;
    renderPassages();
    return;
  }
  state.activePageIndex = Math.max(0, Math.min(pageIndex, pages.length - 1));
  const pageItems = getCurrentPageItems();
  const firstVisible = pageItems[0];
  if (firstVisible) {
    state.activeParagraphIndex = firstVisible.paragraph_index ?? firstVisible._index + 1;
    state.activeChunkId = firstVisible.chunk_id || null;
    updateProgressFromPassage(firstVisible);
    buildSelectionFromPassage(firstVisible, 0, pageItems);
  }
  renderReaderHeader();
  renderSelectionPreview();
  renderPassages();
  scheduleDwellBubbles();
}

function scheduleDwellBubbles() {
  if (state.bubbleTimer) {
    window.clearTimeout(state.bubbleTimer);
    state.bubbleTimer = null;
  }
  if (!state.activeBook || !getCurrentPageItems().length) {
    return;
  }
  state.bubbleTimer = window.setTimeout(() => {
    fetchInlineBubbles().catch((error) => console.error(error));
  }, 12000);
}

async function fetchInlineBubbles(options = {}) {
  if (!state.activeBook) {
    return;
  }
  const selectedOnly = options.selectedOnly === true;
  const pageItems =
    selectedOnly && state.activeChunkId
      ? getCurrentPageItems().filter((item) => item.chunk_id === state.activeChunkId)
      : getCurrentPageItems();
  if (!pageItems.length) {
    state.inlineBubblesByChunk = {};
    renderPassages();
    return;
  }
  try {
    const bubbles = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/bubbles/candidates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_id: state.activeBook,
        current_chapter: state.activeChapter,
        visible_chunk_ids: pageItems.map((item) => item.chunk_id),
        persona_id: state.personaId,
        assistant_mode: state.assistantMode,
        character_name: state.activeCharacterName,
        max_bubbles: selectedOnly ? 2 : 1,
      }),
    });
    const map = {};
    bubbles.forEach((bubble) => {
      if (!map[bubble.chunk_id]) {
        map[bubble.chunk_id] = [];
      }
      map[bubble.chunk_id].push(bubble);
    });
    state.inlineBubblesByChunk = map;
    renderPassages();
  } catch (error) {
    console.error("Inline bubble generation failed", error);
  }
}

async function loadCharacterCandidates() {
  if (!state.activeBook) {
    state.characterCandidates = [];
    renderCharacterCandidates();
    return;
  }

  setButtonLoading("character-generate-btn", true, "加载人物...");
  try {
    state.characterCandidates = await fetchJSON(
      `/api/books/${encodeURIComponent(state.activeBook)}/characters?current_chapter=${state.activeChapter}&limit=200`
    );
    renderCharacterCandidates();
  } catch (error) {
    state.characterCandidates = [];
    renderCharacterCandidates();
    document.getElementById("character-profile-card").innerHTML = `<p class="muted">Failed to load character candidates: ${escapeHtml(
      error.message
    )}</p>`;
  } finally {
    setButtonLoading("character-generate-btn", false);
  }
}

async function generateCharacterProfile() {
  if (!state.activeBook) {
    return;
  }

  const typedName = document.getElementById("character-input").value.trim();
  const selectedName =
    document.getElementById("character-select")?.value.trim() ||
    document.getElementById("assistant-character-select")?.value.trim() ||
    state.activeCharacterName;
  const characterName = typedName || selectedName;
  if (!characterName) {
    document.getElementById("character-profile-card").innerHTML =
      `<p class="muted">请先选择当前书中的人物，或手动输入人物名。</p>`;
    return;
  }

  setSelectedCharacter(characterName);
  renderAssistantStatus();
  setButtonLoading("character-generate-btn", true, "生成中...");
  startPendingWorkflow("characterProfile", "building-profile");

  try {
    const profile = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/characters/profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_id: state.activeBook,
        character_name: characterName,
        current_chapter: state.activeChapter,
      }),
    });
    state.activeCharacterProfile = profile;
    setSelectedCharacter(profile.character_name, { keepProfile: true, skipRender: true });
    renderCharacterProfile();
    renderAssistantStatus();
    if (state.assistantMode === "character") {
      await fetchInlineBubbles({ selectedOnly: true });
    }
    finishPendingWorkflow("done", "Character profile ready", "The character profile has been built from the currently visible reading scope.");
  } catch (error) {
    document.getElementById("character-profile-card").innerHTML = `<p class="muted">Failed to build character profile: ${escapeHtml(
      error.message
    )}</p>`;
    setPendingState(false);
  } finally {
    if (state.pendingWorkflow) {
      releasePendingState();
    }
    setButtonLoading("character-generate-btn", false);
  }
}

async function setActiveChapter(chapter) {
  state.activeChapter = Number(chapter);
  state.activePageIndex = 0;
  state.chapterEnteredAt = Date.now();
  state.activeChunkId = null;
  state.activeCharacterProfile = null;
  resetSelection();

  const passages = getCurrentPassages();
  const first = passages[0] || null;
  state.activeParagraphIndex = first ? first.paragraph_index ?? 1 : null;
  state.activeChunkId = first?.chunk_id || null;
  state.readingProgress = {
    book_id: state.activeBook || "",
    chapter_id: state.activeChapter,
    section_id: `sec-${state.activeChapter}`,
    paragraph_id: first ? String(first.paragraph_index ?? 1) : "",
    token_offset: first?.text ? first.text.length : 0,
    scroll_offset: 0,
    dwell_seconds: 0,
    updated_at: new Date().toISOString(),
  };
  if (first) {
    const pageItems = getCurrentPageItems();
    buildSelectionFromPassage(first, 0, pageItems.length ? pageItems : passages);
  }

  renderChapterNav();
  renderChapterSelects();
  renderReaderHeader();
  renderSelectionPreview();
  renderPassages();
  renderCharacterProfile();
  await loadCharacterCandidates();
  scheduleDwellBubbles();
  if (state.graphViewVisible) {
    await refreshKnowledgeGraph();
  }
}

async function loadPersonas() {
  state.personas = await fetchJSON("/api/personas");
  const select = document.getElementById("persona-select");
  select.innerHTML = state.personas
    .map((persona) => `<option value="${escapeHtml(persona.persona_id)}">${escapeHtml(persona.name)}</option>`)
    .join("");

  const preferred =
    state.personas.find((persona) => persona.persona_id === state.personaId) ||
    state.personas.find((persona) => persona.persona_id !== "neutral") ||
    state.personas[0] ||
    null;

  if (preferred) {
    state.personaId = preferred.persona_id;
    select.value = state.personaId;
  }

  select.addEventListener("change", async (event) => {
    state.personaId = event.target.value;
    renderPersonaDetails();
    renderAssistantStatus();
    if (state.assistantMode === "persona") {
      scheduleDwellBubbles();
    }
  });

  renderPersonaDetails();
}

async function loadBooks() {
  state.books = await fetchJSON("/api/books");
  renderBooks();
}

function renderMemoryStatus() {
  const badge = document.getElementById("memory-status-badge");
  const text = document.getElementById("memory-status-text");
  const callout = document.getElementById("memory-callout");
  const calloutText = document.getElementById("memory-callout-text");
  if (!badge || !text) {
    return;
  }
  if (!state.activeBook) {
    badge.textContent = "unknown";
    text.textContent = "Open a book to inspect memory status.";
    callout?.classList.add("is-hidden");
    return;
  }
  if (!state.memoryStatus) {
    badge.textContent = "loading";
    text.textContent = "Checking memory status...";
    callout?.classList.add("is-hidden");
    return;
  }
  const reasons = state.memoryStatus.degraded_reasons || [];
  const entityCount = state.memoryStatus.entity_count || 0;
  const memoryCount = state.memoryStatus.memory_count || 0;
  const vectorOnlyIssue =
    state.memoryStatus.status === "degraded" &&
    entityCount > 0 &&
    reasons.length > 0 &&
    reasons.every((reason) => /embedding|embeddings|vector/i.test(reason));
  badge.textContent = vectorOnlyIssue ? "graph ready" : state.memoryStatus.status || "unknown";
  text.textContent = vectorOnlyIssue
    ? `${entityCount} entities · ${memoryCount} memories ready. Vector search is skipped; keyword and graph retrieval are active.`
    : reasons.length
      ? `${entityCount} entities · ${reasons.join(" / ")}`
      : `${entityCount} entities · ${memoryCount} memories ready.`;
  const shouldShowCallout =
    !vectorOnlyIssue &&
    (state.memoryStatus.status === "missing" ||
      state.memoryStatus.status === "failed" ||
      !state.memoryStatus.index_ready ||
      !state.memoryStatus.graph_ready);
  callout?.classList.toggle("is-hidden", !shouldShowCallout);
  if (calloutText) {
    calloutText.textContent = shouldShowCallout
      ? "Memory map and evidence-aware answers need a rebuild for this book."
      : "";
  }
}

async function loadMemoryStatus() {
  if (!state.activeBook) {
    return;
  }
  state.memoryStatus = null;
  renderMemoryStatus();
  try {
    state.memoryStatus = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/memory/status`);
  } catch (error) {
    state.memoryStatus = { status: "failed", degraded_reasons: [error.message] };
  }
  renderMemoryStatus();
}

async function rebuildMemory() {
  if (!state.activeBook) {
    return;
  }
  setButtonLoading("memory-rebuild-btn", true, "Rebuilding...");
  setPendingState(true, "memory-rebuild", "Rebuilding memory", "Rebuilding entity registry, graph memory, and retrieval index.");
  try {
    const job = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/memory/rebuild-jobs`, {
      method: "POST",
    });
    let current = job;
    for (;;) {
      setPendingState(
        current.status !== "completed" && current.status !== "failed",
        current.stage || "memory-rebuild",
        current.title || "Rebuilding memory",
        current.message || "Memory rebuild is running.",
        current.percent || 0
      );
      if (current.status === "completed") {
        break;
      }
      if (current.status === "failed") {
        throw new Error(current.error || current.message || "Memory rebuild failed.");
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
      current = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/memory/rebuild-jobs/${current.job_id}`);
    }
    await loadMemoryStatus();
    state.graphViewData = null;
    if (state.graphViewVisible) {
      await refreshKnowledgeGraph();
    }
    finishPendingWorkflow("done", "Memory ready", "Entity registry, layered memory, and map data are ready.");
  } catch (error) {
    setPendingState(false);
    pushConversation("assistant", `Memory rebuild failed: ${error.message}`);
    renderChatHistory();
  } finally {
    setButtonLoading("memory-rebuild-btn", false);
    if (state.pendingWorkflow) {
      releasePendingState();
    }
  }
}

async function openBook(bookId) {
  state.activeBook = bookId;
  try {
    window.localStorage.setItem(LAST_OPENED_BOOK_KEY, bookId);
  } catch (_error) {
    // Ignore storage errors and keep the current session alive.
  }
  state.activeBookDetail = await fetchJSON(`/api/books/${encodeURIComponent(bookId)}`);
  state.personaConversation = [];
  state.characterConversation = [];
  state.activeCharacterName = "";
  state.activeCharacterProfile = null;
  state.graphViewData = null;
  state.graphViewError = "";
  renderBooks();
  renderChatHistory();
  renderGraphPanel();
  await loadMemoryStatus();
  await setActiveChapter(getFirstReadableChapter());
}

async function uploadBook(event) {
  event.preventDefault();
  const input = document.getElementById("file-input");
  if (!input.files[0]) {
    return;
  }

  setPendingState(true, "starting-upload", "Starting upload", "Creating an upload job and waiting for the pipeline to begin.");
  try {
    const payload = new FormData();
    payload.append("file", input.files[0]);
    const job = await fetchJSON("/api/upload-jobs", {
      method: "POST",
      body: payload,
    });
    applyUploadJobState(job);
    const uploaded = await waitForUploadJob(job.job_id);
    await loadBooks();
    await openBook(uploaded.book_id);
    input.value = "";
    finishPendingWorkflow(
      "done",
      "Temporal graph ready",
      `${uploaded.book_title || uploaded.title} has been parsed and its temporal graph is ready for reading.`
    );
  } catch (error) {
    pushConversation("assistant", `Import failed: ${error.message}`);
    renderChatHistory();
    setPendingState(false);
  } finally {
    if (state.pendingWorkflow) {
      releasePendingState();
    }
  }
}

function renderComposerQuestion(text = "") {
  document.getElementById("question-input").value = text;
}

async function askAssistant() {
  if (!state.activeBook) {
    return;
  }
  const question = document.getElementById("question-input").value.trim();
  if (!question) {
    return;
  }

  const history = currentConversation().slice(-8);
  pushConversation("user", question);
  renderChatHistory();
  renderComposerQuestion("");
  setButtonLoading("ask-btn", true, "Generating answer...");
  startPendingWorkflow(
    state.assistantMode === "persona" ? "personaQa" : "characterQa",
    state.assistantMode === "persona" ? "persona-answering" : "character-answering"
  );

  try {
    let answer = "";
    let answerMeta = {};
    if (state.assistantMode === "persona") {
      const response = await fetchJSON("/api/qa", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: state.activeBook,
          question,
          highlight_text: getQuestionContextText(),
          current_chapter: state.activeChapter,
          persona_id: state.personaId,
          conversation_history: history,
        }),
      });
      answer = response.answer;
      answerMeta = {
        citations: response.citations || [],
        confidence: response.confidence,
        unsupported_claim_count: response.unsupported_claim_count,
      };
    } else {
      if (!state.activeCharacterName) {
        throw new Error("Choose or build a character profile before asking the character agent.");
      }
      const response = await fetchJSON(`/api/books/${encodeURIComponent(state.activeBook)}/characters/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: state.activeBook,
          character_name: state.activeCharacterName,
          question,
          current_chapter: state.activeChapter,
          conversation_history: history,
        }),
      });
      answer = response.answer;
      state.activeCharacterProfile = response.profile;
      answerMeta = {
        citations: response.citations || [],
        confidence: response.confidence,
        unsupported_claim_count: response.unsupported_claim_count,
      };
      renderCharacterProfile();
    }
    pushConversation("assistant", answer, answerMeta);
    renderChatHistory();
    finishPendingWorkflow(
      "done",
      state.assistantMode === "persona" ? "Literary answer ready" : "Character answer ready",
      state.assistantMode === "persona"
        ? "The literary agent answer has been generated from visible book context, persona RAG, and prompt policy."
        : "The character agent answer has been generated from visible book context and character profile grounding."
    );
  } catch (error) {
    pushConversation("assistant", `Question failed: ${error.message}`);
    renderChatHistory();
    setPendingState(false);
  } finally {
    setButtonLoading("ask-btn", false);
    if (state.pendingWorkflow) {
      releasePendingState();
    }
  }
}

async function summarizeChapter() {
  if (!state.activeBook) {
    return;
  }

  setButtonLoading("summary-btn", true, "Summarizing...");
  startPendingWorkflow("chapterSummary", "chapter-summary");
  try {
    const response = await fetchJSON("/api/summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_id: state.activeBook,
        current_chapter: state.activeChapter,
        persona_id: state.personaId,
      }),
    });
    state.assistantMode = "persona";
    renderAssistantMode();
    pushConversation("assistant", response.summary);
    renderChatHistory();
    finishPendingWorkflow("done", "Chapter summary ready", "The current chapter summary has been generated from visible chapter context and graph state.");
  } catch (error) {
    pushConversation("assistant", `Summary failed: ${error.message}`);
    renderChatHistory();
    setPendingState(false);
  } finally {
    setButtonLoading("summary-btn", false);
    if (state.pendingWorkflow) {
      releasePendingState();
    }
  }
}

function clearConversation() {
  if (state.assistantMode === "persona") {
    state.personaConversation = [];
  } else {
    state.characterConversation = [];
  }
  renderChatHistory();
}

function setAssistantMode(mode) {
  state.assistantMode = mode;
  renderAssistantMode();
  scheduleDwellBubbles();
}

function setDrawer(drawerId, isOpen) {
  const drawer = document.getElementById(drawerId);
  const backdrop = document.getElementById("drawer-backdrop");
  if (!drawer || !backdrop) {
    return;
  }
  if (!isOpen && drawerId === "insight-drawer") {
    state.graphExpanded = false;
    drawer.classList.remove("is-graph-fullscreen");
    document.body.classList.remove("graph-fullscreen-active");
  }
  drawer.classList.toggle("is-open", isOpen);
  const anyOpen = Array.from(document.querySelectorAll(".drawer")).some((item) =>
    item.classList.contains("is-open")
  );
  backdrop.classList.toggle("is-open", anyOpen);
  if (!isOpen && drawerId === "insight-drawer") {
    renderGraphPanel();
  }
}

function closeDrawers() {
  state.graphExpanded = false;
  document.querySelectorAll(".drawer").forEach((drawer) => drawer.classList.remove("is-open"));
  document.getElementById("insight-drawer")?.classList.remove("is-graph-fullscreen");
  document.body.classList.remove("graph-fullscreen-active");
  document.getElementById("drawer-backdrop")?.classList.remove("is-open");
  renderGraphPanel();
}

function setInsightTab(tab) {
  document.querySelectorAll(".insight-tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === tab);
  });
  document.querySelectorAll(".insight-pane").forEach((pane) => {
    pane.classList.toggle("is-active", pane.dataset.pane === tab);
  });
  document.getElementById("insight-drawer")?.classList.toggle("is-map-mode", tab === "map");
  if (tab === "map" && state.graphViewVisible) {
    refreshKnowledgeGraph().catch((error) => console.error(error));
  }
}

function toggleAssistantPanel(forceOpen = null) {
  const panel = document.getElementById("assistant-panel");
  const stage = document.getElementById("reading-stage");
  if (!panel) {
    return;
  }
  const shouldOpen = forceOpen === null ? panel.classList.contains("is-collapsed") : forceOpen;
  panel.classList.toggle("is-collapsed", !shouldOpen);
  stage?.classList.toggle("assistant-collapsed", !shouldOpen);
}

function wireEvents() {
  document.getElementById("upload-form").addEventListener("submit", uploadBook);
  document.getElementById("ask-btn").addEventListener("click", askAssistant);
  document.getElementById("summary-btn").addEventListener("click", summarizeChapter);
  document.getElementById("graph-toggle-btn").addEventListener("click", () => {
    toggleKnowledgeGraph().catch((error) => console.error(error));
  });
  document.getElementById("graph-refresh-btn").addEventListener("click", () => {
    refreshKnowledgeGraph().catch((error) => console.error(error));
  });
  document.getElementById("graph-scope-passage-btn").addEventListener("click", () => {
    setKnowledgeGraphScope("passage").catch((error) => console.error(error));
  });
  document.getElementById("graph-scope-chapter-btn").addEventListener("click", () => {
    setKnowledgeGraphScope("chapter").catch((error) => console.error(error));
  });
  document.getElementById("graph-mode-people-btn").addEventListener("click", () => {
    setGraphRelationMode("people").catch((error) => console.error(error));
  });
  document.getElementById("graph-mode-all-btn").addEventListener("click", () => {
    setGraphRelationMode("all").catch((error) => console.error(error));
  });
  document.getElementById("graph-zoom-out-btn").addEventListener("click", () => zoomGraph3D(-1));
  document.getElementById("graph-zoom-in-btn").addEventListener("click", () => zoomGraph3D(1));
  document.getElementById("graph-reset-btn").addEventListener("click", resetGraph3DView);
  document.getElementById("graph-expand-btn").addEventListener("click", toggleGraphExpanded);
  document.getElementById("clear-chat-btn").addEventListener("click", clearConversation);
  document.getElementById("persona-mode-btn").addEventListener("click", () => setAssistantMode("persona"));
  document.getElementById("character-mode-btn").addEventListener("click", () => setAssistantMode("character"));
  document.getElementById("character-select").addEventListener("change", (event) => {
    setSelectedCharacter(event.target.value);
    if (event.target.value.trim()) {
      setAssistantMode("character");
    }
  });
  document.getElementById("assistant-character-select").addEventListener("change", (event) => {
    setSelectedCharacter(event.target.value);
    if (event.target.value.trim()) {
      setAssistantMode("character");
    }
  });
  document.getElementById("character-generate-btn").addEventListener("click", generateCharacterProfile);
  document.getElementById("library-open-btn").addEventListener("click", () => setDrawer("library-drawer", true));
  document.getElementById("mobile-library-btn").addEventListener("click", () => setDrawer("library-drawer", true));
  document.getElementById("library-close-btn").addEventListener("click", () => setDrawer("library-drawer", false));
  document.getElementById("insight-open-btn").addEventListener("click", () => setDrawer("insight-drawer", true));
  document.getElementById("mobile-insight-btn").addEventListener("click", () => setDrawer("insight-drawer", true));
  document.getElementById("insight-close-btn").addEventListener("click", () => setDrawer("insight-drawer", false));
  document.getElementById("drawer-backdrop").addEventListener("click", closeDrawers);
  document.getElementById("assistant-toggle-btn").addEventListener("click", () => toggleAssistantPanel());
  document.getElementById("mobile-assistant-btn").addEventListener("click", () => toggleAssistantPanel(true));
  document.getElementById("assistant-close-btn").addEventListener("click", () => toggleAssistantPanel(false));
  document.getElementById("memory-rebuild-btn").addEventListener("click", () => {
    rebuildMemory().catch((error) => console.error(error));
  });
  document.getElementById("memory-callout-rebuild-btn").addEventListener("click", () => {
    rebuildMemory().catch((error) => console.error(error));
  });
  document.querySelectorAll(".insight-tab").forEach((button) => {
    button.addEventListener("click", () => setInsightTab(button.dataset.tab));
  });
  document.getElementById("chapter-select").addEventListener("change", async (event) => {
    await setActiveChapter(Number(event.target.value));
  });
  document.getElementById("paragraph-jump").addEventListener("change", (event) => {
    const targetValue = event.target.value;
    const allPassages = getCurrentPassages();
    const passage = allPassages.find((item, index) => String(item.paragraph_index ?? index + 1) === targetValue);
    if (!passage) {
      return;
    }
    const pageIndex = getCurrentPages().findIndex((page) =>
      page.some((item) => String(item.paragraph_index ?? item._index + 1) === targetValue)
    );
    if (pageIndex >= 0) {
      state.activePageIndex = pageIndex;
    }
    const pageItems = getCurrentPageItems();
    const indexInPage = pageItems.findIndex((item) => item.chunk_id === passage.chunk_id);
    selectPassage(passage, Math.max(0, indexInPage), pageItems.length ? pageItems : allPassages);
  });
  document.getElementById("prev-page-btn").addEventListener("click", () => setPage(state.activePageIndex - 1));
  document.getElementById("next-page-btn").addEventListener("click", () => setPage(state.activePageIndex + 1));
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".inline-bubble-highlight")) {
      closeInlineBubbleNotes();
    }
  });
  document.addEventListener("keydown", (event) => {
    const drawerIsFullscreen = document.getElementById("insight-drawer")?.classList.contains("is-graph-fullscreen");
    if (event.key === "Escape" && (state.graphExpanded || drawerIsFullscreen)) {
      exitGraphFullscreen();
      renderGraphPanel();
      event.preventDefault();
    }
  });
}

async function bootstrap() {
  wireEvents();
  renderPendingWorkflow();
  renderReaderHeader();
  renderSelectionPreview();
  renderCharacterProfile();
  renderAssistantMode();
  renderGraphPanel();
  await loadPersonas();
  await loadBooks();
  let preferredBookId = "";
  try {
    preferredBookId = window.localStorage.getItem(LAST_OPENED_BOOK_KEY) || "";
  } catch (_error) {
    preferredBookId = "";
  }

  const resolvedBookId =
    (preferredBookId && state.books.find((book) => book.book_id === preferredBookId)?.book_id) ||
    state.books[state.books.length - 1]?.book_id ||
    state.books[0]?.book_id;

  if (resolvedBookId) {
    await openBook(resolvedBookId);
  }
}

bootstrap().catch((error) => {
  console.error(error);
  const message = error?.message || "Unknown startup error";
  setPendingState(true, "startup-error", "App could not finish loading", message, 100);
  const passageList = document.getElementById("passage-list");
  if (passageList) {
    passageList.innerHTML = `
      <div class="startup-error">
        <strong>应用没有完整加载</strong>
        <p>${escapeHtml(message)}</p>
        <p class="muted">请确认后端服务正在运行，然后刷新页面。</p>
      </div>
    `;
  }
});
