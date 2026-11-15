/* Front-end interactivity and live polling for Lead Scraper Dashboard */

document.addEventListener("DOMContentLoaded", () => {
  // State
  const selectedCategories = new Set(["plumbers"]);
  let activeJobId = null;
  let pollInterval = null;

  // DOM Elements
  const cityInput = document.getElementById("input-city");
  const metroPresetSelect = document.getElementById("select-metro-preset");
  const customCatInput = document.getElementById("custom-category-input");
  const btnAddCat = document.getElementById("btn-add-category");
  const chipsContainer = document.getElementById("selected-categories-list");
  const depthSelect = document.getElementById("select-depth");
  const gridOptions = document.getElementById("grid-options");
  const scrapeForm = document.getElementById("scrape-form");
  const btnSubmitJob = document.getElementById("btn-submit-job");

  // Monitor Elements
  const monitorSection = document.getElementById("active-job-section");
  const monitorJobId = document.getElementById("monitor-job-id");
  const monitorStatusBadge = document.getElementById("monitor-status-badge");
  const monitorPhase = document.getElementById("monitor-phase");
  const monitorProgressBar = document.getElementById("monitor-progress-bar");
  const monitorUnitProgress = document.getElementById("monitor-unit-progress");
  const monitorPct = document.getElementById("monitor-pct");
  const btnCancelJob = document.getElementById("btn-cancel-job");
  const btnClearLogs = document.getElementById("btn-clear-logs");
  const consoleOutput = document.getElementById("console-output");

  // Stats Elements
  const statFound = document.getElementById("stat-found");
  const statUnique = document.getElementById("stat-unique");
  const statQualified = document.getElementById("stat-qualified");
  const statDuplicates = document.getElementById("stat-duplicates");
  const statBlocks = document.getElementById("stat-blocks");
  const statTime = document.getElementById("stat-time");

  // Table
  const jobsTableBody = document.getElementById("jobs-table-body");
  const btnRefreshJobs = document.getElementById("btn-refresh-jobs");

  // --- Category Chips Management ---
  function renderChips() {
    chipsContainer.innerHTML = "";
    selectedCategories.forEach(cat => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `
        <span>${escapeHtml(cat)}</span>
        <span class="chip-remove" data-cat="${escapeHtml(cat)}">&times;</span>
      `;
      chipsContainer.appendChild(chip);
    });
  }

  function addCategory(cat) {
    const clean = cat.trim();
    if (clean && !selectedCategories.has(clean)) {
      selectedCategories.add(clean);
      renderChips();
    }
  }

  function removeCategory(cat) {
    selectedCategories.delete(cat);
    renderChips();
  }

  chipsContainer.addEventListener("click", e => {
    if (e.target.classList.contains("chip-remove")) {
      removeCategory(e.target.getAttribute("data-cat"));
    }
  });

  btnAddCat.addEventListener("click", () => {
    if (customCatInput.value) {
      addCategory(customCatInput.value);
      customCatInput.value = "";
    }
  });

  customCatInput.addEventListener("keydown", e => {
    if (e.key === "Enter") {
      e.preventDefault();
      btnAddCat.click();
    }
  });

  // Popular category suggestion buttons
  document.querySelectorAll(".btn-chip").forEach(btn => {
    btn.addEventListener("click", () => {
      const cat = btn.getAttribute("data-category");
      if (cat) addCategory(cat);
    });
  });

  // Metro preset dropdown
  metroPresetSelect.addEventListener("change", () => {
    if (metroPresetSelect.value) {
      cityInput.value = metroPresetSelect.value;
    }
  });

  // Depth toggle for grid options
  depthSelect.addEventListener("change", () => {
    if (depthSelect.value === "grid") {
      gridOptions.classList.remove("hidden");
    } else {
      gridOptions.classList.add("hidden");
    }
  });

  // Initial render
  renderChips();

  // --- Job Submission ---
  scrapeForm.addEventListener("submit", async e => {
    e.preventDefault();

    if (selectedCategories.size === 0) {
      alert("Please add at least one business category.");
      customCatInput.focus();
      return;
    }

    const payload = {
      city: cityInput.value.trim(),
      categories: Array.from(selectedCategories),
      depth: depthSelect.value,
      profile: document.getElementById("select-profile").value,
      max_results: document.getElementById("input-max-results").value ? parseInt(document.getElementById("input-max-results").value) : null,
      require_phone: document.getElementById("check-phone").checked,
      crawl_websites: document.getElementById("check-crawl").checked,
      grid_rows: parseInt(document.getElementById("input-grid-rows").value || 3),
      grid_cols: parseInt(document.getElementById("input-grid-cols").value || 3),
      grid_span_km: parseFloat(document.getElementById("input-grid-span").value || 18.0),
    };

    btnSubmitJob.disabled = true;
    btnSubmitJob.innerHTML = "<span>Starting Job...</span>";

    try {
      const resp = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || "Failed to start scraping job.");
      }

      const job = await resp.json();
      startMonitoring(job.job_id);
      refreshJobsTable();
    } catch (err) {
      alert("Error: " + err.message);
    } finally {
      btnSubmitJob.disabled = false;
      btnSubmitJob.innerHTML = "<span>⚡ Start Scraping Job</span>";
    }
  });

  // --- Monitoring & Live Polling ---
  function startMonitoring(jobId) {
    activeJobId = jobId;
    monitorSection.classList.remove("hidden");
    monitorJobId.textContent = jobId;
    consoleOutput.textContent = "";

    // Scroll to monitor
    monitorSection.scrollIntoView({ behavior: "smooth" });

    if (pollInterval) clearInterval(pollInterval);
    pollJobStatus();
    pollInterval = setInterval(pollJobStatus, 2500);
  }

  async function pollJobStatus() {
    if (!activeJobId) return;

    try {
      const resp = await fetch(`/api/jobs/${activeJobId}`);
      if (!resp.ok) return;

      const job = await resp.json();
      updateMonitorUI(job);

      if (["completed", "failed", "cancelled"].includes(job.status)) {
        clearInterval(pollInterval);
        pollInterval = null;
        refreshJobsTable();
      }
    } catch (e) {
      console.error("Poll error:", e);
    }
  }

  function updateMonitorUI(job) {
    // Status Badge
    monitorStatusBadge.className = `badge badge-${job.status}`;
    monitorStatusBadge.textContent = job.status;

    // Pulse dot
    const pulse = document.getElementById("monitor-pulse");
    if (["running", "queued"].includes(job.status)) {
      pulse.style.display = "inline-block";
    } else {
      pulse.style.display = "none";
    }

    // Phase
    monitorPhase.textContent = job.current_phase || job.status;

    // Progress
    const stats = job.stats || {};
    const total = stats.total_units || 0;
    const done = (stats.units_done || 0) + (stats.units_failed || 0) + (stats.units_skipped || 0);
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : (job.status === "completed" ? 100 : 0);

    monitorProgressBar.style.width = `${pct}%`;
    monitorUnitProgress.textContent = `Unit ${done} / ${total}`;
    monitorPct.textContent = `${pct}%`;

    // Metrics
    statFound.textContent = stats.found || 0;
    statUnique.textContent = stats.unique || 0;
    statQualified.textContent = stats.qualified || 0;
    statDuplicates.textContent = stats.duplicates || 0;
    statBlocks.textContent = stats.blocks || 0;

    const elapsed = Math.round(stats.elapsed_seconds || 0);
    const eta = stats.eta_seconds ? Math.round(stats.eta_seconds) : null;
    statTime.textContent = eta ? `${elapsed}s (ETA: ${eta}s)` : `${elapsed}s`;

    // Logs
    if (job.recent_logs && job.recent_logs.length > 0) {
      consoleOutput.textContent = job.recent_logs.join("\n");
      consoleOutput.scrollTop = consoleOutput.scrollHeight;
    }
  }

  // Cancel Job
  btnCancelJob.addEventListener("click", async () => {
    if (!activeJobId) return;
    if (confirm("Are you sure you want to stop this scraping job?")) {
      try {
        await fetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
        pollJobStatus();
      } catch (err) {
        alert("Failed to cancel job: " + err);
      }
    }
  });

  btnClearLogs.addEventListener("click", () => {
    consoleOutput.textContent = "";
  });

  // --- Table Refreshing & Action Buttons ---
  async function refreshJobsTable() {
    try {
      const resp = await fetch("/api/jobs");
      if (!resp.ok) return;
      const jobs = await resp.json();
      renderJobsTable(jobs);
    } catch (e) {
      console.error("Refresh jobs error:", e);
    }
  }

  function renderJobsTable(jobs) {
    if (!jobs || jobs.length === 0) {
      jobsTableBody.innerHTML = `
        <tr id="no-jobs-row">
          <td colspan="7" class="text-center py-8 text-muted">
            No scraping jobs yet. Launch your first run above!
          </td>
        </tr>
      `;
      return;
    }

    jobsTableBody.innerHTML = jobs.map(j => {
      const dateStr = j.created_at ? j.created_at.slice(0, 16).replace("T", " ") : "";
      const categoriesStr = (j.params && j.params.categories) ? j.params.categories.join(", ") : "";

      const downloads = [];
      if (j.files && j.files.qualified_csv) {
        downloads.push(`<a href="/api/jobs/${j.job_id}/download/leads_qualified.csv" class="btn btn-xs btn-success" download>Qualified CSV</a>`);
      }
      if (j.files && j.files.raw_csv) {
        downloads.push(`<a href="/api/jobs/${j.job_id}/download/leads_raw.csv" class="btn btn-xs btn-outline" download>Raw CSV</a>`);
      }
      if (j.files && j.files.xlsx) {
        downloads.push(`<a href="/api/jobs/${j.job_id}/download/leads.xlsx" class="btn btn-xs btn-primary" download>Excel (.xlsx)</a>`);
      }
      if (j.files && j.files.log) {
        downloads.push(`<a href="/api/jobs/${j.job_id}/download/scraper.log" class="btn btn-xs btn-ghost" download>Log</a>`);
      }

      return `
        <tr id="row-${j.job_id}" class="job-row">
          <td>
            <div class="mono text-bold">${escapeHtml(j.job_id)}</div>
            <div class="text-sm text-muted">${dateStr}</div>
          </td>
          <td>
            <div class="target-name">📍 ${escapeHtml(j.params.city || "")}</div>
            <div class="categories-preview text-sm text-muted">${escapeHtml(categoriesStr)}</div>
          </td>
          <td>
            <span class="badge badge-subtle">${j.params.depth || ""}</span>
            <span class="badge badge-subtle">${j.params.profile || ""}</span>
          </td>
          <td>
            <span class="badge badge-${j.status}">${j.status}</span>
          </td>
          <td>
            <div class="lead-counts">
              <span>${j.stats.found || 0} total</span> /
              <strong class="text-success">${j.stats.qualified || 0} qual</strong>
            </div>
          </td>
          <td>
            <div class="download-group">
              ${downloads.length > 0 ? downloads.join(" ") : '<span class="text-muted text-sm">Processing...</span>'}
            </div>
          </td>
          <td>
            <button class="btn btn-xs btn-ghost btn-inspect" data-job-id="${j.job_id}">Inspect</button>
            <button class="btn btn-xs btn-ghost text-danger btn-delete" data-job-id="${j.job_id}">Delete</button>
          </td>
        </tr>
      `;
    }).join("");
  }

  // Inspect and Delete button handlers in table
  jobsTableBody.addEventListener("click", async e => {
    const inspectBtn = e.target.closest(".btn-inspect");
    if (inspectBtn) {
      const jid = inspectBtn.getAttribute("data-job-id");
      startMonitoring(jid);
      return;
    }

    const deleteBtn = e.target.closest(".btn-delete");
    if (deleteBtn) {
      const jid = deleteBtn.getAttribute("data-job-id");
      if (confirm(`Delete job ${jid} and all its output files?`)) {
        try {
          await fetch(`/api/jobs/${jid}`, { method: "DELETE" });
          if (activeJobId === jid) {
            monitorSection.classList.add("hidden");
            activeJobId = null;
          }
          refreshJobsTable();
        } catch (err) {
          alert("Delete failed: " + err);
        }
      }
    }
  });

  btnRefreshJobs.addEventListener("click", refreshJobsTable);

  // Periodic refresh of table every 10 seconds
  setInterval(refreshJobsTable, 10000);

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
