async function fetchJSON(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function escapeHtml(text = "") {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadCategories() {
  const grid = document.querySelector("[data-category-grid]");
  if (!grid) return;

  let categories;
  try {
    categories = await fetchJSON("/api/frontend1/categories");
  } catch (error) {
    console.warn("Category API unavailable; keeping static category cards.", error);
    return;
  }

  grid.innerHTML = categories.map((category) => {
    const isFavorites = category.category_id === "favorites";
    const tagName = isFavorites ? "a" : "article";
    const href = isFavorites ? ` href="${category.href}"` : "";
    const aria = isFavorites ? `进入${escapeHtml(category.name)}页面` : `${escapeHtml(category.name)}分类暂不可进入`;
    return `
      <${tagName} class="category-card${isFavorites ? "" : " is-static"}"${href} aria-label="${aria}">
        <img src="${category.cover}" alt="${escapeHtml(category.name)}分类背景图" />
        <h2 class="card-title">${escapeHtml(category.name)}</h2>
      </${tagName}>
    `;
  }).join("");

  grid.insertAdjacentHTML("beforeend", `
    <article class="category-card create-card" aria-label="新建分类">
      <span class="create-plus" aria-hidden="true">+</span>
    </article>
  `);
}

async function loadCategoryBooks() {
  const grid = document.querySelector("[data-book-grid]");
  if (!grid) return;

  const params = new URLSearchParams(window.location.search);
  const categoryId = params.get("category") || "favorites";

  let payload;
  try {
    payload = await fetchJSON(`/api/frontend1/categories/${categoryId}/books`);
  } catch (error) {
    console.warn("Book category API unavailable; keeping static book cards.", error);
    return;
  }

  const title = document.querySelector("[data-category-title]");
  if (title) title.textContent = payload.title || "书籍";

  grid.innerHTML = payload.books.map((book) => `
    <a class="book-card" href="${book.href || "aPig.html"}" data-book-id="${escapeHtml(book.book_id)}" aria-label="${escapeHtml(book.title)}">
      <img class="book-cover" src="${book.cover}" alt="${escapeHtml(book.title)}书籍封面" />
      ${book.badge ? `
        <span class="book-badge" aria-hidden="true">
          <img src="${book.badge}" alt="" />
          ${book.has_notification ? `<img class="book-badge-notification" src="bookBadgeNotification.png" alt="" />` : ""}
        </span>
      ` : ""}
    </a>
  `).join("");

  grid.insertAdjacentHTML("beforeend", `
    <button class="book-card new-book-card" type="button" aria-label="新建书籍"></button>
  `);
}

loadCategories().catch(console.error);
loadCategoryBooks().catch(console.error);
