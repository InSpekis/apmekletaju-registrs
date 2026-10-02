#!/usr/bin/env bash
# Interaktīvs instalators Linux serverim. Paroles netiek izvadītas terminālī vai žurnālā.
set -euo pipefail

if [[ "$-" == *x* ]]; then
  printf 'Drošības dēļ instalatoru nedrīkst palaist ar "bash -x": tas varētu atklāt paroli.\n' >&2
  exit 1
fi

readonly SERVICE_NAME="visitor-registry.service"
readonly SERVICE_USER="visitorregistry"
readonly APP_DIRECTORY="/opt/visitor-registry"
readonly DATA_DIRECTORY="/var/lib/visitor-registry"
readonly CONFIG_DIRECTORY="/etc/visitor-registry"
readonly CERTIFICATE_DIRECTORY="${CONFIG_DIRECTORY}/tls"
readonly CONFIG_FILE="${CONFIG_DIRECTORY}/visitor-registry.env"
readonly SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"

script_file="$(readlink -f "${BASH_SOURCE[0]}")"
source_directory="$(cd "$(dirname "$script_file")/.." && pwd)"
temporary_files=()
temporary_directories=()
server_python=""

cleanup() {
  for temporary_file in "${temporary_files[@]:-}"; do
    [[ -n "$temporary_file" && -e "$temporary_file" ]] && rm -f "$temporary_file"
  done
  for temporary_directory in "${temporary_directories[@]:-}"; do
    [[ -n "$temporary_directory" && -d "$temporary_directory" ]] && rm -rf "$temporary_directory"
  done
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
Lietošana: sudo bash scripts/install-server.sh

Instalators:
  - pārbauda Python 3.11+, OpenSSL, systemd un curl;
  - ar apstiprinājumu instalē trūkstošās pakotnes Debian/Ubuntu sistēmās;
  - pieprasa administratora paroli slēptā ievadē;
  - uzstāda sistēmas servisu, HTTPS un datu glabāšanu ārpus programmas mapes.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo -- "$script_file" "$@"
fi

confirm() {
  local answer
  read -r -p "$1 [j/N]: " answer
  [[ "$answer" =~ ^[JjYy]$ ]]
}

require_debian_packages() {
  local -a packages=("$@")
  if (( ${#packages[@]} == 0 )); then
    return
  fi
  printf 'Trūkstošās pakotnes: %s\n' "${packages[*]}"
  if ! confirm "Vai instalēt tās ar apt-get?"; then
    printf 'Instalācija pārtraukta. Uzstādiet nepieciešamās pakotnes un palaidiet instalatoru atkārtoti.\n' >&2
    exit 1
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    printf 'Automātiska pakotņu instalēšana šajā Linux distributīvā nav atbalstīta. Nepieciešams: %s\n' "${packages[*]}" >&2
    exit 1
  fi
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install --yes "${packages[@]}"
}

check_prerequisites() {
  local -a packages=()
  if ! usable_python python3; then
    packages+=(python3.11)
  fi
  command -v openssl >/dev/null 2>&1 || packages+=(openssl)
  command -v systemctl >/dev/null 2>&1 || packages+=(systemd)
  command -v curl >/dev/null 2>&1 || packages+=(curl)
  command -v tar >/dev/null 2>&1 || packages+=(tar)
  require_debian_packages "${packages[@]}"
  if usable_python python3; then
    server_python="$(command -v python3)"
  elif usable_python python3.11; then
    server_python="$(command -v python3.11)"
  else
    printf 'Nepieciešams Python 3.11 vai jaunāks. Pārbaudiet sistēmas pakotņu repozitoriju un palaidiet instalatoru atkārtoti.\n' >&2
    exit 1
  fi
  [[ -d /run/systemd/system ]] || { printf 'Šai ierīcei nav palaista systemd servisu vide.\n' >&2; exit 1; }
}

usable_python() {
  local candidate="$1"
  command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
}

read_required() {
  local prompt="$1"
  local value=""
  while [[ -z "$value" ]]; do
    read -r -p "$prompt: " value
  done
  printf '%s' "$value"
}

read_admin_password() {
  local first_password=""
  local second_password=""
  while true; do
    IFS= read -r -s -p 'Administrācijas parole (vismaz 12 simboli): ' first_password
    printf '\n'
    IFS= read -r -s -p 'Atkārtojiet administrācijas paroli: ' second_password
    printf '\n'
    if [[ "$first_password" != "$second_password" ]]; then
      printf 'Paroles nesakrīt. Mēģiniet vēlreiz.\n' >&2
      continue
    fi
    if (( ${#first_password} < 12 )); then
      printf 'Parolei jābūt vismaz 12 simbolus garai.\n' >&2
      continue
    fi
    printf '%s' "$first_password"
    return
  done
}

valid_dns_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ || "$1" == "localhost" ]]
}

valid_ip_address() {
  [[ -z "$1" || "$1" =~ ^[0-9A-Fa-f:.]+$ ]]
}

install_application_files() {
  if [[ -e "$APP_DIRECTORY/server.py" ]] && ! confirm "Esoša programma mapē ${APP_DIRECTORY} tiks atjaunināta, saglabājot datus mapē ${DATA_DIRECTORY}. Turpināt?"; then
    exit 1
  fi
  install -d -m 755 -o root -g root "$APP_DIRECTORY"
  tar --exclude-vcs --exclude='./.env' --exclude='./data' --exclude='./certs' --exclude='./__pycache__' \
    -C "$source_directory" -cf - . | tar -C "$APP_DIRECTORY" -xf -
  chown -R root:root "$APP_DIRECTORY"
  chmod -R go-w "$APP_DIRECTORY"
  install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIRECTORY"
  install -d -m 750 -o root -g "$SERVICE_USER" "$CERTIFICATE_DIRECTORY"
}

prepare_service_user() {
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/${SERVICE_USER}" --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
}

copy_certificate_pair() {
  local certificate_source="$1"
  local private_key_source="$2"
  [[ -r "$certificate_source" && -r "$private_key_source" ]] || { printf 'Sertifikāta vai privātās atslēgas fails nav nolasāms.\n' >&2; exit 1; }
  install -o root -g "$SERVICE_USER" -m 640 "$certificate_source" "$CERTIFICATE_DIRECTORY/server.crt"
  install -o root -g "$SERVICE_USER" -m 640 "$private_key_source" "$CERTIFICATE_DIRECTORY/server.key"
}

confirm_certificate_replacement() {
  if [[ -e "$CERTIFICATE_DIRECTORY/server.crt" || -e "$CERTIFICATE_DIRECTORY/server.key" ]]; then
    confirm "Esošais HTTPS sertifikāts mapē ${CERTIFICATE_DIRECTORY} tiks aizstāts. Turpināt?" || exit 1
  fi
}

create_test_certificate() {
  local dns_name="$1"
  local ip_address="$2"
  local san_list="DNS:localhost,IP:127.0.0.1,DNS:${dns_name}"
  local generation_directory
  [[ -n "$ip_address" ]] && san_list+=",IP:${ip_address}"
  generation_directory="$(mktemp -d "${CERTIFICATE_DIRECTORY}/.certificate.XXXXXX")"
  temporary_directories+=("$generation_directory")
  VISITOR_REGISTRY_CERTIFICATE_SANS="$san_list" \
    bash "$APP_DIRECTORY/scripts/generate-local-certificate.sh" "$generation_directory"
  copy_certificate_pair "$generation_directory/localhost.crt" "$generation_directory/localhost.key"
}

configure_acme_certificate() {
  local dns_name="$1"
  local email_address="$2"
  require_debian_packages certbot
  if ! confirm "Certbot sazināsies ar Let's Encrypt un pieprasa publiski sasniedzamu DNS nosaukumu ${dns_name} un TCP 80. portu. Turpināt?"; then
    exit 1
  fi
  certbot certonly --standalone --non-interactive --agree-tos --email "$email_address" --domains "$dns_name"
  copy_certificate_pair "/etc/letsencrypt/live/${dns_name}/fullchain.pem" "/etc/letsencrypt/live/${dns_name}/privkey.pem"
  if systemctl list-unit-files certbot.timer --no-legend 2>/dev/null | grep -q '^certbot.timer'; then
    systemctl enable --now certbot.timer
  fi

  local hook_file="/etc/letsencrypt/renewal-hooks/deploy/visitor-registry.sh"
  install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    "install -o root -g ${SERVICE_USER} -m 640 \"\${RENEWED_LINEAGE}/fullchain.pem\" \"${CERTIFICATE_DIRECTORY}/server.crt\"" \
    "install -o root -g ${SERVICE_USER} -m 640 \"\${RENEWED_LINEAGE}/privkey.pem\" \"${CERTIFICATE_DIRECTORY}/server.key\"" \
    "systemctl try-restart ${SERVICE_NAME}" > "$hook_file"
  chmod 700 "$hook_file"
}

configure_certificate() {
  local dns_name="$1"
  local ip_address="$2"
  local mode=""
  printf '\nHTTPS sertifikāta veids:\n'
  printf '  1) Uzņēmuma sertifikāts (ieteicams iekšējam tīklam ar centralizēti uzticamiem klientiem)\n'
  printf '  2) Let\047s Encrypt (automātiski; nepieciešams publisks DNS un TCP 80)\n'
  printf '  3) Pašparakstīts testa sertifikāts (katrai klienta ierīcei manuāli jāuztic sertifikāts)\n'
  while [[ ! "$mode" =~ ^[123]$ ]]; do
    read -r -p 'Izvēle [1-3]: ' mode
  done
  confirm_certificate_replacement

  case "$mode" in
    1)
      local certificate_source private_key_source
      certificate_source="$(read_required 'Uzņēmuma sertifikāta pilnais ceļš')"
      private_key_source="$(read_required 'Privātās atslēgas pilnais ceļš')"
      copy_certificate_pair "$certificate_source" "$private_key_source"
      ;;
    2)
      local email_address
      email_address="$(read_required 'Let\047s Encrypt paziņojumu e-pasts')"
      configure_acme_certificate "$dns_name" "$email_address"
      ;;
    3)
      if ! confirm "Pašparakstītam sertifikātam katrā Surface ierīcē būs jāveic manuāla uzticēšana. Turpināt?"; then
        exit 1
      fi
      create_test_certificate "$dns_name" "$ip_address"
      ;;
  esac
}

systemd_environment_value() {
  local value="$1"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || { printf 'Konfigurācijas vērtībā nedrīkst būt rindas pārnesums.\n' >&2; exit 1; }
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\\$}"
  value="${value//\`/\\\`}"
  printf '"%s"' "$value"
}

write_configuration() {
  local admin_password="$1"
  local company_name="$2"
  local temporary_config
  temporary_config="$(mktemp)"
  temporary_files+=("$temporary_config")
  umask 077
  {
    printf 'VISITOR_REGISTRY_DB=%s\n' "$(systemd_environment_value "${DATA_DIRECTORY}/visitor_registry.sqlite3")"
    printf 'VISITOR_REGISTRY_HOST=0.0.0.0\n'
    printf 'VISITOR_REGISTRY_PORT=8443\n'
    printf 'VISITOR_REGISTRY_ADMIN_PASSWORD=%s\n' "$(systemd_environment_value "$admin_password")"
    printf 'VISITOR_REGISTRY_SESSION_SECRET=%s\n' "$(systemd_environment_value "$("$server_python" -c 'import secrets; print(secrets.token_urlsafe(48))')")"
    printf 'VISITOR_REGISTRY_COMPANY=%s\n' "$(systemd_environment_value "$company_name")"
    printf 'VISITOR_REGISTRY_TIMEZONE=Europe/Riga\n'
    printf 'VISITOR_REGISTRY_TLS_CERTIFICATE=%s\n' "$(systemd_environment_value "${CERTIFICATE_DIRECTORY}/server.crt")"
    printf 'VISITOR_REGISTRY_TLS_PRIVATE_KEY=%s\n' "$(systemd_environment_value "${CERTIFICATE_DIRECTORY}/server.key")"
    printf 'VISITOR_REGISTRY_SECURE_COOKIES=true\n'
    printf 'VISITOR_REGISTRY_RETENTION_SWEEP_SECONDS=3600\n'
  } > "$temporary_config"
  install -o root -g "$SERVICE_USER" -m 640 "$temporary_config" "$CONFIG_FILE"
}

install_service() {
  local temporary_service
  temporary_service="$(mktemp)"
  temporary_files+=("$temporary_service")
  sed "s|^ExecStart=.*|ExecStart=${server_python} ${APP_DIRECTORY}/server.py|" \
    "$APP_DIRECTORY/systemd/visitor-registry-system.service.example" > "$temporary_service"
  install -m 644 "$temporary_service" "$SERVICE_FILE"
  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME"
  systemctl is-active --quiet "$SERVICE_NAME"
}

main() {
  check_prerequisites
  prepare_service_user
  install_application_files

  printf '\nAizpildiet servera konfigurāciju. Paroles ievade netiks parādīta ekrānā.\n'
  local company_name dns_name ip_address admin_password
  company_name="$(read_required 'Pārziņa juridiskais nosaukums')"
  dns_name="$(read_required 'Servera DNS nosaukums')"
  if ! valid_dns_name "$dns_name"; then
    printf 'DNS nosaukums nav derīgs.\n' >&2
    exit 1
  fi
  read -r -p 'Servera LAN IP adrese (nav obligāta, bet ieteicama sertifikātam): ' ip_address
  if ! valid_ip_address "$ip_address"; then
    printf 'IP adreses formāts nav derīgs.\n' >&2
    exit 1
  fi
  admin_password="$(read_admin_password)"
  configure_certificate "$dns_name" "$ip_address"
  write_configuration "$admin_password" "$company_name"
  unset admin_password
  install_service

  printf '\nInstalācija pabeigta. Servisa statuss:\n'
  systemctl --no-pager --full status "$SERVICE_NAME"
  printf '\nReģistrācijas adrese: https://%s:8443/\n' "$dns_name"
  printf 'Administrācijas adrese: https://%s:8443/admin\n' "$dns_name"
  printf 'Pirms Surface nodošanas pārbaudiet sertifikāta uzticamību, kameras atļauju un pilnu reģistrācijas plūsmu.\n'
}

main
