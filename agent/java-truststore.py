#!/usr/bin/env python3
"""Write the system's CA bundle, with the proxy's CA, as a truststore for Java.

Java reads neither the system trust nor SSL_CERT_FILE, only its own cacerts,
which a JDK from Nix keeps read-only in the store. So the entrypoint writes
this file after update-ca-certificates, and JAVA_TOOL_OPTIONS in the
Dockerfile points Java at it. The image has no keytool and its OpenSSL cannot
mark PKCS12 certificates as trusted for Java, so the file is JKS, written here
with the standard library only.

Usage: java-truststore.py [BUNDLE [TARGET]]
"""
import base64
import hashlib
import os
from pathlib import Path
import re
import struct
import sys
import time

BUNDLE = '/etc/ssl/certs/ca-certificates.crt'
TARGET = '/etc/ssl/java/cacerts'
# Java's default truststore password. It only guards the file's integrity, and
# Java loads the file without it.
PASSWORD = 'changeit'
PEM = re.compile(rb'-----BEGIN CERTIFICATE-----\s*(.+?)\s*-----END CERTIFICATE-----', re.S)


def certificates(pem):
    """The DER certificates of a PEM bundle."""
    return [base64.b64decode(b''.join(body.split())) for body in PEM.findall(pem)]


def utf(text):
    # DataOutputStream.writeUTF; the aliases are ASCII.
    data = text.encode('ascii')
    return struct.pack('>H', len(data)) + data


def jks(ders, password=PASSWORD, now=None):
    """A JKS keystore of trusted certificate entries."""
    stamp = int((time.time() if now is None else now) * 1000)
    data = struct.pack('>IiI', 0xFEEDFEED, 2, len(ders))
    for index, der in enumerate(ders):
        data += (struct.pack('>i', 2) + utf(f'ca-{index:04d}') + struct.pack('>q', stamp)
                 + utf('X.509') + struct.pack('>i', len(der)) + der)
    digest = hashlib.sha1(password.encode('utf-16-be') + b'Mighty Aphrodite' + data).digest()
    return data + digest


def write(bundle=BUNDLE, target=TARGET):
    ders = certificates(Path(bundle).read_bytes())
    if not ders:
        raise ValueError(f'{bundle}: no certificates')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name('.' + target.name + '.tmp')
    temporary.write_bytes(jks(ders))
    os.chmod(temporary, 0o644)
    temporary.replace(target)


if __name__ == '__main__':
    try:
        write(*sys.argv[1:3])
    except (OSError, ValueError) as error:
        sys.exit(f'error: java-truststore: {error}')
