const $ = (selector) => document.querySelector(selector);
const loginView = $("#login-view");
const dashboardView = $("#dashboard-view");
const optionLabels = {
  representedCompany: "Uzņēmums / iestāde",
  documentType: "Dokumenta veids",
  visitedCompany: "Apmeklējamais uzņēmums",
  hostPerson: "Persona, pie kuras ieradies",
  visitPurpose: "Vizītes mērķis",
};
const privacyFieldIds = {
  shortNotice: "privacy-short-notice",
  controllerName: "privacy-controller-name",
  controllerRegistrationNumber: "privacy-controller-registration-number",
  controllerContact: "privacy-controller-contact",
  dpoContact: "privacy-dpo-contact",
  legalBasis: "privacy-legal-basis",
  legalBasisDetails: "privacy-legal-basis-details",
  processingPurpose: "privacy-processing-purpose",
  recipients: "privacy-recipients",
  retentionMonths: "privacy-retention-months",
  storageDescription: "privacy-storage-description",
  nextRegistrationSeconds: "privacy-next-registration-seconds",
  privacyReadingSeconds: "privacy-reading-seconds",
};

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Neizdevās ielādēt datus.");
  return data;
}

function setView(isLoggedIn) {
  loginView.classList.toggle("hidden", isLoggedIn);
  dashboardView.classList.toggle("hidden", !isLoggedIn);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

function queryString() {
  const params = new URLSearchParams();
  if ($("#filter-from").value) params.set("from", $("#filter-from").value);
  if ($("#filter-to").value) params.set("to", $("#filter-to").value);
  if ($("#filter-search").value.trim()) params.set("q", $("#filter-search").value.trim());
  return params.toString();
}

async function loadVisits() {
  const query = queryString();
  $("#dashboard-error").textContent = "";
  try {
    const data = await api(`/api/admin/visits${query ? `?${query}` : ""}`);
    $("#result-summary").textContent = `${data.count} ${data.count === 1 ? "ieraksts" : "ieraksti"} · Laika josla: ${data.timezone}`;
    $("#export-link").href = `/api/admin/export.csv${query ? `?${query}` : ""}`;
    $("#export-photo-link").href = `/api/admin/export.zip${query ? `?${query}` : ""}`;
    $("#visits-body").innerHTML = data.visits.length ? data.visits.map((visit) => `
      <tr>
        <td>${escapeHtml(visit.displayTime)}</td>
        <td><strong>${escapeHtml(visit.firstName)} ${escapeHtml(visit.lastName)}</strong><small>${escapeHtml(visit.personalCode)}</small></td>
        <td>${escapeHtml(visit.representedCompany)}</td>
        <td>${escapeHtml(visit.documentType)}<small>${escapeHtml(visit.documentNumber)}</small></td>
        <td><strong>${escapeHtml(visit.visitedCompany)}</strong><small>Pie: ${escapeHtml(visit.hostPerson)}</small><small>${escapeHtml(visit.visitPurpose)}</small></td>
        <td>${visit.hasPhoto ? `<a class="photo-link" href="/api/admin/visits/${Number(visit.id)}/photo" target="_blank" rel="noopener">Skatīt foto</a>` : "—"}</td>
        <td><button class="delete-button" type="button" data-delete-id="${visit.id}" aria-label="Dzēst ierakstu">Dzēst</button></td>
      </tr>`).join("") : '<tr><td colspan="7" class="empty-state">Atlasītajā periodā ierakstu nav.</td></tr>';
  } catch (error) {
    if (error.message.includes("piekļuve")) { setView(false); return; }
    $("#dashboard-error").textContent = error.message;
  }
}

function applyPrivacySettings(settings) {
  Object.entries(privacyFieldIds).forEach(([key, elementId]) => {
    const element = $(`#${elementId}`);
    if (element) element.value = settings?.[key] || "";
  });
}

function privacyPayload() {
  return Object.fromEntries(Object.entries(privacyFieldIds).map(([key, elementId]) => [key, $(`#${elementId}`).value]));
}

async function loadPrivacy() {
  $("#privacy-error").textContent = "";
  try {
    const data = await api("/api/admin/privacy");
    applyPrivacySettings(data.privacy);
  } catch (error) {
    $("#privacy-error").textContent = error.message;
  }
}

async function cameraPermissionState() {
  if (!window.isSecureContext) return "insecure";
  if (!navigator.mediaDevices?.getUserMedia) return "unavailable";
  if (!navigator.permissions?.query) return "unknown";
  try {
    return (await navigator.permissions.query({ name: "camera" })).state;
  } catch (_) {
    return "unknown";
  }
}

async function updateCameraSetupStatus() {
  const state = await cameraPermissionState();
  const messages = {
    granted: "Kamera šajā pārlūkā ir atļauta.",
    prompt: "Kamera vēl nav aktivizēta šajā pārlūkā.",
    denied: "Kameras piekļuve ir bloķēta pārlūka iestatījumos.",
    insecure: "Kameras iestatīšanai nepieciešama HTTPS adrese.",
    unavailable: "Šajā ierīcē vai pārlūkā kamera nav pieejama.",
    unknown: "Nospiediet pogu, lai pārbaudītu un aktivizētu kameru.",
  };
  $("#camera-setup-status").textContent = messages[state];
}

$("#camera-setup-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  $("#camera-setup-status").textContent = "Gaida pārlūka kameras atļauju…";
  try {
    if (!window.isSecureContext) throw new Error("Kameras iestatīšanai atveriet administratora paneli HTTPS adresē.");
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    stream.getTracks().forEach((track) => track.stop());
    $("#camera-setup-status").textContent = "Kamera ir atļauta šajā pārlūkā un planšetē. Apmeklētājiem tā vairs nebūs jāapstiprina.";
  } catch (error) {
    $("#camera-setup-status").textContent = error.message || "Kameras atļauju neizdevās piešķirt.";
  } finally {
    button.disabled = false;
  }
});

async function loadOptions() {
  $("#option-error").textContent = "";
  try {
    const data = await api("/api/admin/options");
    $("#option-groups").innerHTML = Object.entries(optionLabels).map(([fieldKey, label]) => {
      const values = data.options[fieldKey] || [];
      return `<section class="option-group"><h3>${escapeHtml(label)}</h3>${values.length
        ? `<ul>${values.map((option) => `<li><span>${escapeHtml(option.value)}</span><button class="option-delete" type="button" data-option-id="${option.id}" aria-label="Dzēst: ${escapeHtml(option.value)}">×</button></li>`).join("")}</ul>`
        : '<p>Vērtību vēl nav.</p>'}</section>`;
    }).join("");
  } catch (error) {
    $("#option-error").textContent = error.message;
  }
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("#login-error").textContent = "";
  const button = event.submitter;
  button.disabled = true;
  try {
    await api("/api/admin/login", { method: "POST", body: JSON.stringify({ password: $("#admin-password").value }) });
    $("#admin-password").value = "";
    setView(true);
    loadVisits();
    loadOptions();
    loadPrivacy();
    updateCameraSetupStatus();
  } catch (error) { $("#login-error").textContent = error.message; }
  finally { button.disabled = false; }
});

$("#filters-form").addEventListener("submit", (event) => { event.preventDefault(); loadVisits(); });
$("#option-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  $("#option-error").textContent = "";
  try {
    await api("/api/admin/options", {
      method: "POST",
      body: JSON.stringify({ fieldKey: $("#option-field").value, value: $("#option-value").value }),
    });
    $("#option-value").value = "";
    loadOptions();
  } catch (error) { $("#option-error").textContent = error.message; }
  finally { button.disabled = false; }
});
$("#privacy-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  $("#privacy-error").textContent = "";
  $("#privacy-status").textContent = "";
  try {
    const data = await api("/api/admin/privacy", { method: "PUT", body: JSON.stringify(privacyPayload()) });
    applyPrivacySettings(data.privacy);
    $("#privacy-status").textContent = "Privātuma paziņojums ir saglabāts.";
  } catch (error) { $("#privacy-error").textContent = error.message; }
  finally { button.disabled = false; }
});
$("#logout-button").addEventListener("click", async () => { await api("/api/admin/logout", { method: "POST", body: "{}" }); setView(false); });
$("#visits-body").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-delete-id]");
  if (!button || !confirm("Vai tiešām dzēst šo apmeklējuma ierakstu?")) return;
  try { await api(`/api/admin/visits/${button.dataset.deleteId}`, { method: "DELETE" }); loadVisits(); }
  catch (error) { $("#dashboard-error").textContent = error.message; }
});
$("#option-groups").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-option-id]");
  if (!button || !confirm("Vai dzēst šo izvēlnes vērtību?")) return;
  try { await api(`/api/admin/options/${button.dataset.optionId}`, { method: "DELETE" }); loadOptions(); }
  catch (error) { $("#option-error").textContent = error.message; }
});

api("/api/admin/session").then((result) => { setView(result.authenticated); if (result.authenticated) { loadVisits(); loadOptions(); loadPrivacy(); updateCameraSetupStatus(); } }).catch(() => setView(false));
