"""A throwaway CA so the simulator will trust a local mock server over HTTPS.

`xcrun simctl keychain <udid> add-root-cert` installs the CA into the device's
trust store, which is what lets Get Contents of URL talk to localhost without
certificate errors. iOS refuses leaf certificates without a subjectAltName, an
extendedKeyUsage of serverAuth, or with a lifetime beyond ~398 days, so all
three are set here deliberately.

The generated key material is disposable; keep the directory out of git.
Erasing the simulator drops the trust, so re-add the CA after any erase.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

CA_CNF = """[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no
[dn]
CN = {ca_name}
[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
"""

LEAF_CNF = """[req]
distinguished_name = dn
prompt = no
[dn]
CN = localhost
[v3_req]
basicConstraints = CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost,IP:127.0.0.1
"""


def _openssl(*args: str) -> None:
    subprocess.run(["openssl", *args], check=True, capture_output=True, text=True)


def ensure_certs(tls_dir: Path, *, ca_name: str = "shortcut-forge Test CA", force: bool = False) -> tuple[Path, Path]:
    """Return `(ca_pem, server_pem)` under `tls_dir`, generating them the first time.

    `server_pem` holds the leaf key and certificate together, the shape
    `ssl.SSLContext.load_cert_chain` takes as a single file.
    """
    tls_dir.mkdir(parents=True, exist_ok=True)
    ca_pem = tls_dir / "ca.pem"
    server_pem = tls_dir / "server.pem"
    if server_pem.exists() and ca_pem.exists() and not force:
        return ca_pem, server_pem

    (tls_dir / "ca.cnf").write_text(CA_CNF.format(ca_name=ca_name))
    (tls_dir / "leaf.cnf").write_text(LEAF_CNF)
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(tls_dir / "ca.key"),
        "-out",
        str(ca_pem),
        "-days",
        "300",
        "-config",
        str(tls_dir / "ca.cnf"),
    )
    _openssl(
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(tls_dir / "server.key"),
        "-out",
        str(tls_dir / "server.csr"),
        "-config",
        str(tls_dir / "leaf.cnf"),
    )
    _openssl(
        "x509",
        "-req",
        "-in",
        str(tls_dir / "server.csr"),
        "-CA",
        str(ca_pem),
        "-CAkey",
        str(tls_dir / "ca.key"),
        "-CAcreateserial",
        "-out",
        str(tls_dir / "server.crt"),
        "-days",
        "300",
        "-extfile",
        str(tls_dir / "leaf.cnf"),
        "-extensions",
        "v3_req",
    )
    server_pem.write_text((tls_dir / "server.key").read_text() + (tls_dir / "server.crt").read_text())
    return ca_pem, server_pem
