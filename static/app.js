const STATUSES = [
  ["saved", "Saved"],
  ["applied", "Applied"],
  ["interview", "Interview"],
  ["offer", "Offer"],
  ["rejected", "Rejected"],
];

const board = document.getElementById("board");
const filters = document.getElementById("filters");
const count = document.getElementById("count");
const dialog = document.getElementById("listing-dialog");
const form = document.getElementById("listing-form");
const kicker = document.getElementById("dialog-kicker");
const dialogTitle = document.getElementById("dialog-title");
const urlInput = document.getElementById("job-url");
const titleInput = document.getElementById("title");
const companyInput = document.getElementById("company");
const locationInput = document.getElementById("location");
const statusInput = document.getElementById("status");
const notesInput = document.getElementById("notes");
const lookupBtn = document.getElementById("lookup-btn");
const lookupMsg = document.getElementById("lookup-msg");
const deleteBtn = document.getElementById("delete-btn");
const saveBtn = document.getElementById("save-btn");

let jobs = [];
let filter = "all";
let editingId = null;

fillStatusSelect(statusInput);

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-action='new']")) openCreate();
});

document.getElementById("close-dialog").addEventListener("click", () => dialog.close());
document.getElementById("cancel-btn").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

lookupBtn.addEventListener("click", () => lookup());
urlInput.addEventListener("paste", () => {
  setTimeout(() => lookup(), 0);
});
urlInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    lookup();
  }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  saveListing();
});

deleteBtn.addEventListener("click", deleteListing);

board.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-action='open-edit']");
  if (opener) openEdit(opener.dataset.id);
});

board.addEventListener("change", (event) => {
  const select = event.target.closest("[data-action='status']");
  if (select) updateStatus(select.dataset.id, select.value);
});

filters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  filter = button.dataset.filter;
  render();
});

refresh().catch(() => {
  board.replaceChildren();
  const message = document.createElement("p");
  message.className = "banner";
  message.textContent = "Couldn't load listings. Start the tracker with python server.py and refresh this page.";
  board.append(message);
});

async function refresh() {
  const response = await fetch("/api/jobs");
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Load failed");
  jobs = data;
  render();
}

function render() {
  renderFilters();
  const visible = jobs.filter((job) => filter === "all" || job.status === filter);
  const noun = visible.length === 1 ? "listing" : "listings";
  const label = filter === "all" ? noun : `${statusName(filter).toLowerCase()} ${noun}`;
  count.textContent = visible.length ? `${visible.length} ${label}` : `No ${label}`;

  board.className = "board";
  board.replaceChildren();
  if (!visible.length) {
    board.append(emptyState());
    return;
  }
  for (const job of visible) board.append(jobRow(job));
}

function renderFilters() {
  filters.replaceChildren();
  const options = [["all", "All"], ...STATUSES];
  for (const [value, label] of options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "filter" + (filter === value ? " active" : "");
    button.dataset.filter = value;
    const total = value === "all" ? jobs.length : jobs.filter((job) => job.status === value).length;
    button.textContent = `${label} ${total}`;
    filters.append(button);
  }
}

function emptyState() {
  const box = document.createElement("div");
  box.className = "empty";
  const heading = document.createElement("h2");
  heading.textContent = filter === "all" ? "No listings yet" : `No ${statusName(filter).toLowerCase()} listings`;
  const copy = document.createElement("p");
  copy.textContent = filter === "all"
    ? "Add a listing and paste an Indeed link. A search link works when it includes the selected job as vjk."
    : "Listings with this status will show up here.";
  box.append(heading, copy);
  if (filter === "all") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "primary";
    button.dataset.action = "new";
    button.textContent = "New listing";
    box.append(button);
  }
  return box;
}

function jobRow(job) {
  const row = document.createElement("article");
  row.className = "job";

  const main = document.createElement("button");
  main.type = "button";
  main.className = "job-main";
  main.dataset.action = "open-edit";
  main.dataset.id = job.id;

  const pip = document.createElement("span");
  pip.className = `pip ${job.status}`;

  const text = document.createElement("span");
  text.className = "job-copy";
  const title = document.createElement("strong");
  title.textContent = job.title || "Untitled role";
  const meta = document.createElement("span");
  meta.className = "meta";
  meta.textContent = [job.company, job.location].filter(Boolean).join(" · ") || "Company not set";
  text.append(title, meta);
  if (job.notes) {
    const notes = document.createElement("span");
    notes.className = "notes";
    notes.textContent = job.notes;
    text.append(notes);
  }
  main.append(pip, text);

  const select = document.createElement("select");
  select.className = "status-select";
  select.dataset.action = "status";
  select.dataset.id = job.id;
  select.setAttribute("aria-label", `Status for ${job.title}`);
  fillStatusSelect(select);
  select.value = STATUSES.some(([value]) => value === job.status) ? job.status : "saved";

  const link = document.createElement("a");
  link.className = "open-link";
  link.textContent = "Open";
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  const href = safeHref(job.job_url || job.linkedin_url);
  if (href) link.href = href;
  else link.hidden = true;

  const when = document.createElement("time");
  when.className = "when";
  when.dateTime = job.created_at || "";
  when.textContent = formatDate(job.created_at);
  when.title = job.created_at ? `Added ${formatDate(job.created_at, true)}` : "";

  row.append(main, select, link, when);
  return row;
}

function openCreate() {
  editingId = null;
  form.reset();
  statusInput.value = "saved";
  kicker.textContent = "New listing";
  dialogTitle.textContent = "Paste an Indeed link";
  saveBtn.textContent = "Add listing";
  deleteBtn.hidden = true;
  setMsg("Copy the address from Indeed. A search link works when it includes vjk.");
  resetLookup();
  dialog.showModal();
  urlInput.focus();
}

function openEdit(id) {
  const job = jobs.find((item) => item.id === id);
  if (!job) return;
  editingId = id;
  form.reset();
  urlInput.value = job.job_url || job.linkedin_url || "";
  titleInput.value = job.title || "";
  companyInput.value = job.company || "";
  locationInput.value = job.location || "";
  statusInput.value = job.status || "saved";
  notesInput.value = job.notes || "";
  kicker.textContent = "Edit listing";
  dialogTitle.textContent = job.title || "Edit listing";
  saveBtn.textContent = "Save changes";
  deleteBtn.hidden = false;
  setMsg("");
  resetLookup();
  dialog.showModal();
  titleInput.focus();
}

async function lookup() {
  const url = normalizeUrl(urlInput.value);
  urlInput.value = url;
  const site = jobSite(url);
  if (!site) {
    setMsg("Paste an Indeed job link.", "error");
    return;
  }
  lookupBtn.disabled = true;
  lookupBtn.textContent = "Looking up…";
  setMsg(site === "indeed" ? "Reading the Indeed job…" : "Reading the LinkedIn page…");
  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok) {
      setMsg(data.error || "Couldn't read that link.", "error");
      return;
    }
    if (data.job_url) urlInput.value = data.job_url;
    if (data.title) titleInput.value = data.title;
    if (data.company) companyInput.value = data.company;
    if (data.location) locationInput.value = data.location;
    const duplicate = jobs.find((job) => (job.job_url || job.linkedin_url) === urlInput.value && job.id !== editingId);
    let message = data.message || "";
    let tone = data.title ? "ok" : "warn";
    if (duplicate) {
      message = `You already saved “${duplicate.title}”. ${message}`;
      tone = "warn";
    }
    setMsg(message, tone);
    if (!data.title) titleInput.focus();
  } catch {
    setMsg("Couldn't reach the job page. Enter the details yourself.", "error");
  } finally {
    resetLookup();
  }
}

async function saveListing() {
  const payload = {
    job_url: normalizeUrl(urlInput.value),
    title: titleInput.value.trim(),
    company: companyInput.value.trim(),
    location: locationInput.value.trim(),
    status: statusInput.value,
    notes: notesInput.value.trim(),
  };
  urlInput.value = payload.job_url;
  if (!jobSite(payload.job_url)) {
    setMsg("Paste an Indeed job link.", "error");
    urlInput.focus();
    return;
  }
  if (!payload.title) {
    setMsg("Add a job title.", "error");
    titleInput.focus();
    return;
  }

  saveBtn.disabled = true;
  try {
    const response = await fetch(editingId ? `/api/jobs/${editingId}` : "/api/jobs", {
      method: editingId ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      setMsg(data.error || "Couldn't save the listing.", "error");
      return;
    }
    dialog.close();
    await refresh();
  } catch {
    setMsg("Couldn't save. Is the Python server still running?", "error");
  } finally {
    saveBtn.disabled = false;
  }
}

async function updateStatus(id, status) {
  const job = jobs.find((item) => item.id === id);
  if (!job || job.status === status) return;
  const previous = job.status;
  job.status = status;
  render();
  try {
    const response = await fetch(`/api/jobs/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (!response.ok) throw new Error("status failed");
    await refresh();
  } catch {
    job.status = previous;
    render();
  }
}

async function deleteListing() {
  if (!editingId) return;
  const job = jobs.find((item) => item.id === editingId);
  const name = job ? job.title : "this listing";
  if (!confirm(`Delete ${name}?`)) return;
  deleteBtn.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${editingId}`, { method: "DELETE" });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      setMsg(data.error || "Couldn't delete the listing.", "error");
      return;
    }
    dialog.close();
    await refresh();
  } catch {
    setMsg("Couldn't delete. Is the Python server still running?", "error");
  } finally {
    deleteBtn.disabled = false;
  }
}

function fillStatusSelect(select) {
  select.replaceChildren();
  for (const [value, label] of STATUSES) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    select.append(option);
  }
}

function statusName(value) {
  const found = STATUSES.find(([id]) => id === value);
  return found ? found[1] : value;
}

function normalizeUrl(value) {
  let url = value.trim();
  if (!url) return "";
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  return url;
}

function jobSite(url) {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.toLowerCase();
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
    if (host === "indeed.com" || host.endsWith(".indeed.com")) return "indeed";
    if (
      host === "linkedin.com" ||
      host.endsWith(".linkedin.com") ||
      host === "lnkd.in" ||
      host.endsWith(".lnkd.in")
    ) {
      return "linkedin";
    }
  } catch {
    return "";
  }
  return "";
}

function safeHref(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch {
    return "";
  }
  return "";
}

function formatDate(iso, withYear) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: withYear ? "numeric" : undefined,
  });
}

function setMsg(text, tone) {
  lookupMsg.textContent = text || "";
  lookupMsg.className = "hint" + (tone ? ` ${tone}` : "");
}

function resetLookup() {
  lookupBtn.disabled = false;
  lookupBtn.textContent = "Look up";
}
