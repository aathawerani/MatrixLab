#!/usr/bin/env bash
# Create a name-constrained private CA (once) and issue GitLab's server certificate.
# Run as root on the GitLab VM. The CA key never leaves the VM.
#
#   sudo bash issue-gitlab-certificate.sh gitlab.matrix-dev.internal 10.20.0.2 matrix-dev.internal 10.20.0.0/255.255.0.0
#
# Re-running reuses the existing CA and re-issues the server certificate (825 days,
# the longest validity Apple clients accept). Then run `gitlab-ctl reconfigure`.
set -euo pipefail
HOST=${1:?hostname}; IP=${2:?server IP}; DOMAIN=${3:?permitted DNS domain}; NET=${4:?permitted IP net/mask}
CA=/etc/gitlab/private-ca; SSL=/etc/gitlab/ssl
umask 077
install -d -m 700 "$CA"; install -d -m 755 "$SSL"

if [ ! -e "$CA/ca.key" ]; then
  cat > "$CA/ca.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no
[dn]
CN = Matrix Dev Private CA
[v3_ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
nameConstraints = critical,permitted;DNS:${DOMAIN},permitted;IP:${NET}
CNF
  openssl req -x509 -config "$CA/ca.cnf" -newkey rsa:4096 -sha256 -days 3650 -noenc \
    -keyout "$CA/ca.key" -out "$CA/ca.crt"
fi

printf 'subjectAltName=DNS:%s,IP:%s\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n' \
  "$HOST" "$IP" > "$CA/server.ext"
openssl req -new -newkey rsa:2048 -noenc -keyout "$SSL/$HOST.key" -out "$CA/$HOST.csr" -subj "/CN=$HOST"
openssl x509 -req -in "$CA/$HOST.csr" -CA "$CA/ca.crt" -CAkey "$CA/ca.key" -CAcreateserial \
  -days 825 -sha256 -extfile "$CA/server.ext" -out "$SSL/$HOST.crt"
chmod 644 "$CA/ca.crt" "$SSL/$HOST.crt"; chmod 600 "$CA/ca.key" "$SSL/$HOST.key"

install -d -m 755 /etc/gitlab/trusted-certs
install -m 644 "$CA/ca.crt" /etc/gitlab/trusted-certs/matrix-dev-private-ca.crt

openssl verify -CAfile "$CA/ca.crt" "$SSL/$HOST.crt"
openssl x509 -in "$SSL/$HOST.crt" -noout -subject -issuer -enddate -ext subjectAltName
openssl x509 -in "$CA/ca.crt" -noout -fingerprint -sha256
