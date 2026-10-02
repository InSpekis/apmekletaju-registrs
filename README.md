# Apmeklētāju reģistrs

Planšetei pielāgots apmeklētāju reģistrs latviešu valodā. Planšete ir tikai reģistrācijas klients: visi ieraksti glabājas SQLite SQL datubāzē uz servera, nevis planšetē vai pārlūkprogrammā.

## Dokumentācija

- Pilna servera, HTTPS sertifikātu, systemd, Surface un atjaunināšanas kārtība: [INSTALLATION.md](INSTALLATION.md)
- Pirms nodošanas ekspluatācijā izlasiet instrukcijas sadaļu **GDPR un personas datu aizsardzība**. Sistēma apstrādā personas kodus, dokumentu datus un fotoattēlus.

## Servera instalācija

Debian/Ubuntu serverī ieteicams izmantot interaktīvo instalatoru:

```bash
sudo bash scripts/install-server.sh
```

Tas slēptā ievadē prasa administratora paroli, pārbauda nepieciešamās pakotnes un, tikai pēc apstiprinājuma, instalē trūkstošās pakotnes. Instalatorā izvēlieties uzņēmuma sertifikātu, ja Surface ierīcēm jāuzticas HTTPS bez manuālas sertifikāta importēšanas; Let's Encrypt der tikai publiski sasniedzamam DNS vārdam. Pilna kārtība, sertifikātu nosacījumi un drošības prasības ir [INSTALLATION.md](INSTALLATION.md#ātrā-instalācija-ar-interaktīvo-instalatoru).

## Iespējas

- jauna apmeklētāja reģistrācija ar vārdu, uzvārdu, personas kodu, pārstāvēto iestādi, dokumentu, apmeklējamo uzņēmumu, kontaktpersonu un vizītes mērķi;
- serveris automātiski ieraksta datumu un laiku (`Europe/Riga`);
- personas kods ir vienīgais apmeklētāja identifikators — lietotne neveido papildu kodu un neprasa tālruņa numuru;
- ievadot iepriekš reģistrētu personas kodu, dati tiek automātiski aizpildīti ar pēdējās vizītes informāciju, un tos var labot;
- aizsargāts administrācijas panelis (`/admin`), datumu/meklēšanas filtrs, CSV eksports un ieraksta dzēšana;
- pastāvīga privātuma/GDPR informācija reģistrācijas skatā;
- administratora ievadāms īsais un pilnais privātuma/GDPR paziņojums, glabāšanas termiņš un ekrāna neaktivitātes laiki;
- obligāta planšetes kameras foto fiksācija pēc personas koda ievades (ne ātrāk kā pēc trim rakstzīmēm); planšete uzņem piecus kandidātkadrus un datubāzē saglabā tikai kvalitatīvāko pilno kameras kadru;
- CSV atskaite un filtrēta ZIP atskaite ar CSV reģistru un foto failiem atsevišķā `foto/` mapē;
- administratora noteikts datu glabāšanas termiņš ar noklusējumu 6 mēneši; samazinot termiņu, dati tiek dzēsti uzreiz, un serveris veic termiņdatu pārbaudi katru stundu;
- PWA manifests un pilnekrāna poga Surface/Android ierīcēm.

## Palaišana lokāli

Nepieciešams Python 3.11 vai jaunāks. Pirms palaišanas iestatiet administrācijas paroli un sesijas atslēgu.

```bash
export VISITOR_REGISTRY_ADMIN_PASSWORD='izveidojiet-garu-uniku-paroli'
export VISITOR_REGISTRY_SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export VISITOR_REGISTRY_COMPANY='SIA Piemērs'
python3 server.py
```

Atveriet `http://localhost:8080`. Administrācijas panelis ir `http://localhost:8080/admin`. Ražošanas vidē šo pašu adresi atver planšete un administratora darba dators, taču datubāzes fails paliek tikai uz servera.

## HTTPS un kamera

Kameras pieprasījumam planšetē izmantojiet HTTPS. Šai ierīcei var izveidot lokālu attīstības sertifikātu:

```bash
bash scripts/generate-local-certificate.sh
export VISITOR_REGISTRY_PORT=8443
export VISITOR_REGISTRY_TLS_CERTIFICATE="$PWD/certs/localhost.crt"
export VISITOR_REGISTRY_TLS_PRIVATE_KEY="$PWD/certs/localhost.key"
export VISITOR_REGISTRY_SECURE_COOKIES=true
python3 server.py
```

Atveriet `https://localhost:8443`. Lokāls pašparakstīts sertifikāts jāapstiprina vai jāuztic konkrētajā ierīcē. Ražošanā izmantojiet uzņēmuma iekšējās sertifikātu iestādes vai publiski uzticamu sertifikātu. Darbinieks šajā pašā planšetē atver `/admin` un sadaļā **Kameras atļauja** vienreiz nospiež **Aktivizēt kameru šajā ierīcē**; pārlūks parāda savu atļaujas dialogu. Pēc apstiprināšanas, kad personas koda lauks ir pabeigts, planšete pēc īsas pauzes uzņem piecus kandidātkadrus, ļauj fokusam nostabilizēties un saglabā tikai kvalitatīvāko kadru.

Datubāze pēc noklusējuma tiek izveidota `data/visitor_registry.sqlite3`. Rezerves kopēšanai apturiet serveri un nokopējiet šo failu. Pirms ražošanas izvietošanas lietojiet regulāras šifrētas rezerves kopijas un nosakiet uzņēmuma datu glabāšanas termiņu.

## Pastāvīga palaišana šajā datorā

Šajā datorā pievienotais `systemd/visitor-registry.service` lietotāja serviss automātiski pārstartē reģistru pēc kļūmes. Tas izmanto lokālo, ar Git neversēto `.env` konfigurāciju. Pēc servisa piesaistīšanas tas ir pieejams arī tad, kad terminālis ir aizvērts.

## Uzstādīšana uz Surface planšetes

1. Serveri izvietojiet uzticamā iekšējā tīklā ar HTTPS. Iestatiet `VISITOR_REGISTRY_SECURE_COOKIES=true`.
2. Surface ierīcē atveriet reģistrācijas adresi Microsoft Edge.
3. Izvēlieties **… → Apps → Install this site as an app**. Atvērtā lietotne darbojas bez parastajām pārlūka joslām. Var izmantot arī lapas pilnekrāna pogu.
4. Publiskajā reģistrācijas planšetē neatveriet `/admin`; administrācijas panelim izmantojiet tikai pilnvarotu darba datoru.

## Android un iPad/iPhone nākotnē

Lietotne ir responsīva un tai ir PWA manifests. Android pārlūkā var izvēlēties **Install app / Pievienot sākuma ekrānam**. iPad/iPhone Safari izvēlieties **Share → Add to Home Screen**; tas atver reģistru lietotnei līdzīgā skatā. iOS pašlaik neļauj tīmekļa lapām droši piespiest pilnekrāna režīmu, tādēļ šis ir ieteicamais risinājums.

## GDPR un datu aizsardzība

Persona, kas pati ievada savus datus, ar to nepārnes atbildību uz sevi. Uzņēmums, kas nosaka reģistra mērķus un līdzekļus, ir datu pārzinis un atbild par apstrādes tiesiskumu, pārredzamību, drošību un glabāšanas termiņu. Tas attiecas arī uz fotoattēliem un jebkuru datu nodošanu citai iestādei.

Pirms ekspluatācijas uzņēmumam jāapstiprina konkrēts tiesiskais pamats un mērķis, jāaizpilda administrācijas panelī redzamais īsais un pilnais paziņojums, jāierobežo administratoru piekļuve, jānosaka rezerves kopiju kārtība un jāizveido datu subjektu pieprasījumu un incidentu apstrādes kārtība. Izvērtējiet arī DPIA nepieciešamību. Plašāks kontrolsaraksts un oficiālas atsauces ir [INSTALLATION.md](INSTALLATION.md#9-gdpr-un-personas-datu-aizsardzība).

Lietotne neglabā personas datus pārlūka `localStorage`, API un administrācijas atbildes netiek kešotas, administrācijas sesija ir `HttpOnly`/`SameSite` sīkdatne, un dzēšanas savienojumiem ir ieslēgta SQLite drošā dzēšana. Šie tehniskie pasākumi neaizstāj pārziņa juridiskos, organizatoriskos un fiziskās drošības pienākumus.
