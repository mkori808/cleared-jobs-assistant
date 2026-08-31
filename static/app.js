const PAGE_SIZE = 100;

const state = {
  jobs: [], companies: [], search: "", companyFilter: "", clearanceOnly: false, clearanceLevel: "",
  cityFilter: [], stateFilter: [], countryFilter: [], remoteOnly: false, includeRemote: false,
  salaryMin: null, salaryMax: null, gradeFilter: [], equityOnly: false,
  selectedJobIds: new Set(), appliedJobs: [], currentView: "main",
  sortColumn: "first_seen", sortDirection: "desc", currentPage: 1,
};

const STAMP_CLASS = {
  "TS/SCI + Polygraph": "stamp-tssci",
  "TS/SCI": "stamp-tssci",
  "Top Secret": "stamp-topsecret",
  "Q Clearance": "stamp-q",
  "Secret": "stamp-secret",
  "L Clearance": "stamp-l",
  "Public Trust": "stamp-publictrust",
  "None mentioned": "stamp-none",
};

function stampClass(level) {
  return STAMP_CLASS[level] || "stamp-other";
}

const MATCH_CLASS = { A: "match-a", B: "match-b", C: "match-c", D: "match-d", F: "match-f" };

function renderMatch(job) {
  if (!job.resume_match_grade) return "—";
  const cls = MATCH_CLASS[job.resume_match_grade] || "match-c";
  return `<span class="match-grade ${cls}" onclick="showMatchRationale('${job.id}')">${job.resume_match_grade}</span>`;
}

function showMatchRationale(jobId) {
  const job = state.jobs.find(j => j.id === jobId);
  if (!job || !job.resume_match_grade) return;

  const badge = document.getElementById("match-rationale-grade");
  badge.textContent = job.resume_match_grade;
  badge.className = `match-grade ${MATCH_CLASS[job.resume_match_grade] || "match-c"}`;
  document.getElementById("match-rationale-title").textContent = job.title;
  document.getElementById("match-rationale-company").textContent = job.company;
  document.getElementById("match-rationale-text").textContent =
    job.resume_match_rationale || "No rationale was recorded for this grade.";
  document.getElementById("match-rationale-overlay").hidden = false;
}

function hideMatchRationale() {
  document.getElementById("match-rationale-overlay").hidden = true;
}

function formatSalary(job) {
  if (job.salary_min == null && job.salary_max == null) return "—";
  const fmt = n => n >= 1000 ? `${Math.round(n / 1000)}K` : Math.round(n);
  const unit = job.salary_interval === "hour" ? "/hr" : "/yr";
  const currency = job.salary_currency || "USD";
  const symbol = currency === "USD" ? "$" : currency + " ";
  if (job.salary_min && job.salary_max && job.salary_min !== job.salary_max) {
    return `${symbol}${fmt(job.salary_min)}\u2013${fmt(job.salary_max)}${unit}`;
  }
  const single = job.salary_max || job.salary_min;
  return `${symbol}${fmt(single)}${unit}`;
}

function formatCity(job) {
  if (job.city) return job.city + (job.remote ? " (Remote)" : "");
  if (job.remote) return "Remote";
  return job.location || "—";
}

function timeAgo(unixSeconds) {
  if (!unixSeconds) return "—";
  const diffMs = Date.now() - unixSeconds * 1000;
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days === 0) return "today";
  if (days === 1) return "1 day ago";
  return `${days} days ago`;
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function loadCompanies() {
  const companies = await fetchJSON("/api/companies");
  state.companies = companies;

  const select = document.getElementById("company-filter");
  select.innerHTML = '<option value="">All companies</option>' +
    companies.map(c => `<option value="${c.name}">${c.name}</option>`).join("");

  document.getElementById("company-count").textContent = companies.length;

  const unresolved = companies.filter(c => c.status === "unresolved" || (c.ats === null && c.status !== "resolved"));
  const panel = document.getElementById("unresolved-panel");
  const list = document.getElementById("unresolved-list");

  if (unresolved.length) {
    panel.hidden = false;
    list.innerHTML =
      unresolved.map(c => `<span class="chip">${c.name}</span>`).join("");
  } else {
    panel.hidden = false;
    list.innerHTML = '<p style="color: var(--text-dim); font-size: 13px; margin: 0;">✓ All companies resolved</p>';
  }
}

// Candidate names come straight from a third-party API and routinely contain "&"
// ("BURNS & MCDONNELL ENGINEERING COMPANY"). They're interpolated into both text and a
// data- attribute, so escape quotes as well as angle brackets.
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function formatAward(amount) {
  if (!amount) return "";
  if (amount >= 1e9) return `$${(amount / 1e9).toFixed(1)}B`;
  if (amount >= 1e6) return `$${Math.round(amount / 1e6)}M`;
  return `$${Math.round(amount / 1e3)}K`;
}

async function loadCandidateCompanies() {
  const candidates = await fetchJSON("/api/candidate-companies");
  const list = document.getElementById("candidates-list");
  if (!candidates.length) {
    list.innerHTML = '<p style="color: var(--text-dim); font-size: 13px; margin: 0;">' +
      'No pending suggestions — run "Find new companies" to check federal award data.</p>';
    return;
  }
  list.innerHTML = candidates.map(c => `
    <span class="chip candidate-chip">
      <span class="candidate-name">${escapeHtml(c.name)}</span>
      <span class="candidate-award">${formatAward(c.award_amount)}</span>
      <button class="candidate-add" data-name="${escapeHtml(c.name)}" title="Add to companies.json">+</button>
      <button class="candidate-dismiss" data-name="${escapeHtml(c.name)}" title="Dismiss">&times;</button>
    </span>`).join("");

  list.querySelectorAll(".candidate-add").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch("/api/candidate-companies/add", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: btn.dataset.name }),
      });
      // Reload both: the company list grew, and this candidate drops off the suggestions.
      await Promise.all([loadCandidateCompanies(), loadCompanies()]);
    });
  });
  list.querySelectorAll(".candidate-dismiss").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch("/api/candidate-companies/dismiss", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: btn.dataset.name }),
      });
      await loadCandidateCompanies();
    });
  });
}

async function runDiscoverCompanies() {
  const statusLine = document.getElementById("discover-status-line");
  const btn = document.getElementById("discover-companies-btn");
  btn.disabled = true;
  statusLine.textContent = "querying federal award data…";

  const res = await fetch("/api/discover-companies", { method: "POST" });
  if (!res.ok) {
    statusLine.textContent = res.status === 409 ? "already running" : "failed to start";
    btn.disabled = false;
    return;
  }
  const poll = setInterval(async () => {
    const s = await fetchJSON("/api/discover-companies/status");
    if (s.running) return;
    clearInterval(poll);
    btn.disabled = false;
    statusLine.textContent = s.error
      ? `failed: ${s.error}`
      : `found ${s.candidates} untracked ${s.candidates === 1 ? "employer" : "employers"}`;
    await loadCandidateCompanies();
  }, 2000);
}

// Shared by loadJobs() and loadLocations(): both need the exact same set of active filters --
// /api/locations uses them to compute each location dropdown's options under the *other*
// currently-active filters (see db.get_locations), so if these two ever drifted apart the
// dropdowns would narrow inconsistently with what the job list itself actually shows.
function buildFilterParams() {
  const params = new URLSearchParams();
  if (state.companyFilter) params.set("company", state.companyFilter);
  if (state.clearanceLevel) {
    params.set("clearance_level", state.clearanceLevel);
  } else if (state.clearanceOnly) {
    params.set("clearance_only", "true");
  }
  if (state.remoteOnly) params.set("remote_only", "true");
  if (state.includeRemote) params.set("include_remote", "true");
  if (state.equityOnly) params.set("equity_only", "true");
  if (state.salaryMin != null) params.set("salary_min", state.salaryMin);
  if (state.salaryMax != null) params.set("salary_max", state.salaryMax);
  for (const g of state.gradeFilter) params.append("grades", g);
  for (const c of state.cityFilter) params.append("cities", c);
  for (const s of state.stateFilter) params.append("states", s);
  for (const c of state.countryFilter) params.append("countries", c);
  return params;
}

async function loadLocations() {
  const params = buildFilterParams();
  const { cities, states, countries, remote_count } = await fetchJSON(`/api/locations?${params.toString()}`);
  populateLocationDropdowns(cities, states, countries);

  const remoteLabel = document.getElementById("remote-only-check").closest("label");
  remoteLabel.lastChild.textContent = remote_count > 0 ? ` Remote only (${remote_count})` : " Remote only";
}

let loadJobsSeq = 0;

async function loadJobs() {
  const mySeq = ++loadJobsSeq;
  const params = buildFilterParams();
  params.set("active_only", "true");
  if (state.sortColumn) params.set("sort_by", state.sortColumn);
  if (state.sortDirection) params.set("sort_order", state.sortDirection);

  const jobs = await fetchJSON(`/api/jobs?${params.toString()}`);
  if (mySeq !== loadJobsSeq) return; // a newer loadJobs() call superseded this one -- discard
  state.jobs = jobs;
  state.currentPage = 1;
  renderJobs();
  renderSummary();
  await loadLocations(); // filters just changed -- re-narrow the location dropdowns to match
}

async function loadStatus() {
  const { last_refresh } = await fetchJSON("/api/status");
  const line = document.getElementById("status-line");
  if (!last_refresh) {
    line.textContent = "no refresh has run yet";
    return null;
  }
  const finished = new Date(last_refresh.finished_at * 1000);
  line.textContent = `last refresh: ${finished.toLocaleString()} · ${last_refresh.jobs_found} jobs across ${last_refresh.companies_checked} companies`;
  return last_refresh;
}

async function pollUntilJobsAppear() {
  // The background refresh can still be running when the page first loads (e.g. right
  // after the DB was emptied), so the very first fetch can legitimately come back with
  // nothing yet. Keep re-fetching until jobs show up or a full refresh cycle finishes,
  // instead of leaving the table stuck empty until the user happens to touch a filter.
  for (let i = 0; i < 90 && state.jobs.length === 0; i++) {
    await new Promise(r => setTimeout(r, 4000));
    await Promise.all([loadJobs(), loadCompanies()]); // loadJobs() re-narrows locations itself
    const lastRefresh = await loadStatus();
    if (lastRefresh) break; // full cycle done -- whatever's in state.jobs now is the final answer
  }
}

function renderSummary() {
  const jobs = state.jobs;
  const withClearance = jobs.filter(j => j.clearance_level !== "None mentioned").length;
  const tsSci = jobs.filter(j => j.clearance_level && j.clearance_level.includes("TS/SCI")).length;
  const poly = jobs.filter(j => j.polygraph_mentioned).length;
  const withSalary = jobs.filter(j => j.salary_min != null || j.salary_max != null).length;
  const withEquity = jobs.filter(j => j.equity_mentioned).length;

  document.getElementById("summary-strip").innerHTML = `
    <div class="stat-chip"><span class="num">${jobs.length}</span><span class="label">Total roles</span></div>
    <div class="stat-chip"><span class="num">${withClearance}</span><span class="label">Clearance mentioned</span></div>
    <div class="stat-chip"><span class="num">${tsSci}</span><span class="label">TS/SCI</span></div>
    <div class="stat-chip"><span class="num">${poly}</span><span class="label">Polygraph mentioned</span></div>
    <div class="stat-chip"><span class="num">${withSalary}</span><span class="label">Salary listed</span></div>
    <div class="stat-chip"><span class="num">${withEquity}</span><span class="label">Equity mentioned</span></div>
  `;
}

function getVisibleJobs() {
  // state.jobs is already filtered server-side by company/clearance/location/active; this
  // applies the one filter that's client-only (free-text search) on top of that.
  const q = state.search.trim().toLowerCase();
  let filtered = !q ? state.jobs : state.jobs.filter(j => j.title.toLowerCase().includes(q) || j.company.toLowerCase().includes(q));
  return sortJobs(filtered);
}

function sortJobs(jobs) {
  if (!state.sortColumn) return jobs;

  const sorted = [...jobs].sort((a, b) => {
    let aVal = a[state.sortColumn];
    let bVal = b[state.sortColumn];

    // Blank cells always sort last, regardless of sort direction -- so ascending and
    // descending only ever reorder the rows that actually have a value.
    if (aVal == null && bVal == null) return 0;
    if (aVal == null) return 1;
    if (bVal == null) return -1;

    // Numeric comparison
    if (typeof aVal === "number" && typeof bVal === "number") {
      return state.sortDirection === "asc" ? aVal - bVal : bVal - aVal;
    }

    // String comparison
    aVal = String(aVal).toLowerCase();
    bVal = String(bVal).toLowerCase();
    if (state.sortDirection === "asc") {
      return aVal.localeCompare(bVal);
    } else {
      return bVal.localeCompare(aVal);
    }
  });

  return sorted;
}

function updateSortIndicators() {
  document.querySelectorAll("thead th.sortable").forEach(th => {
    th.classList.remove("asc", "desc");
    if (th.dataset.sort === state.sortColumn) {
      th.classList.add(state.sortDirection);
    }
  });
}

function renderJobs() {
  const tbody = document.getElementById("jobs-body");
  const filtered = getVisibleJobs();

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty-state">No matching roles. Try widening your filters, or run a refresh.</td></tr>`;
    updateSortIndicators();
    renderPagination(0);
    return;
  }

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  state.currentPage = Math.min(Math.max(1, state.currentPage), totalPages);
  const start = (state.currentPage - 1) * PAGE_SIZE;
  const pageJobs = filtered.slice(start, start + PAGE_SIZE);

  tbody.innerHTML = pageJobs.map(j => `
    <tr>
      <td style="width: 30px;"><input type="checkbox" class="job-checkbox" data-job-id="${j.id}" ${state.selectedJobIds.has(j.id) ? 'checked' : ''}></td>
      <td class="company-cell">${j.company}</td>
      <td><a href="${j.url || '#'}" target="_blank" rel="noopener">${j.title}</a></td>
      <td>${formatCity(j)}</td>
      <td>${j.state || "—"}</td>
      <td>${j.country || "—"}</td>
      <td class="salary-cell">${formatSalary(j)}</td>
      <td>${j.equity_mentioned ? '<span class="signal-badge">equity</span>' : "—"}</td>
      <td><span class="clearance-stamp ${stampClass(j.clearance_level)}">${j.clearance_level}</span></td>
      <td>
        ${j.citizenship_required ? '<span class="signal-badge">US citizenship</span>' : ""}
        ${j.polygraph_mentioned ? '<span class="signal-badge">polygraph</span>' : ""}
      </td>
      <td>${renderMatch(j)}</td>
      <td class="first-seen">${timeAgo(j.first_seen)}</td>
    </tr>
  `).join("");

  updateSortIndicators();
  renderPagination(filtered.length);
  document.querySelectorAll(".job-checkbox").forEach(cb => {
    cb.addEventListener("change", e => {
      if (e.target.checked) {
        state.selectedJobIds.add(e.target.dataset.jobId);
      } else {
        state.selectedJobIds.delete(e.target.dataset.jobId);
      }
      updateSelectionUI();
    });
  });
}

function renderPagination(totalCount) {
  const bar = document.getElementById("pagination-bar");
  const info = document.getElementById("pagination-info");
  const pageLabel = document.getElementById("pagination-page");
  const prevBtn = document.getElementById("pagination-prev");
  const nextBtn = document.getElementById("pagination-next");

  if (!totalCount) {
    bar.hidden = true;
    return;
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const start = (state.currentPage - 1) * PAGE_SIZE + 1;
  const end = Math.min(state.currentPage * PAGE_SIZE, totalCount);

  bar.hidden = false;
  info.textContent = `Showing ${start}–${end} of ${totalCount}`;
  pageLabel.textContent = `Page ${state.currentPage} of ${totalPages}`;
  prevBtn.disabled = state.currentPage <= 1;
  nextBtn.disabled = state.currentPage >= totalPages;
}

function updateSelectionUI() {
  const count = state.selectedJobIds.size;
  const countEl = document.getElementById("selection-count");
  const markBtn = document.getElementById("mark-applied-btn");
  const selectAllCb = document.getElementById("select-all-check");
  const tableSelectAllCb = document.getElementById("table-select-all-check");

  countEl.textContent = count > 0 ? `${count} selected` : "";
  markBtn.disabled = count === 0;

  const visible = getVisibleJobs().length;
  const allVisible = visible > 0 && state.selectedJobIds.size === visible;
  selectAllCb.checked = allVisible;
  tableSelectAllCb.checked = allVisible;
}

async function loadAppliedJobs() {
  const jobs = await fetchJSON("/api/applied-jobs");
  state.appliedJobs = jobs;
  renderAppliedJobs();
}

function renderAppliedJobs() {
  const tbody = document.getElementById("applied-body");
  if (!state.appliedJobs.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-state">No applied jobs yet. Select jobs and mark them as applied.</td></tr>`;
    return;
  }

  tbody.innerHTML = state.appliedJobs.map(j => `
    <tr>
      <td class="company-cell">${j.company}</td>
      <td><a href="${j.url || '#'}" target="_blank" rel="noopener">${j.title}</a></td>
      <td>${formatCity(j)}</td>
      <td>${j.state || "—"}</td>
      <td>${j.country || "—"}</td>
      <td class="salary-cell">${formatSalary(j)}</td>
      <td><span class="clearance-stamp ${stampClass(j.clearance_level)}">${j.clearance_level}</span></td>
      <td>
        <select class="status-select" data-job-id="${j.id}" onchange="updateApplicationStatus('${j.id}', this.value)">
          <option value="applied" ${j.application_status === 'applied' ? 'selected' : ''}>Applied</option>
          <option value="interview" ${j.application_status === 'interview' ? 'selected' : ''}>Interview</option>
          <option value="offer" ${j.application_status === 'offer' ? 'selected' : ''}>Offer</option>
          <option value="rejected" ${j.application_status === 'rejected' ? 'selected' : ''}>Rejected</option>
          <option value="withdrawn" ${j.application_status === 'withdrawn' ? 'selected' : ''}>Withdrawn</option>
        </select>
      </td>
      <td><input type="text" class="notes-input" data-job-id="${j.id}" value="${j.application_notes || ''}" onchange="updateApplicationNotes('${j.id}', this.value)" placeholder="Notes…"></td>
      <td class="first-seen">${timeAgo(j.applied_at)}</td>
    </tr>
  `).join("");
}

async function markSelectedAsApplied() {
  const job_ids = Array.from(state.selectedJobIds);
  if (!job_ids.length) return;

  await fetch("/api/mark-applied", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids, status: "applied" }),
  });

  state.selectedJobIds.clear();
  updateSelectionUI();
  await Promise.all([loadJobs(), loadAppliedJobs()]);
}

async function updateApplicationStatus(job_id, status) {
  await fetch("/api/update-application-status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id, status }),
  });
  await loadAppliedJobs();
}

async function updateApplicationNotes(job_id, notes) {
  await fetch("/api/update-application-status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id, status: (state.appliedJobs.find(j => j.id === job_id) || {}).application_status || "applied", notes }),
  });
}

function initLocationDropdowns() {
  // Handle dropdown toggle
  document.querySelectorAll(".multiselect-toggle").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const dropdown = btn.nextElementSibling;
      const isOpen = dropdown.classList.contains("open");
      document.querySelectorAll(".multiselect-dropdown").forEach(d => d.classList.remove("open"));
      if (!isOpen) {
        dropdown.classList.add("open");
        dropdown.querySelector(".multiselect-search-input").focus();
      }
    });
  });

  // Handle search in dropdowns
  document.querySelectorAll(".multiselect-search-input").forEach(input => {
    input.addEventListener("input", (e) => {
      const query = e.target.value.toLowerCase();
      const optionsDiv = e.target.closest(".multiselect-dropdown").querySelector(".multiselect-options");
      optionsDiv.querySelectorAll(".multiselect-option").forEach(opt => {
        const text = opt.textContent.toLowerCase();
        opt.classList.toggle("hidden", !text.includes(query));
      });
    });
  });

  // Close dropdowns when clicking outside
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".custom-multiselect")) {
      document.querySelectorAll(".multiselect-dropdown").forEach(d => d.classList.remove("open"));
    }
  });
}

// facets: [{value, count}], narrowed server-side by every *other* active filter (see
// db.get_locations) -- e.g. selecting State=CA narrows the city facets to CA cities only.
// selected: the filter's own currently-checked values (state.cityFilter etc).
function createFacetOptions(facets, filterId, selected) {
  const optionsDiv = document.getElementById(`${filterId}-options`);
  const selectedSet = new Set(selected);

  // Narrowing can drop a value that's still actively selected (e.g. picking State=CA while
  // City=Reston was already checked) -- keep it in the list instead of silently leaving an
  // invisible filter applied that the user can no longer see or uncheck (and that would zero
  // out results with no visible explanation, since city/state/country are AND'd together).
  const facetValues = new Set(facets.map(f => f.value));
  const merged = [...facets, ...selected.filter(v => !facetValues.has(v)).map(v => ({ value: v, count: null }))];

  const selectEl = document.getElementById(`${filterId}-filter`);
  selectEl.innerHTML = merged.map(f => `<option value="${escapeHtml(f.value)}">${escapeHtml(f.value)}</option>`).join("");
  [...selectEl.options].forEach(opt => { opt.selected = selectedSet.has(opt.value); });

  optionsDiv.innerHTML = merged.map(f => `
    <label class="multiselect-option">
      <input type="checkbox" data-value="${escapeHtml(f.value)}" ${selectedSet.has(f.value) ? "checked" : ""}>
      <span>${escapeHtml(f.value)}${f.count != null ? ` (${f.count})` : ""}</span>
    </label>
  `).join("");

  const toggle = document.querySelector(`.multiselect-toggle[data-filter="${filterId}"]`);
  toggle.querySelector(".multiselect-count").textContent = selected.length > 0 ? selected.length : "";

  optionsDiv.querySelectorAll("input[type='checkbox']").forEach(cb => {
    cb.addEventListener("change", () => {
      const nowSelected = [...optionsDiv.querySelectorAll("input[type='checkbox']:checked")].map(c => c.dataset.value);
      [...selectEl.options].forEach(opt => { opt.selected = nowSelected.includes(opt.value); });
      toggle.querySelector(".multiselect-count").textContent = nowSelected.length > 0 ? nowSelected.length : "";
      selectEl.dispatchEvent(new Event("change")); // loadJobs() picks this up via the existing change listener
    });
  });
}

function populateLocationDropdowns(cities, states, countries) {
  createFacetOptions(cities || [], "city", state.cityFilter);
  createFacetOptions(states || [], "state", state.stateFilter);
  createFacetOptions(countries || [], "country", state.countryFilter);
}

function populateGradeDropdown() {
  const optionsDiv = document.getElementById("grade-options");
  optionsDiv.innerHTML = ["A", "B", "C", "D", "F"].map(g => `
    <label class="multiselect-option">
      <input type="checkbox" data-value="${g}">
      <span>${g}</span>
    </label>
  `).join("");

  optionsDiv.querySelectorAll("input[type='checkbox']").forEach(cb => {
    cb.addEventListener("change", () => {
      const selected = [...optionsDiv.querySelectorAll("input[type='checkbox']:checked")].map(c => c.dataset.value);

      const selectEl = document.getElementById("grade-filter");
      [...selectEl.options].forEach(opt => opt.selected = false);
      selected.forEach(val => {
        const opt = [...selectEl.options].find(o => o.value === val);
        if (opt) opt.selected = true;
      });

      const toggle = document.querySelector('.multiselect-toggle[data-filter="grade"]');
      const count = toggle.querySelector(".multiselect-count");
      count.textContent = selected.length > 0 ? selected.length : "";

      selectEl.dispatchEvent(new Event("change"));
    });
  });
}

function showMainView() {
  state.currentView = "main";
  document.getElementById("main-view").hidden = false;
  document.getElementById("applied-view").hidden = true;
}

function showAppliedView() {
  state.currentView = "applied";
  document.getElementById("main-view").hidden = true;
  document.getElementById("applied-view").hidden = false;
  loadAppliedJobs();
}

function wireControls() {
  initLocationDropdowns();

  document.getElementById("match-rationale-close").addEventListener("click", hideMatchRationale);
  document.getElementById("match-rationale-overlay").addEventListener("click", e => {
    if (e.target.id === "match-rationale-overlay") hideMatchRationale();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !document.getElementById("match-rationale-overlay").hidden) {
      hideMatchRationale();
    }
  });

  document.getElementById("search-input").addEventListener("input", e => {
    state.search = e.target.value;
    state.currentPage = 1;
    renderJobs();
  });
  document.getElementById("pagination-prev").addEventListener("click", () => {
    state.currentPage -= 1;
    renderJobs();
    document.getElementById("main-view").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("pagination-next").addEventListener("click", () => {
    state.currentPage += 1;
    renderJobs();
    document.getElementById("main-view").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("company-filter").addEventListener("change", e => {
    state.companyFilter = e.target.value;
    loadJobs();
  });
  document.getElementById("clearance-filter").addEventListener("change", e => {
    const val = e.target.value;
    state.clearanceLevel = val.startsWith("level:") ? val.slice(6) : "";
    state.clearanceOnly = val === "clearance_only";
    loadJobs();
  });
  const selectedValues = select => [...select.selectedOptions].map(o => o.value);
  document.getElementById("city-filter").addEventListener("change", e => {
    state.cityFilter = selectedValues(e.target);
    loadJobs();
  });
  document.getElementById("state-filter").addEventListener("change", e => {
    state.stateFilter = selectedValues(e.target);
    loadJobs();
  });
  document.getElementById("country-filter").addEventListener("change", e => {
    state.countryFilter = selectedValues(e.target);
    loadJobs();
  });
  document.getElementById("clear-location-btn").addEventListener("click", () => {
    for (const id of ["city-filter", "state-filter", "country-filter"]) {
      for (const opt of document.getElementById(id).options) opt.selected = false;
      // Also clear checkboxes in custom dropdowns
      const filterId = id.replace("-filter", "");
      document.getElementById(`${filterId}-options`).querySelectorAll("input[type='checkbox']").forEach(cb => cb.checked = false);
      document.querySelector(`.multiselect-toggle[data-filter="${filterId}"] .multiselect-count`).textContent = "";
    }
    state.cityFilter = [];
    state.stateFilter = [];
    state.countryFilter = [];
    state.includeRemote = false;
    document.getElementById("include-remote-check").checked = false;
    loadJobs();
  });
  document.getElementById("remote-only-check").addEventListener("change", e => {
    state.remoteOnly = e.target.checked;
    loadJobs();
  });
  document.getElementById("include-remote-check").addEventListener("change", e => {
    state.includeRemote = e.target.checked;
    loadJobs();
  });
  document.getElementById("equity-only-check").addEventListener("change", e => {
    state.equityOnly = e.target.checked;
    loadJobs();
  });
  document.getElementById("grade-filter").addEventListener("change", e => {
    state.gradeFilter = selectedValues(e.target);
    loadJobs();
  });
  let salaryDebounce;
  const debounceSalary = () => {
    clearTimeout(salaryDebounce);
    salaryDebounce = setTimeout(loadJobs, 500);
  };
  document.getElementById("salary-min-input").addEventListener("input", e => {
    state.salaryMin = e.target.value === "" ? null : Number(e.target.value);
    debounceSalary();
  });
  document.getElementById("salary-max-input").addEventListener("input", e => {
    state.salaryMax = e.target.value === "" ? null : Number(e.target.value);
    debounceSalary();
  });
  document.getElementById("select-all-check").addEventListener("change", e => {
    const visible = getVisibleJobs();
    if (e.target.checked) {
      visible.forEach(j => state.selectedJobIds.add(j.id));
    } else {
      visible.forEach(j => state.selectedJobIds.delete(j.id));
    }
    updateSelectionUI();
    renderJobs();
  });
  document.getElementById("table-select-all-check").addEventListener("change", e => {
    const visible = getVisibleJobs();
    if (e.target.checked) {
      visible.forEach(j => state.selectedJobIds.add(j.id));
    } else {
      visible.forEach(j => state.selectedJobIds.delete(j.id));
    }
    updateSelectionUI();
    renderJobs();
  });
  document.getElementById("mark-applied-btn").addEventListener("click", markSelectedAsApplied);
  document.getElementById("applied-tab-btn").addEventListener("click", showAppliedView);
  document.getElementById("back-to-main-btn").addEventListener("click", showMainView);
  document.getElementById("refresh-btn").addEventListener("click", async () => {
    const btn = document.getElementById("refresh-btn");
    btn.disabled = true;
    btn.textContent = "Refreshing…";
    await fetchJSON("/api/refresh", { method: "POST" });
    setTimeout(async () => {
      await Promise.all([loadCompanies(), loadJobs(), loadStatus()]); // loadJobs() re-narrows locations itself
      btn.disabled = false;
      btn.textContent = "Refresh now";
    }, 15000); // give the background thread time to make headway; full refresh may take longer for 140 companies
  });

  document.querySelectorAll("thead th.sortable").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      if (state.sortColumn === col) {
        state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      } else {
        state.sortColumn = col;
        // Default to descending for clearance level and salary (highest first)
        state.sortDirection = (col === "clearance_level" || col === "salary_max") ? "desc" : "asc";
      }
      updateSortIndicators();
      loadJobs();
    });
  });

  document.getElementById("resume-toggle-btn").addEventListener("click", () => {
    const panel = document.getElementById("resume-panel");
    panel.hidden = !panel.hidden;
  });
  document.getElementById("save-resume-btn").addEventListener("click", saveResume);
  document.getElementById("score-jobs-btn").addEventListener("click", runScoreJobs);

  document.getElementById("reparse-toggle-btn").addEventListener("click", () => {
    const panel = document.getElementById("reparse-panel");
    panel.hidden = !panel.hidden;
  });
  document.getElementById("reparse-jobs-btn").addEventListener("click", runReparseJobs);

  document.getElementById("discover-companies-btn").addEventListener("click", runDiscoverCompanies);
}

async function loadResume() {
  const { resume_text } = await fetchJSON("/api/resume");
  document.getElementById("resume-text").value = resume_text || "";
}

async function saveResume() {
  const statusLine = document.getElementById("resume-status-line");
  const resume_text = document.getElementById("resume-text").value;
  const res = await fetch("/api/resume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume_text }),
  });
  const data = await res.json();
  const yoeText = data.years_of_experience !== null ? ` (${data.years_of_experience} years experience detected)` : "";
  statusLine.textContent = `Saved${yoeText}. Existing grades cleared — click "Score jobs vs resume" to re-grade.`;
  await loadJobs();
}

async function runScoreJobs() {
  const btn = document.getElementById("score-jobs-btn");
  const statusLine = document.getElementById("resume-status-line");
  const filteredOnly = document.getElementById("score-filtered-only-check").checked;

  let job_ids = null;
  if (filteredOnly) {
    job_ids = getVisibleJobs().map(j => j.id);
    if (!job_ids.length) {
      statusLine.textContent = "No jobs match the current filters — nothing to score.";
      return;
    }
  }

  const res = await fetch("/api/score-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids }),
  });
  if (res.status === 409) {
    // A run was already in flight, so this click's own job_ids (filtered or not) were never
    // sent anywhere -- the existing run just keeps going with whatever scope IT started with.
    // Say so explicitly rather than silently watching it as if this request had taken effect,
    // which previously made the filtered-only checkbox look broken/ignored.
    const existing = await fetchJSON("/api/score-jobs/status");
    const requestedCount = filteredOnly ? job_ids.length : null;
    const note = (requestedCount != null && existing.total !== requestedCount)
      ? `Different run already in progress, not your filtered ${requestedCount} — `
      : "";
    pollScoreJobs(btn, note);
    return;
  }
  if (res.status === 400) {
    statusLine.textContent = "Save a resume first.";
    return;
  }
  if (!res.ok) throw new Error(`/api/score-jobs -> ${res.status}`);
  btn.disabled = true;
  pollScoreJobs(btn);
}

function pollScoreJobs(btn, note = "") {
  const statusLine = document.getElementById("resume-status-line");
  const tick = async () => {
    const status = await fetchJSON("/api/score-jobs/status");
    if (status.running) {
      btn.disabled = true;
      const known = status.total > 0 ? `${status.processed}/${status.total}` : "…";
      statusLine.textContent = `${note}Scoring… ${known}`;
      setTimeout(tick, 2000);
    } else {
      btn.disabled = false;
      if (status.processed > 0) {
        statusLine.textContent = `Done — scored ${status.processed} job(s), ${status.errors} error(s).`;
        await loadJobs();
      }
    }
  };
  tick();
}

async function runReparseJobs() {
  const btn = document.getElementById("reparse-jobs-btn");
  const statusLine = document.getElementById("reparse-status-line");
  const filteredOnly = document.getElementById("reparse-filtered-only-check").checked;

  let job_ids = null;
  if (filteredOnly) {
    job_ids = getVisibleJobs().map(j => j.id);
    if (!job_ids.length) {
      statusLine.textContent = "No jobs match the current filters — nothing to re-parse.";
      return;
    }
  }

  const res = await fetch("/api/reparse-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids }),
  });
  if (res.status === 409) {
    // See runScoreJobs's identical 409 handling for why this can't just silently watch the
    // existing run: this click's job_ids were never sent anywhere, so a filtered request
    // sitting behind an already-running unfiltered one would otherwise look ignored/broken.
    const existing = await fetchJSON("/api/reparse-jobs/status");
    const requestedCount = filteredOnly ? job_ids.length : null;
    const note = (requestedCount != null && existing.total !== requestedCount)
      ? `Different run already in progress, not your filtered ${requestedCount} — `
      : "";
    pollReparseJobs(btn, note);
    return;
  }
  if (!res.ok) throw new Error(`/api/reparse-jobs -> ${res.status}`);
  btn.disabled = true;
  pollReparseJobs(btn);
}

function pollReparseJobs(btn, note = "") {
  const statusLine = document.getElementById("reparse-status-line");
  const tick = async () => {
    const status = await fetchJSON("/api/reparse-jobs/status");
    if (status.running) {
      btn.disabled = true;
      const known = status.total > 0 ? `${status.processed}/${status.total}` : "…";
      statusLine.textContent = `${note}Re-parsing… ${known}`;
      setTimeout(tick, 2000);
    } else {
      btn.disabled = false;
      if (status.processed > 0) {
        statusLine.textContent = `Done — re-parsed ${status.processed} job(s), ${status.errors} error(s).`;
        await loadJobs();
      }
    }
  };
  tick();
}

(async function init() {
  wireControls();
  populateGradeDropdown();
  await Promise.all([loadCompanies(), loadJobs(), loadStatus(), loadResume(), // loadJobs() re-narrows locations itself
                     loadAppliedJobs(), loadCandidateCompanies()]);
  if (state.jobs.length === 0) await pollUntilJobsAppear();
})();
