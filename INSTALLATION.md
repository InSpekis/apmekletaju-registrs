# Uzstādīšanas un ekspluatācijas instrukcija

Šī instrukcija paredzēta sistēmas administratoram. Planšete ir tikai reģistrācijas klients; SQL datubāze, sertifikāti un administrācijas piekļuve atrodas serverī.

## 1. Pirms uzstādīšanas

Nepieciešams:

- Linux serveris ar Python 3.11 vai jaunāku, `openssl`, `git` un `systemd`;
- pastāvīga servera IP adrese vai iekšējs DNS nosaukums;
- TCP 8443 pieejamība tikai no paredzētā iekšējā tīkla;
- uzņēmuma apstiprināts pārzinis, tiesiskais pamats, glabāšanas termiņš un saņēmēju loks;
- HTTPS sertifikāts, kam uzticas visas reģistrācijas planšetes.

Piemērā tiek izmantots servera nosaukums `registry.example.local`, IP `192.168.100.198` un lietotājs `visitorregistry`. Aizstājiet tos ar saviem faktiskajiem parametriem.

## Ātrā instalācija ar interaktīvo instalatoru

Debian vai Ubuntu serverī no iegūtās programmas mapes izpildiet:

```bash
sudo bash scripts/install-server.sh
```

Instalators pirms darbu sākšanas pārbauda `Python 3.11+`, `OpenSSL`, `systemd`, `curl` un `tar`. Ja kāda pakotne trūkst, tas nosauc pakotnes un prasa apstiprinājumu, pirms izmanto `apt-get` to uzstādīšanai. Instalators neveic neatgriezeniskas izmaiņas bez apstiprinājuma; esošu programmu vai HTTPS sertifikātu tas aizstāj tikai pēc atsevišķa jautājuma.

Administrācijas parole tiek ievadīta divreiz bez rakstzīmju attēlošanas. Tā netiek nodota kā komandrindas parametrs un instalēšanas laikā netiek izvadīta terminālī. Pēc tam tā tiek glabāta tikai servera konfigurācijas failā `/etc/visitor-registry/visitor-registry.env` ar piekļuvi `root` un `visitorregistry` grupai. Nelietojiet instalatoru ar `bash -x`, nelīmējiet paroli biļetēs vai čatā un pēc pirmās pieslēgšanās nomainiet paroli, ja tā varēja būt atklāta.

Instalators jautā arī pārziņa juridisko nosaukumu, servera DNS vārdu un izvēlēto HTTPS sertifikāta veidu.

- **Uzņēmuma sertifikāts** ir ieteicamais risinājums slēgtam tīklam. Izvēlieties to, ja uzņēmuma iekšējā sertifikātu iestāde vai ierīču pārvaldība (AD/MDM) jau automātiski uztic saknes sertifikātu Surface ierīcēm. Tad sertifikāts nav manuāli jāimportē katrā planšetē.
- **Let's Encrypt** instalators pieprasa automātiski ar Certbot un ieslēdz tā atjaunošanas taimeri. Tas ir iespējams tikai tad, ja DNS vārds ir publiski atrisināms un Let's Encrypt validācijai no interneta ir pieejams servera TCP 80. ports. Tas nedarbojas ar `localhost`, privātu IP adresi vai tikai iekšējam tīklam pieejamu nosaukumu.
- **Pašparakstīts testa sertifikāts** tiek izveidots automātiski, tomēr tas jāuztic katrā klienta ierīcē. Tas nav risinājums, ja mērķis ir izvairīties no manuālas sertifikātu uzticēšanas.

Pēc instalācijas atveriet norādīto `https://servera-nosaukums:8443/` adresi un pārbaudiet `sudo systemctl status visitor-registry.service`.

## 2. Servera lietotājs un programma

Instalējiet pamatpakotnes un izveidojiet lietotāju, kurš drīkst piekļūt tikai šai lietotnei:

```bash
sudo apt update
sudo apt install --yes git python3 openssl
sudo useradd --system --create-home --home-dir /opt/visitor-registry --shell /usr/sbin/nologin visitorregistry
sudo git clone https://github.com/InSpekis/apmekletaju-registrs.git /opt/visitor-registry
sudo chown -R root:root /opt/visitor-registry
sudo chmod -R go-w /opt/visitor-registry
sudo install -d -m 700 -o visitorregistry -g visitorregistry /var/lib/visitor-registry
sudo install -d -m 750 -o root -g visitorregistry /etc/visitor-registry/tls
```

Ja repozitorijs nav publisks, izmantojiet uzņēmuma piekļuves metodi, piemēram, izvietošanas atslēgu vai iekšēju Git serveri. Neizmantojiet darbinieka personisko paroli vai atslēgu servera pastāvīgā konfigurācijā.

## 3. HTTPS sertifikāts

Ražošanas vidē izmantojiet uzņēmuma sertifikātu iestādes sertifikātu vai citu klientu uzticamu sertifikātu. Sertifikāta `Subject Alternative Name` laukā jābūt reālajam DNS nosaukumam un, ja planšetes pieslēdzas ar IP adresi, arī šai IP adresei.

Nokopējiet sertifikātu un privāto atslēgu šādās vietās:

```text
/etc/visitor-registry/tls/server.crt
/etc/visitor-registry/tls/server.key
```

Privātajai atslēgai jābūt pieejamai tikai `root` un grupai `visitorregistry`:

```bash
sudo chown root:visitorregistry /etc/visitor-registry/tls/server.crt /etc/visitor-registry/tls/server.key
sudo chmod 640 /etc/visitor-registry/tls/server.crt /etc/visitor-registry/tls/server.key
```

### Iekšēja testa sertifikāta izveide

Pašparakstīts sertifikāts ir piemērots tikai kontrolētai iekšējai videi, ja to manuāli uztic visās Surface ierīcēs. Izveidojiet to ar faktiskajiem servera nosaukumiem un IP adresēm:

```bash
cd /opt/visitor-registry
sudo env \
  VISITOR_REGISTRY_CERTIFICATE_SANS='DNS:localhost,IP:127.0.0.1,DNS:registry.example.local,IP:192.168.100.198' \
  bash scripts/generate-local-certificate.sh certs

sudo install -o root -g visitorregistry -m 640 certs/localhost.crt /etc/visitor-registry/tls/server.crt
sudo install -o root -g visitorregistry -m 640 certs/localhost.key /etc/visitor-registry/tls/server.key
```

Planšetē sertifikātu uztic konkrētajam Edge profilam vai Windows sertifikātu krātuvei. Nepieņemiet pārlūka sertifikāta brīdinājumu kā pastāvīgu risinājumu un neuzticiet nezināmas izcelsmes sertifikātus.

## 4. Konfigurācija

Izveidojiet konfidenciālu konfigurācijas failu:

```bash
sudo install -m 640 -o root -g visitorregistry /dev/null /etc/visitor-registry/visitor-registry.env
sudoedit /etc/visitor-registry/visitor-registry.env
```

Ievadiet šādu konfigurāciju, aizstājot paroli, sesijas atslēgu, uzņēmuma nosaukumu un sertifikāta ceļus:

```text
VISITOR_REGISTRY_DB=/var/lib/visitor-registry/visitor_registry.sqlite3
VISITOR_REGISTRY_HOST=0.0.0.0
VISITOR_REGISTRY_PORT=8443
VISITOR_REGISTRY_ADMIN_PASSWORD=ievadiet-garu-un-uniku-paroli
VISITOR_REGISTRY_SESSION_SECRET=ievadiet-vismaz-48-simbolu-nejausu-slepeno-atslegu
VISITOR_REGISTRY_COMPANY=Jūsu uzņēmuma juridiskais nosaukums
VISITOR_REGISTRY_TIMEZONE=Europe/Riga
VISITOR_REGISTRY_TLS_CERTIFICATE=/etc/visitor-registry/tls/server.crt
VISITOR_REGISTRY_TLS_PRIVATE_KEY=/etc/visitor-registry/tls/server.key
VISITOR_REGISTRY_SECURE_COOKIES=true
VISITOR_REGISTRY_RETENTION_SWEEP_SECONDS=3600
```

Sesijas atslēgu var izveidot ar:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Nekad neiekļaujiet šo failu Git repozitorijā, e-pastā vai atskaitēs.

## 5. Systemd serviss

Nokopējiet sistēmas servisa paraugu un palaidiet to:

```bash
sudo install -m 644 /opt/visitor-registry/systemd/visitor-registry-system.service.example /etc/systemd/system/visitor-registry.service
sudo systemctl daemon-reload
sudo systemctl enable --now visitor-registry.service
sudo systemctl status visitor-registry.service
```

Pārbaudiet žurnālu, ja serviss nepalaižas:

```bash
sudo journalctl -u visitor-registry.service --since today --no-pager
```

Atļaujiet 8443. portu tikai nepieciešamajam apakštīklam. Piemērs ar UFW:

```bash
sudo ufw allow from 192.168.100.0/24 to any port 8443 proto tcp
```

Pārbaudiet serveri no uzticama datora, izmantojot sertifikātu, nevis ignorējot sertifikāta pārbaudi:

```bash
curl --fail --cacert /etc/visitor-registry/tls/server.crt https://registry.example.local:8443/api/config
```

## 6. Surface planšetes

1. Surface pieslēdziet tam pašam iekšējam tīklam.
2. Importējiet uzņēmuma vai iekšējo sertifikātu Windows uzticamo sertifikātu krātuvē.
3. Atveriet `https://registry.example.local:8443/` ar Microsoft Edge.
4. Sadaļā **More tools → Apps** izvēlieties **Install this site as an app**.
5. Pirmajā iestatīšanā tajā pašā Edge profilā atveriet `/admin`, piesakieties un sadaļā **Kameras atļauja** piešķiriet kameras piekļuvi.
6. Windows sadaļā **Privacy & security → Camera** ieslēdziet piekļuvi kamerai un darbvirsmas lietotnēm.
7. Administrācijas paneli neglabājiet publiskajā planšetē pēc iestatīšanas; izmantojiet atsevišķu pilnvarotu administratora ierīci.

PWA atvēršanai joprojām vajadzīgs tīkla savienojums ar serveri: personas dati netiek saglabāti planšetē bezsaistē.

## 7. Datu glabāšana, dzēšana un rezerves kopijas

Noklusējuma glabāšanas termiņš ir **6 mēneši**. Administrators to maina sadaļā **Privātuma paziņojums → Glabāšanas termiņš (mēneši)**.

- Ja termiņu samazina, neatbilstošie apmeklējumi, tiem piesaistītie foto un vairs neizmantotie apmeklētāju profili tiek dzēsti uzreiz.
- Neatkarīgs servera process termiņdatus pārbauda ik stundu arī tad, ja sistēmā neviens nereģistrējas.
- SQLite dzēšanas savienojumiem ir ieslēgta drošā dzēšana.
- Rezerves kopijām jāpiemēro tas pats glabāšanas termiņš, piekļuves ierobežojumi un dzēšanas kārtība. Sistēmas dzēšana nevar automātiski izdzēst jau nokopētu ārēju rezerves kopiju vai nosūtītu ZIP atskaiti.

Rezerves kopiju veidojiet tikai ar apturētu servisu vai ar SQLite konsekventu dublēšanas procedūru:

```bash
sudo systemctl stop visitor-registry.service
sudo install -d -m 700 -o root -g root /srv/secure-backups/visitor-registry
sudo cp /var/lib/visitor-registry/visitor_registry.sqlite3 /srv/secure-backups/visitor-registry/
sudo systemctl start visitor-registry.service
```

Šifrējiet rezerves kopijas un glabājiet tās atsevišķi no servera. Regulāri pārbaudiet atjaunošanas procedūru.

## 8. Atjaunināšana

Pirms atjaunināšanas izveidojiet rezerves kopiju. Dati ir ārpus programmas mapes, tāpēc avota koda atjaunināšana tos nepārraksta.

```bash
sudo systemctl stop visitor-registry.service
cd /opt/visitor-registry
sudo git pull --ff-only
python3 -m py_compile server.py
sudo systemctl start visitor-registry.service
sudo systemctl status visitor-registry.service
```

## 9. GDPR un personas datu aizsardzība

Šī sistēma apstrādā personas kodus, dokumentu datus, vizīšu informāciju un fotoattēlus. Persona, kas šos datus ievada pati, ar to neatbrīvo uzņēmumu no atbildības. Uzņēmums, kas nosaka apstrādes mērķus un līdzekļus, ir datu pārzinis un atbild par atbilstību.

Pirms ekspluatācijas pārzinim ar datu aizsardzības speciālistu vai juristu jānodrošina vismaz šādi pasākumi:

1. Jānosaka un jādokumentē konkrēts apstrādes mērķis un katrai apstrādei atbilstošs tiesiskais pamats. Norāde uz kritiskās infrastruktūras drošību pati par sevi neaizstāj normatīvā akta vai leģitīmo interešu izvērtējumu.
2. Pirms datu ievades jāsniedz skaidrs paziņojums: pārzinis un kontakts, mērķis, tiesiskais pamats, datu kategorijas, saņēmēji, termiņš, tiesības un pilnā paziņojuma atrašanās vieta. Administrācijas panelī ievadiet reālu, jurista apstiprinātu īso un pilno paziņojumu.
3. Jāizvērtē nepieciešamība katram laukam un foto fiksācijai; nedrīkst vākt datus “katram gadījumam”. Fotoattēls ir personas dati, un tā saņemšana, glabāšana vai nodošana ir datu apstrāde.
4. Jāierobežo piekļuve administratoriem, jāizmanto individuāli konti vai cits piekļuves kontroles mehānisms, jāaizsargā serveris, rezerves kopijas un ZIP atskaites ar šifrēšanu un fizisku piekļuves kontroli.
5. Pirms foto vai datu nodošanas iestādei jābūt pārbaudītam tiesiskajam pamatam, saņēmējam, pilnvarojumam un drošam nodošanas kanālam. Eksporta arhīvus nedrīkst sūtīt ar neaizsargātu e-pastu vai glabāt nekontrolētās koplietotās mapēs.
6. Jābūt kārtībai piekļuves, labošanas, dzēšanas, ierobežošanas un iebildumu pieprasījumu apstrādei, kā arī incidentu un datu aizsardzības pārkāpumu izvērtēšanai.
7. Jāizvērtē, vai plānotā apstrāde rada augstu risku un vai nepieciešams ietekmes uz datu aizsardzību novērtējums (DPIA). Šo izvērtējumu neveic programmatūra uzņēmuma vietā.

VDAR 5. pants nosaka, ka datiem jābūt apstrādātiem likumīgi, godprātīgi un pārredzami, tikai konkrētiem mērķiem, minimālā apjomā, ar ierobežotu glabāšanu un pienācīgu drošību. VDAR 13. pants nosaka informēšanu datu iegūšanas brīdī; 25. un 32. pants attiecas uz datu aizsardzību pēc noklusējuma un apstrādes drošību. Skatiet [VDAR oficiālo tekstu](https://eur-lex.europa.eu/legal-content/LV/TXT/?uri=CELEX:32016R0679) un [Datu valsts inspekcijas informāciju par videonovērošanu](https://www.dvi.gov.lv/lv/videonoverosana).

Šī instrukcija nav juridisks atzinums. Pirms reālas lietošanas uzņēmumam jāsaņem savs juridiskais un datu aizsardzības izvērtējums.
