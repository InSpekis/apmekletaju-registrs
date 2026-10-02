#!/usr/bin/env bash
set -euo pipefail

certificate_directory="${1:-certs}"
certificate_file="${certificate_directory}/localhost.crt"
private_key_file="${certificate_directory}/localhost.key"
certificate_sans="${VISITOR_REGISTRY_CERTIFICATE_SANS:-DNS:localhost,IP:127.0.0.1}"

if [[ -e "$certificate_file" || -e "$private_key_file" ]]; then
  printf 'Sertifikāts vai privātā atslēga jau pastāv: %s\n' "$certificate_directory" >&2
  exit 1
fi

mkdir -p "$certificate_directory"
openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 365 \
  -keyout "$private_key_file" \
  -out "$certificate_file" \
  -subj '/CN=localhost' \
  -addext "subjectAltName=${certificate_sans}"
chmod 600 "$private_key_file"

printf 'Izveidots lokālais sertifikāts: %s\n' "$certificate_file"
