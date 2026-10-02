const $ = (selector) => document.querySelector(selector);
const registrationForm = $("#registration-form");
const formPanel = $("#form-panel");
const successPanel = $("#success-panel");
const personalCodeInput = $("#personal-code");
const lookupNote = $("#lookup-note");
let defaultVisitedCompany = "";
let lastLookupKey = "";
let lookupTimer;
let successTimer;
let photoDataUrl = "";
let photoCapturePromise;
let photoCaptureError = "";
let activeCameraStream;
let photoCaptureTimer;
let cameraPermission = "unknown";
let privacySettings = {};
let nextRegistrationDelay = 5000;
let privacyReadingDelay = 60000;
let nextRegistrationAt = 0;
const PHOTO_CAPTURE_CODE_LENGTH = 11;
const PHOTO_CAPTURE_IDLE_DELAY = 1200;
const PHOTO_CANDIDATE_COUNT = 5;

const optionListIds = {
  representedCompany: "represented-company-options",
  documentType: "document-type-options",
  visitedCompany: "visited-company-options",
  hostPerson: "host-person-options",
  visitPurpose: "visit-purpose-options",
};

function populateOptions(options) {
  Object.entries(optionListIds).forEach(([fieldKey, listId]) => {
    const list = document.getElementById(listId);
    if (!list) return;
    list.replaceChildren(...(options?.[fieldKey] || []).map((item) => {
      const option = document.createElement("option");
      option.value = item.value;
      return option;
    }));
  });
}

function configuredPrivacy(settings) {
  return ["controllerName", "controllerContact", "legalBasis", "legalBasisDetails", "processingPurpose", "recipients", "retentionPeriod", "storageDescription"]
    .every((key) => settings?.[key]);
}

function privacySummary(settings) {
  return settings?.shortNotice || "Reģistrācijas laikā tiek apstrādāti apmeklējuma dati un uzņemts foto. Datus izmanto tikai apmeklētāju reģistram un drošības vajadzībām, nevis mārketingam vai datu tirdzniecībai.";
}

function appendPrivacyRow(container, title, text) {
  if (!text) return;
  const row = document.createElement("p");
  const heading = document.createElement("strong");
  heading.textContent = `${title}: `;
  row.append(heading, text);
  container.append(row);
}

function renderPrivacyDetails(settings) {
  const container = $("#success-privacy");
  if (!container) return;
  container.replaceChildren();
  if (!configuredPrivacy(settings)) {
    container.textContent = "Pilno privātuma paziņojumu administrators vēl konfigurē. Reģistrācijas laikā foto fiksācija tiek veikta pēc vismaz 3 personas koda rakstzīmēm.";
    return;
  }
  const controller = [settings.controllerName, settings.controllerRegistrationNumber && `reģ. Nr. ${settings.controllerRegistrationNumber}`, settings.controllerContact].filter(Boolean).join(" · ");
  appendPrivacyRow(container, "Pārzinis", controller);
  appendPrivacyRow(container, "Datu aizsardzības speciālists", settings.dpoContact);
  appendPrivacyRow(container, "Apstrādājamie dati", "vārds, uzvārds, personas kods, pārstāvētā iestāde vai uzņēmums, dokumenta veids un numurs, vizītes dati, reģistrācijas laiks un obligāti uzņemts foto.");
  appendPrivacyRow(container, "Foto fiksācija", "Pēc personas koda ievades (ne ātrāk kā pēc trim rakstzīmēm) planšetes kamera lokāli uzņem vairākus kadrus un saglabā kvalitatīvāko foto kopā ar sekmīgi reģistrētu vizīti.");
  appendPrivacyRow(container, "Mērķis", settings.processingPurpose);
  appendPrivacyRow(container, "Tiesiskais pamats", `${settings.legalBasis}. ${settings.legalBasisDetails}`);
  appendPrivacyRow(container, "Datu saņēmēji", settings.recipients);
  appendPrivacyRow(container, "Glabāšana", `${settings.retentionPeriod}. ${settings.storageDescription}`);
  appendPrivacyRow(container, "Jūsu tiesības", "Varat pieprasīt piekļuvi saviem datiem, to labošanu, dzēšanu, apstrādes ierobežošanu vai iebilst pret apstrādi, ciktāl to pieļauj piemērojamie normatīvie akti; varat iesniegt sūdzību Datu valsts inspekcijā.");
  appendPrivacyRow(container, "Automatizēta lēmumu pieņemšana", "Netiek veikta.");
}

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function stopCamera() {
  activeCameraStream?.getTracks().forEach((track) => track.stop());
  activeCameraStream = undefined;
}

function imageSharpness(context, width, height) {
  const pixels = context.getImageData(0, 0, width, height).data;
  let difference = 0;
  let samples = 0;
  for (let y = 1; y < height - 1; y += 2) {
    for (let x = 1; x < width - 1; x += 2) {
      const index = (y * width + x) * 4;
      const brightness = pixels[index] + pixels[index + 1] + pixels[index + 2];
      const right = pixels[index + 4] + pixels[index + 5] + pixels[index + 6];
      const below = pixels[index + width * 4] + pixels[index + width * 4 + 1] + pixels[index + width * 4 + 2];
      difference += Math.abs(brightness - right) + Math.abs(brightness - below);
      samples += 2;
    }
  }
  return difference / Math.max(samples, 1);
}

function captureFrame(video) {
  const sourceWidth = video.videoWidth || 640;
  const sourceHeight = video.videoHeight || 480;
  const width = Math.min(sourceWidth, 960);
  const height = Math.round(width * sourceHeight / sourceWidth);
  const photoCanvas = document.createElement("canvas");
  photoCanvas.width = width;
  photoCanvas.height = height;
  const photoContext = photoCanvas.getContext("2d", { alpha: false });
  if (!photoContext) throw new Error("Foto apstrāde šajā ierīcē nav pieejama.");
  photoContext.drawImage(video, 0, 0, width, height);

  const scoreWidth = Math.min(width, 160);
  const scoreHeight = Math.max(1, Math.round(height * scoreWidth / width));
  const scoreCanvas = document.createElement("canvas");
  scoreCanvas.width = scoreWidth;
  scoreCanvas.height = scoreHeight;
  const scoreContext = scoreCanvas.getContext("2d", { willReadFrequently: true });
  if (!scoreContext) throw new Error("Foto apstrāde šajā ierīcē nav pieejama.");
  scoreContext.drawImage(photoCanvas, 0, 0, scoreWidth, scoreHeight);
  return { score: imageSharpness(scoreContext, scoreWidth, scoreHeight), dataUrl: photoCanvas.toDataURL("image/jpeg", 0.84) };
}

async function waitForVideo(video) {
  await new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error("Kameru neizdevās palaist.")), 5000);
    video.onloadedmetadata = () => { window.clearTimeout(timeout); resolve(); };
    video.onerror = () => { window.clearTimeout(timeout); reject(new Error("Kameru neizdevās palaist.")); };
  });
  await video.play();
}

async function captureBestPhoto() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error("Šajā pārlūkā kameras fiksācija nav pieejama.");
  let video;
  try {
    activeCameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
      audio: false,
    });
    video = document.createElement("video");
    video.className = "camera-capture";
    video.muted = true;
    video.playsInline = true;
    video.srcObject = activeCameraStream;
    document.body.append(video);
    await waitForVideo(video);
    // Neuzņem pirmo kadru uzreiz: kamerai ir jāiestata ekspozīcija un fokusam jānostabilizējas.
    await wait(900);
    const candidates = [];
    for (let index = 0; index < PHOTO_CANDIDATE_COUNT; index += 1) {
      candidates.push(captureFrame(video));
      if (index < PHOTO_CANDIDATE_COUNT - 1) await wait(300);
    }
    candidates.sort((left, right) => right.score - left.score);
    photoDataUrl = candidates[0].dataUrl;
  } finally {
    video?.remove();
    stopCamera();
  }
}

function startPhotoCapture() {
  if (photoDataUrl || photoCapturePromise) return;
  photoCaptureError = "";
  photoCapturePromise = captureBestPhoto()
    .catch((error) => {
      photoCaptureError = error.message || "Kameras fiksāciju neizdevās veikt.";
      $("#registration-error").textContent = photoCaptureError;
    })
    .finally(() => { photoCapturePromise = undefined; });
}

function maybeCapturePhoto() {
  window.clearTimeout(photoCaptureTimer);
  const codeLength = personalCodeKey(personalCodeInput.value).length;
  if (codeLength < PHOTO_CAPTURE_CODE_LENGTH || cameraPermission !== "granted" || photoDataUrl || photoCapturePromise) return;
  photoCaptureTimer = window.setTimeout(() => {
    if (personalCodeKey(personalCodeInput.value).length >= PHOTO_CAPTURE_CODE_LENGTH) startPhotoCapture();
  }, PHOTO_CAPTURE_IDLE_DELAY);
}

async function checkCameraPermission() {
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraPermission = "unavailable";
    return;
  }
  if (!navigator.permissions?.query) return;
  try {
    const permissionStatus = await navigator.permissions.query({ name: "camera" });
    const updatePermission = () => { cameraPermission = permissionStatus.state; maybeCapturePhoto(); };
    updatePermission();
    permissionStatus.addEventListener("change", updatePermission);
  } catch (_) {
    // Dažās pārlūkprogrammās kameras atļaujas stāvoklis nav nolasāms.
  }
}

function resetPhotoCapture() {
  window.clearTimeout(photoCaptureTimer);
  stopCamera();
  photoDataUrl = "";
  photoCaptureError = "";
  photoCapturePromise = undefined;
}

function showForm() {
  formPanel.classList.remove("hidden");
  successPanel.classList.add("hidden");
  window.scrollTo({ top: 0, behavior: "smooth" });
  personalCodeInput.focus();
}

function scheduleNextRegistration() {
  window.clearTimeout(successTimer);
  if (successPanel.classList.contains("hidden") || document.hidden) return;
  const remaining = Math.max(0, nextRegistrationAt - Date.now());
  successTimer = window.setTimeout(() => { resetForm(); showForm(); }, remaining);
}

function noteSuccessActivity() {
  if (successPanel.classList.contains("hidden")) return;
  nextRegistrationAt = Math.max(nextRegistrationAt, Date.now() + privacyReadingDelay);
  scheduleNextRegistration();
}

function resetForm() {
  resetPhotoCapture();
  registrationForm.reset();
  $("#visited-company").value = defaultVisitedCompany;
  $("#registration-error").textContent = "";
  lookupNote.textContent = "";
  lookupNote.classList.remove("lookup-found", "lookup-missing");
  lastLookupKey = "";
}

function personalCodeKey(value) {
  return value.trim().toUpperCase().replace(/[\s-]/g, "");
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "Kaut kas neizdevās. Lūdzu, mēģiniet vēlreiz.");
    error.status = response.status;
    throw error;
  }
  return data;
}

async function lookupPreviousVisitor() {
  const enteredCode = personalCodeInput.value.trim();
  const codeKey = personalCodeKey(enteredCode);
  if (codeKey.length < 6 || codeKey === lastLookupKey) return;
  lastLookupKey = codeKey;
  lookupNote.textContent = "Meklē iepriekšējo reģistrāciju…";
  lookupNote.className = "lookup-note";
  try {
    const visitor = await request(`/api/visitors/by-personal-code/${encodeURIComponent(enteredCode)}`);
    if (personalCodeKey(personalCodeInput.value) !== codeKey) return;
    Object.entries(visitor).forEach(([key, value]) => {
      const field = registrationForm.elements.namedItem(key);
      if (field) field.value = value;
    });
    // Personas koda redzamo formātu saglabājam tādu, kādu ievadījis apmeklētājs.
    personalCodeInput.value = enteredCode;
    lookupNote.textContent = "Iepriekšējie dati ir aizpildīti. Vajadzības gadījumā tos labojiet.";
    lookupNote.className = "lookup-note lookup-found";
    registrationForm.elements.firstName.focus();
  } catch (error) {
    if (personalCodeKey(personalCodeInput.value) !== codeKey) return;
    if (error.status === 404) {
      lookupNote.textContent = "Iepriekšēja reģistrācija netika atrasta — aizpildiet pārējos laukus.";
      lookupNote.className = "lookup-note lookup-missing";
      return;
    }
    lookupNote.textContent = error.message;
    lookupNote.className = "lookup-note lookup-missing";
  }
}

personalCodeInput.addEventListener("input", () => {
  maybeCapturePhoto();
  clearTimeout(lookupTimer);
  const codeKey = personalCodeKey(personalCodeInput.value);
  if (codeKey.length < 11) {
    lastLookupKey = "";
    lookupNote.textContent = "";
    return;
  }
  lookupTimer = setTimeout(lookupPreviousVisitor, 600);
});
personalCodeInput.addEventListener("blur", lookupPreviousVisitor);

registrationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!registrationForm.reportValidity()) return;
  const button = event.submitter;
  button.disabled = true;
  $("#registration-error").textContent = "";
  try {
    if (!photoDataUrl && !photoCapturePromise) {
      const cameraMessage = cameraPermission === "denied"
        ? "Kameras piekļuve šajā planšetē ir bloķēta. Darbiniekam tā jāatļauj pārlūka vietnes iestatījumos."
        : "Kamera šajā planšetē vēl nav aktivizēta. Darbiniekam administratora panelī jāizvēlas “Aktivizēt kameru šajā ierīcē”.";
      throw new Error(cameraMessage);
    }
    await photoCapturePromise;
    if (!photoDataUrl) throw new Error(photoCaptureError || "Foto fiksācija vēl nav pabeigta. Lūdzu, uzgaidiet.");
    const payload = Object.fromEntries(new FormData(registrationForm).entries());
    payload.photo = photoDataUrl;
    const result = await request("/api/visits", { method: "POST", body: JSON.stringify(payload) });
    $("#success-personal-code").textContent = result.personalCode;
    $("#success-time").textContent = `Reģistrācijas laiks: ${result.registeredAt}`;
    formPanel.classList.add("hidden");
    successPanel.classList.remove("hidden");
    nextRegistrationAt = Date.now() + nextRegistrationDelay;
    scheduleNextRegistration();
  } catch (error) {
    $("#registration-error").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#fullscreen-button").addEventListener("click", async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (_) {
    // iOS pārlūkā pilnekrāna skatam izmantojama PWA instalācija sākuma ekrānā.
  }
});

["pointerdown", "pointermove", "touchstart", "keydown", "scroll"].forEach((eventName) => {
  document.addEventListener(eventName, noteSuccessActivity, { passive: eventName !== "keydown" });
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) window.clearTimeout(successTimer);
  else noteSuccessActivity();
});

fetch("/api/config")
  .then((response) => response.json())
  .then((config) => {
    defaultVisitedCompany = config.visitedCompany || "";
    $("#visited-company").value = defaultVisitedCompany;
    populateOptions(config.options);
    privacySettings = config.privacy || {};
    nextRegistrationDelay = Math.max(1000, Number(privacySettings.nextRegistrationSeconds || 5) * 1000);
    privacyReadingDelay = Math.max(1000, Number(privacySettings.privacyReadingSeconds || 60) * 1000);
    $("#privacy-summary").textContent = privacySummary(privacySettings);
    renderPrivacyDetails(privacySettings);
  })
  .catch(() => { defaultVisitedCompany = ""; renderPrivacyDetails({}); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js?v=4", { updateViaCache: "none" }).catch(() => {});
window.addEventListener("pagehide", stopCamera);
checkCameraPermission();
