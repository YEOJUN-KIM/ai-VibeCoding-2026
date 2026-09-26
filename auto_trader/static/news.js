const $ = (selector) => document.querySelector(selector);
let csrfToken = "";

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = !["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken ? { "X-CSRF-Token": csrfToken } : {};
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...headers, ...(options.headers || {}) } });
  if (response.status === 401) { window.location.replace("/login"); throw new Error("로그인이 필요합니다."); }
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "요청에 실패했습니다." }));
    throw new Error(error.detail || "요청에 실패했습니다.");
  }
  return response.status === 204 ? null : response.json();
}

function formatNewsTime(value) {
  const date = new Date(value);
  const elapsedMinutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (elapsedMinutes < 60) return `${Math.max(1, elapsedMinutes)}분 전`;
  if (elapsedMinutes < 1440) return `${Math.floor(elapsedMinutes / 60)}시간 전`;
  return date.toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
}

function articleElement(article) {
  const link = document.createElement("a");
  link.className = "news-item";
  link.href = article.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  const meta = document.createElement("span");
  meta.className = "news-item-meta";
  const source = document.createElement("strong");
  source.textContent = article.source;
  const time = document.createElement("time");
  time.dateTime = article.published_at;
  time.textContent = formatNewsTime(article.published_at);
  const title = document.createElement("span");
  title.className = "news-item-title";
  title.textContent = article.title;
  const arrow = document.createElement("span");
  arrow.className = "news-item-arrow";
  arrow.textContent = "↗";
  meta.append(source, time);
  link.append(meta, title, arrow);
  return link;
}

function issueElement(issue) {
  const wrapper = document.createElement("article");
  wrapper.className = "news-issue";
  const summary = document.createElement("div");
  summary.className = "news-issue-summary";
  const stats = document.createElement("span");
  stats.className = "news-issue-stats";
  stats.textContent = `${issue.article_count}개 기사 · ${issue.source_count}개 매체 · ${formatNewsTime(issue.latest_at)}`;
  const topics = document.createElement("div");
  topics.className = "news-issue-topics";
  issue.key_topics.slice(0, 3).forEach((topic) => {
    const tag = document.createElement("span");
    tag.textContent = `# ${topic}`;
    topics.append(tag);
  });
  summary.append(stats, topics);
  wrapper.append(summary, articleElement(issue.articles[0]));
  const related = issue.articles.slice(1);
  if (related.length) {
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "news-related-toggle";
    toggle.textContent = `관련 기사 ${related.length}개 펼치기`;
    toggle.setAttribute("aria-expanded", "false");
    const relatedList = document.createElement("div");
    relatedList.className = "news-related-list hidden";
    related.forEach((article) => relatedList.append(articleElement(article)));
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      toggle.textContent = expanded ? `관련 기사 ${related.length}개 펼치기` : "관련 기사 접기";
      relatedList.classList.toggle("hidden", expanded);
    });
    wrapper.append(toggle, relatedList);
  }
  return wrapper;
}

function coreIssueElement(issue, rank) {
  const link = document.createElement("a");
  link.className = "news-core-card";
  link.href = issue.articles[0].url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  const head = document.createElement("div");
  head.className = "news-core-card-head";
  const number = document.createElement("span");
  number.className = "news-core-rank";
  number.textContent = String(rank).padStart(2, "0");
  const topic = document.createElement("span");
  topic.className = "news-core-topic";
  topic.textContent = issue.key_topics.slice(0, 2).join(" · ") || "시장 이슈";
  head.append(number, topic);
  const title = document.createElement("strong");
  title.textContent = issue.title;
  const meta = document.createElement("span");
  meta.className = "news-core-meta";
  meta.textContent = `${issue.article_count}개 기사 · ${issue.source_count}개 매체 · ${formatNewsTime(issue.latest_at)}`;
  link.append(head, title, meta);
  return link;
}

function renderCoreIssues(data) {
  const coreIssues = data.issues.slice(0, 5);
  $("#news-core-title").textContent = data.query === "오늘의 주요 증시 이슈"
    ? "오늘의 핵심 이슈"
    : `‘${data.query}’ 핵심 이슈`;
  $("#news-core-count").textContent = `${coreIssues.length}개 선정`;
  const grid = $("#news-core-grid");
  grid.replaceChildren();
  if (!coreIssues.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "표시할 핵심 이슈가 없습니다.";
    grid.append(empty);
    return;
  }
  coreIssues.forEach((issue, index) => grid.append(coreIssueElement(issue, index + 1)));
}

function renderDigest(data) {
  $("#news-brief-title").textContent = `‘${data.query}’ 보도 흐름`;
  $("#news-count").textContent = `${data.article_count}개 기사 · ${data.issue_count}개 이슈`;
  $("#news-overview").textContent = data.overview;
  $("#news-updated").textContent = `${new Date(data.generated_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })} 수집`;
  renderCoreIssues(data);
  const keywords = $("#news-keywords");
  keywords.replaceChildren(...data.key_topics.map((topic) => {
    const span = document.createElement("span");
    span.textContent = `# ${topic}`;
    return span;
  }));
  const list = $("#news-list");
  list.replaceChildren();
  if (!data.issues.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "표시할 최근 기사가 없습니다.";
    list.append(empty);
  } else {
    data.issues.forEach((issue) => list.append(issueElement(issue)));
  }
  $("#news-message").textContent = `${data.source_name}의 기사 제목을 유사한 이슈별로 묶었습니다.`;
}

async function loadNews(query, { hot = false } = {}) {
  const normalized = query.trim();
  if (!hot && !normalized) {
    $("#news-query").setCustomValidity("검색어를 입력해 주세요.");
    $("#news-query").reportValidity();
    return;
  }
  $("#news-query").value = hot ? "" : normalized;
  $("#news-message").textContent = "최근 기사를 찾고 있습니다.";
  $("#news-list").innerHTML = '<div class="empty">뉴스를 불러오는 중입니다.</div>';
  const url = new URL(window.location.href);
  if (hot) url.searchParams.delete("q");
  else url.searchParams.set("q", normalized);
  window.history.replaceState(null, "", url);
  try {
    const endpoint = hot
      ? "/research/news?limit=15"
      : `/research/news?q=${encodeURIComponent(normalized)}&limit=15`;
    renderDigest(await api(endpoint));
  } catch (error) {
    $("#news-message").textContent = error.message;
    $("#news-list").innerHTML = '<div class="empty"></div>';
    $("#news-list .empty").textContent = error.message;
  }
}

$("#news-search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadNews($("#news-query").value);
});
$("#news-query").addEventListener("input", () => {
  $("#news-query").setCustomValidity("");
});
document.querySelectorAll("[data-news-query]").forEach((button) => {
  button.addEventListener("click", () => loadNews(button.dataset.newsQuery));
});
document.querySelector("[data-news-mode='hot']").addEventListener("click", () => loadNews("", { hot: true }));
$("#logout-button").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST" }); } finally { window.location.replace("/login"); }
});

async function initialize() {
  try {
    const session = await api("/auth/me");
    csrfToken = session.csrf_token;
    $("#current-user").textContent = session.username;
    const initialQuery = new URLSearchParams(window.location.search).get("q");
    await loadNews(initialQuery || "", { hot: !initialQuery });
  } catch (error) {
    $("#news-message").textContent = error.message;
  }
}

initialize();
