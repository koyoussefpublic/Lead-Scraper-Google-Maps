/* CarteFidele CRM & Outreach Workspace Frontend */

document.addEventListener("DOMContentLoaded", () => {
  const tabs = document.querySelectorAll(".crm-tab");
  const panels = document.querySelectorAll(".crm-panel");
  const envBadge = document.getElementById("crm-env-badge");

  // Modal
  const leadModal = document.getElementById("lead-detail-modal");
  const modalTitle = document.getElementById("modal-lead-title");
  const modalBody = document.getElementById("modal-lead-body");
  document.getElementById("btn-close-lead-modal").addEventListener("click", () => {
    leadModal.classList.add("hidden");
  });

  function switchTab(tabName) {
    tabs.forEach(t => {
      const active = t.dataset.tab === tabName;
      t.classList.toggle("btn-primary", active);
      t.classList.toggle("btn-ghost", !active);
    });
    panels.forEach(p => {
      p.classList.toggle("hidden", p.id !== `tab-${tabName}`);
    });
    if (tabName === "overview") loadAnalytics();
    if (tabName === "leads") loadLeads();
    if (tabName === "pipeline") loadPipeline();
    if (tabName === "tasks") loadTasks("all");
    if (tabName === "campaigns") loadCampaignsAndTemplates();
    if (tabName === "inbox") loadInbox();
    if (tabName === "integrations") loadIntegrations();
    if (tabName === "compliance") loadSuppressions();
  }

  tabs.forEach(t => t.addEventListener("click", () => switchTab(t.dataset.tab)));

  async function apiJson(url, opts = {}) {
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // --- 1. Analytics ---
  async function loadAnalytics() {
    const data = await apiJson("/api/crm/analytics");
    const grid = document.getElementById("crm-kpi-grid");
    const kpis = [
      ["Total Leads", data.total_leads],
      ["New", data.new_leads],
      ["Qualified", data.qualified_leads],
      ["Contacted", data.contacted_leads],
      ["Replied", data.replied_leads],
      ["Demos Scheduled", data.demos_scheduled],
      ["Demos Completed", data.demos_completed],
      ["Converted", `${data.converted_customers} (${data.conversion_rate_pct}%)`],
    ];
    grid.innerHTML = kpis.map(([lbl, val]) => `
      <div class="stat-card"><span class="stat-label">${lbl}</span><span class="stat-value">${val}</span></div>
    `).join("");

    document.getElementById("analytics-categories").innerHTML =
      data.top_categories.length
        ? data.top_categories.map(c => `<div>${esc(c.category)}: <strong>${c.count}</strong> (avg score ${c.avg_score})</div>`).join("")
        : "No category data yet.";

    document.getElementById("analytics-cities").innerHTML =
      data.top_cities.length
        ? data.top_cities.map(c => `<div>${esc(c.city)} ${esc(c.country)}: <strong>${c.count}</strong></div>`).join("")
        : "No city data yet.";
  }

  // --- 2. Leads Table ---
  async function loadLeads() {
    const search = document.getElementById("lead-filter-search").value.trim();
    const status = document.getElementById("lead-filter-status").value;
    const minScore = document.getElementById("lead-filter-min-score").value;
    const qs = new URLSearchParams();
    if (search) qs.set("search", search);
    if (status) qs.set("status", status);
    if (minScore) qs.set("min_score", minScore);

    const data = await apiJson(`/api/crm/leads?${qs.toString()}`);
    const tbody = document.getElementById("crm-leads-tbody");
    if (!data.items.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="text-center text-muted">No leads found. Run a scrape or import a CSV.</td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map(l => `
      <tr>
        <td><input type="checkbox" class="lead-checkbox" value="${l.id}"></td>
        <td><strong>${esc(l.name)}</strong><div class="text-sm text-muted">${esc(l.category)}</div></td>
        <td>${esc(l.city)} ${esc(l.country)}</td>
        <td class="text-sm">${esc(l.phone || "-")}<br>${esc(l.email || "-")}</td>
        <td>${l.rating ?? "-"} (${l.review_count ?? 0})</td>
        <td><span class="badge badge-primary">${l.score}/100</span></td>
        <td><span class="badge badge-subtle">${esc(l.pipeline_status)}</span></td>
        <td><button class="btn btn-xs btn-primary btn-open-lead" data-id="${l.id}">Open</button></td>
      </tr>
    `).join("");
  }

  document.getElementById("btn-filter-leads").addEventListener("click", loadLeads);
  document.getElementById("select-all-leads").addEventListener("change", e => {
    document.querySelectorAll(".lead-checkbox").forEach(cb => { cb.checked = e.target.checked; });
  });

  document.getElementById("btn-apply-bulk").addEventListener("click", async () => {
    const statusVal = document.getElementById("bulk-action-status").value;
    const ids = Array.from(document.querySelectorAll(".lead-checkbox:checked")).map(c => parseInt(c.value, 10));
    if (!statusVal || !ids.length) return alert("Select leads and a bulk status.");
    await apiJson("/api/crm/leads/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lead_ids: ids, action: "status", value: statusVal }),
    });
    loadLeads();
  });

  document.getElementById("crm-import-csv-input").addEventListener("change", async e => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const res = await apiJson("/api/crm/leads/import-csv", { method: "POST", body: fd });
    alert(`CSV Import complete: ${res.created} created, ${res.updated} updated, ${res.skipped} skipped.`);
    e.target.value = "";
    loadLeads();
  });

  document.getElementById("crm-leads-tbody").addEventListener("click", e => {
    const btn = e.target.closest(".btn-open-lead");
    if (btn) openLeadDetail(parseInt(btn.dataset.id, 10));
  });

  // --- Lead Detail Modal ---
  async function openLeadDetail(leadId) {
    const lead = await apiJson(`/api/crm/leads/${leadId}`);
    modalTitle.textContent = `${lead.name} (${lead.score}/100)`;
    const reasons = (lead.score_breakdown && lead.score_breakdown.reasons) || [];
    const contact = (lead.contacts && lead.contacts[0]) || null;

    modalBody.innerHTML = `
      <div style="display: grid; gap: 0.75rem;">
        <div>
          <strong>Category:</strong> ${esc(lead.category)} | <strong>City:</strong> ${esc(lead.city)} ${esc(lead.country)}<br>
          <strong>Phone:</strong> ${esc(lead.phone)} | <strong>Email:</strong> ${esc(lead.email)}<br>
          <strong>Website:</strong> ${lead.website ? `<a href="${esc(lead.website)}" target="_blank">${esc(lead.website)}</a>` : "None"}
          ${lead.google_maps_url ? `| <a href="${esc(lead.google_maps_url)}" target="_blank">Google Maps</a>` : ""}
        </div>
        <div>
          <strong>Score Explanation:</strong>
          <ul style="margin: 0.25rem 0 0 1.25rem;">${reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
        </div>
        <div style="display: flex; gap: 0.5rem; align-items: center;">
          <label><strong>Pipeline Status:</strong></label>
          <select id="modal-status-select" class="form-control form-control-sm" style="max-width: 200px;">
            ${["new","researching","qualified","contacted","replied","interested","demo_scheduled","demo_completed","trial","converted","disqualified","not_interested","unreachable","do_not_contact"].map(s => `<option value="${s}" ${lead.pipeline_status === s ? "selected" : ""}>${s}</option>`).join("")}
          </select>
          <button id="btn-save-lead-status" class="btn btn-xs btn-primary" type="button">Update</button>
        </div>
        ${contact ? `
        <div class="card" style="padding: 0.75rem;">
          <strong>Channel Eligibility & Compliance (Contact #${contact.id})</strong>
          <div class="text-sm text-muted" style="margin-bottom: 0.4rem;">Public scraping does not grant outreach consent automatically.</div>
          <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <button id="btn-toggle-email-elig" class="btn btn-xs ${contact.email_eligible ? "btn-success" : "btn-outline"}" type="button">
              Email: ${contact.email_eligible ? "Eligible ✓" : "Ineligible (Click to Verify)"}
            </button>
            <button id="btn-toggle-wa-elig" class="btn btn-xs ${contact.whatsapp_eligible ? "btn-success" : "btn-outline"}" type="button">
              WhatsApp: ${contact.whatsapp_eligible ? "Eligible ✓" : "Ineligible (Click to Verify)"}
            </button>
          </div>
        </div>` : ""}
        <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
          <button id="btn-modal-schedule-demo" class="btn btn-xs btn-primary" type="button">📅 Schedule Demo</button>
          <button id="btn-modal-add-task" class="btn btn-xs btn-secondary" type="button">📝 Add Follow-up Task</button>
          <button id="btn-modal-add-note" class="btn btn-xs btn-ghost" type="button">💬 Add Note</button>
        </div>
        <div>
          <strong>Send Email (Resend)</strong>
          <input type="text" id="quick-email-subj" class="form-control form-control-sm" value="CarteFidele Demo for ${esc(lead.name)}" style="margin-top:0.35rem;">
          <textarea id="quick-email-body" class="form-control form-control-sm" rows="3" style="margin-top:0.35rem;">Bonjour, CarteFidele aide les commerces à ${esc(lead.city)} à fidéliser leurs clients. Disponible pour une démo ?</textarea>
          <button id="btn-send-quick-email" class="btn btn-xs btn-success" style="margin-top:0.35rem;" type="button">Send Email</button>
        </div>
        <div>
          <strong>Send WhatsApp (Evolution / Mock)</strong>
          <textarea id="quick-wa-body" class="form-control form-control-sm" rows="2" style="margin-top:0.35rem;">Bonjour ${esc(lead.name)}, ici CarteFidele (cartefidele.com). Souhaitez-vous découvrir notre solution de fidélité digitale ?</textarea>
          <button id="btn-send-quick-wa" class="btn btn-xs btn-success" style="margin-top:0.35rem;" type="button">Send WhatsApp</button>
        </div>
        <div>
          <strong>Recent Activities</strong>
          <div class="text-sm" style="max-height: 150px; overflow-y: auto;">
            ${(lead.activities || []).map(a => `<div>• <strong>${esc(a.title)}</strong> <span class="text-muted">(${esc((a.created_at || "").slice(0,16))})</span></div>`).join("") || "No activities yet."}
          </div>
        </div>
      </div>
    `;

    leadModal.classList.remove("hidden");

    document.getElementById("btn-save-lead-status").onclick = async () => {
      const st = document.getElementById("modal-status-select").value;
      await apiJson(`/api/crm/leads/${leadId}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: st }),
      });
      openLeadDetail(leadId);
      loadLeads();
    };

    if (contact) {
      document.getElementById("btn-toggle-email-elig").onclick = async () => {
        await apiJson(`/api/crm/contacts/${contact.id}/eligibility`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email_eligible: !contact.email_eligible }),
        });
        openLeadDetail(leadId);
      };
      document.getElementById("btn-toggle-wa-elig").onclick = async () => {
        await apiJson(`/api/crm/contacts/${contact.id}/eligibility`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ whatsapp_eligible: !contact.whatsapp_eligible }),
        });
        openLeadDetail(leadId);
      };
    }

    document.getElementById("btn-modal-add-note").onclick = async () => {
      const note = prompt("Enter sales note:");
      if (!note) return;
      await apiJson(`/api/crm/leads/${leadId}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      openLeadDetail(leadId);
    };

    document.getElementById("btn-modal-add-task").onclick = async () => {
      const title = prompt("Follow-up task title:", `Follow up with ${lead.name}`);
      if (!title) return;
      const due = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
      await apiJson("/api/crm/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_id: leadId, title, due_date: due }),
      });
      openLeadDetail(leadId);
    };

    document.getElementById("btn-modal-schedule-demo").onclick = async () => {
      const when = prompt("Demo date/time (YYYY-MM-DD HH:MM):", new Date(Date.now() + 86400000).toISOString().slice(0, 16).replace("T", " "));
      if (!when) return;
      await apiJson("/api/crm/demos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_id: leadId, scheduled_at: when }),
      });
      openLeadDetail(leadId);
    };

    document.getElementById("btn-send-quick-email").onclick = async () => {
      try {
        const res = await apiJson(`/api/crm/leads/${leadId}/send-email`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            to_email: lead.email || "",
            subject: document.getElementById("quick-email-subj").value,
            body_text: document.getElementById("quick-email-body").value,
            contact_id: contact ? contact.id : null,
          }),
        });
        alert(`Email status: ${res.delivery_status} (${res.provider})${res.error_message ? "\nError: " + res.error_message : ""}`);
        openLeadDetail(leadId);
      } catch (err) {
        alert(err.message);
      }
    };

    document.getElementById("btn-send-quick-wa").onclick = async () => {
      try {
        const res = await apiJson(`/api/crm/leads/${leadId}/send-whatsapp`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            phone: lead.phone || "",
            body_text: document.getElementById("quick-wa-body").value,
            contact_id: contact ? contact.id : null,
          }),
        });
        alert(`WhatsApp status: ${res.delivery_status} (${res.provider})${res.error_message ? "\nError: " + res.error_message : ""}`);
        openLeadDetail(leadId);
      } catch (err) {
        alert(err.message);
      }
    };
  }

  // --- 3. Pipeline Kanban ---
  async function loadPipeline() {
    const data = await apiJson("/api/crm/leads?limit=200");
    const cols = ["new", "qualified", "contacted", "replied", "interested", "demo_scheduled", "converted"];
    const board = document.getElementById("kanban-board");
    board.innerHTML = cols.map(col => {
      const items = data.items.filter(i => i.pipeline_status === col);
      return `
        <div class="card" style="padding: 0.75rem;">
          <h4 style="text-transform: uppercase; font-size: 0.8rem; margin-bottom: 0.5rem;">${col.replace("_", " ")} (${items.length})</h4>
          <div style="display: grid; gap: 0.4rem;">
            ${items.slice(0, 15).map(i => `
              <div class="card" style="padding: 0.5rem; cursor: pointer;" onclick="document.dispatchEvent(new CustomEvent('open-lead', {detail: ${i.id}}))">
                <div class="text-sm"><strong>${esc(i.name)}</strong></div>
                <div class="text-sm text-muted">${esc(i.city)} • ${i.score}/100</div>
              </div>
            `).join("") || '<div class="text-sm text-muted">Empty</div>'}
          </div>
        </div>
      `;
    }).join("");
  }
  document.addEventListener("open-lead", e => openLeadDetail(e.detail));

  // --- 4. Tasks & Demos ---
  async function loadTasks(bucket) {
    const tasks = await apiJson(`/api/crm/tasks?bucket=${bucket}`);
    const list = document.getElementById("crm-tasks-list");
    list.innerHTML = tasks.length
      ? tasks.map(t => `
        <div class="card" style="padding: 0.6rem; margin-bottom: 0.4rem; display: flex; justify-content: space-between; align-items: center;">
          <div><strong>${esc(t.title)}</strong> — ${esc(t.lead_name)} <span class="text-muted text-sm">(Due: ${esc(t.due_date.slice(0,10))}, ${t.status})</span></div>
          ${t.status === "pending" ? `<button class="btn btn-xs btn-success btn-complete-task" data-id="${t.id}">Complete</button>` : ""}
        </div>`).join("")
      : '<div class="text-muted">No tasks in this view.</div>';

    const demos = await apiJson("/api/crm/demos");
    document.getElementById("crm-demos-list").innerHTML = demos.length
      ? demos.map(d => `<div class="card" style="padding: 0.6rem; margin-bottom: 0.4rem;"><strong>${esc(d.lead_name)}</strong> — ${esc(d.scheduled_at)} <span class="badge badge-subtle">${esc(d.status)}</span></div>`).join("")
      : '<div class="text-muted">No demos scheduled yet.</div>';
  }

  document.querySelectorAll(".task-bucket").forEach(b => b.addEventListener("click", () => loadTasks(b.dataset.bucket)));
  document.getElementById("crm-tasks-list").addEventListener("click", async e => {
    const btn = e.target.closest(".btn-complete-task");
    if (btn) {
      await apiJson(`/api/crm/tasks/${btn.dataset.id}/complete`, { method: "POST" });
      loadTasks("all");
    }
  });

  // --- 5. Campaigns & Templates ---
  async function loadCampaignsAndTemplates() {
    const camps = await apiJson("/api/crm/campaigns");
    document.getElementById("crm-campaigns-list").innerHTML = camps.length
      ? camps.map(c => `
        <div class="card" style="padding: 0.65rem; margin-bottom: 0.4rem; display: flex; justify-content: space-between; align-items: center;">
          <div><strong>${esc(c.name)}</strong> (${esc(c.channel)}, ${esc(c.language)}) — <span class="badge badge-subtle">${esc(c.status)}</span></div>
          <div style="display: flex; gap: 0.4rem;">
            <button class="btn btn-xs btn-secondary btn-preview-camp" data-id="${c.id}">Preview</button>
            <button class="btn btn-xs btn-primary btn-run-camp" data-id="${c.id}">Execute</button>
          </div>
        </div>`).join("")
      : '<div class="text-muted">No campaigns created yet.</div>';

    const tpls = await apiJson("/api/crm/templates");
    document.getElementById("crm-templates-list").innerHTML = tpls.map(t => `
      <div class="card" style="padding: 0.6rem; margin-bottom: 0.4rem;">
        <strong>${esc(t.name)}</strong> <span class="badge badge-subtle">${esc(t.channel)} / ${esc(t.language)}</span>
        <div class="text-sm text-muted">${esc(t.body_text.slice(0, 140))}...</div>
      </div>`).join("");
  }

  document.getElementById("campaign-create-form").addEventListener("submit", async e => {
    e.preventDefault();
    await apiJson("/api/crm/campaigns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("camp-name").value,
        channel: document.getElementById("camp-channel").value,
        language: document.getElementById("camp-lang").value,
        min_score: parseInt(document.getElementById("camp-min-score").value || "0", 10),
      }),
    });
    e.target.reset();
    loadCampaignsAndTemplates();
  });

  document.getElementById("crm-campaigns-list").addEventListener("click", async e => {
    const prev = e.target.closest(".btn-preview-camp");
    if (prev) {
      const res = await apiJson(`/api/crm/campaigns/${prev.dataset.id}/preview`);
      alert(`Eligible targets: ${res.eligible_count} | Blocked by compliance/status: ${res.blocked_count}`);
    }
    const run = e.target.closest(".btn-run-camp");
    if (run && confirm("Execute this campaign for eligible targets?")) {
      const res = await apiJson(`/api/crm/campaigns/${run.dataset.id}/execute`, { method: "POST" });
      alert(`Campaign finished: sent=${res.sent}, failed=${res.failed}, manual_tasks=${res.manual_tasks}, blocked=${res.blocked}`);
      loadCampaignsAndTemplates();
    }
  });

  // --- 6. Inbox ---
  async function loadInbox() {
    const msgs = await apiJson("/api/crm/inbox");
    document.getElementById("crm-inbox-list").innerHTML = msgs.length
      ? msgs.map(m => `
        <div class="card" style="padding: 0.65rem; margin-bottom: 0.4rem;">
          <div><strong>${esc(m.lead_name)}</strong> (${esc(m.channel)} / ${esc(m.direction)}) — <span class="badge badge-subtle">${esc(m.delivery_status)}</span> ${m.is_simulated ? '<span class="badge badge-warning">SIMULATED</span>' : ""}</div>
          <div class="text-sm">${esc(m.body_text)}</div>
        </div>`).join("")
      : '<div class="text-muted">No messages recorded yet.</div>';
  }

  // --- 7. Integrations ---
  async function loadIntegrations() {
    const [settings, wa] = await Promise.all([apiJson("/api/crm/settings"), apiJson("/api/crm/whatsapp/status")]);
    const resend = settings.resend;
    document.getElementById("integrations-status-box").innerHTML = `
      <div class="card" style="padding: 1rem;">
        <h3>📧 Email (Resend)</h3>
        <div class="text-sm">Configured: <strong>${resend.configured ? "Yes" : "No"}</strong></div>
        <div class="text-sm">From: ${esc(resend.from_email)}</div>
        <div class="text-sm">Reply-To: ${esc(resend.reply_to)}</div>
        <div class="text-sm">External Sending: <strong>${resend.external_messaging_allowed ? "Enabled" : "Disabled (Safe Mode)"}</strong></div>
      </div>
      <div class="card" style="padding: 1rem;">
        <h3>💬 WhatsApp (Self-Hosted Evolution API)</h3>
        <div class="text-sm">Mode: <strong>${wa.development_mock_mode ? "LOCAL MOCK (Safe)" : esc(wa.provider)}</strong></div>
        <div class="text-sm">Instance: ${esc(wa.evolution_instance)}</div>
        <div class="text-sm">State: <strong>${esc(wa.live.state)}</strong></div>
        <div style="margin-top: 0.5rem; display: flex; gap: 0.4rem;">
          <button id="btn-wa-qr" class="btn btn-xs btn-primary" type="button">Get QR Code</button>
          <button id="btn-wa-disconnect" class="btn btn-xs btn-danger" type="button">Disconnect</button>
        </div>
      </div>
    `;

    document.getElementById("btn-wa-qr").onclick = async () => {
      const qrBox = document.getElementById("whatsapp-qr-box");
      const qr = await apiJson("/api/crm/whatsapp/qr");
      qrBox.classList.remove("hidden");
      qrBox.innerHTML = qr.qr_available && qr.base64
        ? `<img src="${qr.base64}" alt="WhatsApp QR" style="max-width: 240px;">`
        : `<div class="text-sm">${esc(qr.message || "QR unavailable.")}</div>`;
    };

    document.getElementById("btn-wa-disconnect").onclick = async () => {
      await apiJson("/api/crm/whatsapp/disconnect", { method: "POST" });
      loadIntegrations();
    };
  }

  // --- 8. Compliance ---
  async function loadSuppressions() {
    const items = await apiJson("/api/crm/suppression");
    document.getElementById("crm-suppression-list").innerHTML = items.length
      ? items.map(s => `
        <div class="card" style="padding: 0.5rem; margin-bottom: 0.35rem; display: flex; justify-content: space-between; align-items: center;">
          <div class="text-sm"><strong>${esc(s.channel)}</strong>: ${esc(s.identifier)} (${esc(s.reason)})</div>
          <button class="btn btn-xs btn-ghost text-danger btn-del-sup" data-id="${s.id}">Remove</button>
        </div>`).join("")
      : '<div class="text-muted">Suppression list is empty.</div>';
  }

  document.getElementById("suppression-form").addEventListener("submit", async e => {
    e.preventDefault();
    await apiJson("/api/crm/suppression", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        channel: document.getElementById("sup-channel").value,
        identifier: document.getElementById("sup-identifier").value,
        reason: document.getElementById("sup-reason").value || "Manual opt-out",
      }),
    });
    e.target.reset();
    loadSuppressions();
  });

  document.getElementById("crm-suppression-list").addEventListener("click", async e => {
    const btn = e.target.closest(".btn-del-sup");
    if (btn) {
      await apiJson(`/api/crm/suppression/${btn.dataset.id}`, { method: "DELETE" });
      loadSuppressions();
    }
  });

  // Initial load
  apiJson("/api/crm/whatsapp/status").then(wa => {
    envBadge.textContent = wa.development_mock_mode ? "ENV: DEVELOPMENT (MOCK MESSAGING)" : "ENV: PRODUCTION";
  }).catch(() => {});
  loadAnalytics();
});
