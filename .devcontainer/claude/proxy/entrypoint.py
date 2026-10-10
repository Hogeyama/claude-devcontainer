"""Publish the CA certificate (never its key) for the agent, then run mitmdump."""
import os
from pathlib import Path

from mitmproxy import certs

CONFDIR = Path('/home/mitmproxy/.mitmproxy')
PUBLIC_CA = Path('/public-ca/mitmproxy-ca-cert.pem')
COMMAND = ['mitmdump', '--mode', 'regular', '--listen-host', '0.0.0.0', '--listen-port', '8080',
           '--scripts', '/opt/proxy/addon.py', '--set', f'confdir={CONFDIR}',
           # Show the addon's log (denials and upstream statuses, without the
           # query), but not mitmproxy's own per-flow lines: a URL may carry a secret.
           '--set', 'termlog_verbosity=warn', '--set', 'flow_detail=0',
           # Connect upstream only for requests the addon has accepted.
           '--set', 'connection_strategy=lazy', '--set', 'upstream_cert=false',
           '--set', 'rawtcp=false', '--set', 'http2=false']


def publish_ca():
    certificate = certs.CertStore.from_store(CONFDIR, 'mitmproxy', 2048).default_ca.to_pem()
    temporary = PUBLIC_CA.with_name('.ca.tmp')
    temporary.write_bytes(certificate)
    temporary.replace(PUBLIC_CA)


if __name__ == '__main__':
    publish_ca()
    os.execvp(COMMAND[0], COMMAND)
