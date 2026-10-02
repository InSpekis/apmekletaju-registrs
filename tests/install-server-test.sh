#!/usr/bin/env bash
# Instalatora vienības pārbaude: neizmanto root tiesības un nemaina sistēmas konfigurāciju.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${project_root}/scripts/install-server.sh"

test_directory="$(mktemp -d)"
trap 'rm -rf "$test_directory"' EXIT
configuration_file="${test_directory}/visitor-registry.env"
empty_configuration_file="${test_directory}/empty.env"
admin_password='Droša$Parole"Ar\simboliem`!123'
company_name='SIA Testa uzņēmums'
encoded_password=""

server_python="$(command -v python3)"
systemd_environment_value "$admin_password" encoded_password
generate_configuration_file "$configuration_file" "$admin_password" "$company_name"
validate_configuration_file "$configuration_file"

grep -qE '^VISITOR_REGISTRY_ADMIN_PASSWORD=.+$' "$configuration_file"
grep -qE '^VISITOR_REGISTRY_SESSION_SECRET=.+$' "$configuration_file"
grep -Fxq "VISITOR_REGISTRY_ADMIN_PASSWORD=${encoded_password}" "$configuration_file"

if systemd_environment_value $'nederīga\nparole' encoded_password 2>/dev/null; then
  printf 'Kļūda: rindas pārnesums parolē netika noraidīts.\n' >&2
  exit 1
fi

printf 'VISITOR_REGISTRY_ADMIN_PASSWORD=""\nVISITOR_REGISTRY_SESSION_SECRET=""\n' > "$empty_configuration_file"
if validate_configuration_file "$empty_configuration_file" 2>/dev/null; then
  printf 'Kļūda: tukša konfigurācija netika noraidīta.\n' >&2
  exit 1
fi

printf 'Instalatora konfigurācijas tests veiksmīgs.\n'
