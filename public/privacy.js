const content = document.querySelector("#privacy-page-content");

function isConfigured(settings) {
  return ["controllerName", "controllerContact", "legalBasis", "legalBasisDetails", "processingPurpose", "recipients", "retentionPeriod", "storageDescription"]
    .every((key) => settings?.[key]);
}

function addRow(title, text) {
  if (!text) return;
  const row = document.createElement("p");
  const heading = document.createElement("strong");
  heading.textContent = `${title}: `;
  row.append(heading, text);
  content.append(row);
}

function render(settings) {
  content.replaceChildren();
  if (!isConfigured(settings)) {
    content.textContent = "Pilno privātuma paziņojumu administrators vēl konfigurē. Pirms reģistrācijas tiek sniegta informācija par obligātu foto fiksāciju.";
    return;
  }
  const controller = [settings.controllerName, settings.controllerRegistrationNumber && `reģ. Nr. ${settings.controllerRegistrationNumber}`, settings.controllerContact].filter(Boolean).join(" · ");
  addRow("Pārzinis", controller);
  addRow("Datu aizsardzības speciālists", settings.dpoContact);
  addRow("Apstrādājamie dati", "vārds, uzvārds, personas kods, pārstāvētā iestāde vai uzņēmums, dokumenta veids un numurs, vizītes dati, reģistrācijas laiks un obligāti uzņemts foto.");
  addRow("Foto fiksācija", "Pēc personas koda ievades (ne ātrāk kā pēc trim rakstzīmēm) planšetes kamera lokāli uzņem vairākus kadrus un saglabā kvalitatīvāko foto kopā ar sekmīgi reģistrētu vizīti.");
  addRow("Mērķis", settings.processingPurpose);
  addRow("Tiesiskais pamats", `${settings.legalBasis}. ${settings.legalBasisDetails}`);
  addRow("Datu saņēmēji", settings.recipients);
  addRow("Glabāšana", `${settings.retentionPeriod}. ${settings.storageDescription}`);
  addRow("Jūsu tiesības", "Varat pieprasīt piekļuvi saviem datiem, to labošanu, dzēšanu, apstrādes ierobežošanu vai iebilst pret apstrādi, ciktāl to pieļauj piemērojamie normatīvie akti; varat iesniegt sūdzību Datu valsts inspekcijā.");
  addRow("Automatizēta lēmumu pieņemšana", "Netiek veikta.");
}

fetch("/api/config", { cache: "no-store" })
  .then((response) => response.ok ? response.json() : Promise.reject())
  .then((config) => render(config.privacy || {}))
  .catch(() => { content.textContent = "Privātuma paziņojumu pašlaik neizdevās ielādēt."; });
