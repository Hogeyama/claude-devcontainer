"""Run outside the Bash sandbox; no proxy env can hide a direct connect."""
import json
import os
import socket
import sys

host, port = sys.argv[1], int(sys.argv[2])
for address, destination in [('1.1.1.1', 443), ('8.8.8.8', 53), ('10.231.70.10', 8081),
                             ('10.231.71.10', 2375), ('172.17.0.1', port), (host, port),
                             # The host's addresses on the internal network bridges.
                             ('10.231.70.1', port), ('10.231.71.1', port),
                             ('2001:4860:4860::8888', 443)]:
    try:
        connection = socket.create_connection((address, destination), .4)
    except OSError:
        continue
    connection.close()
    raise AssertionError('direct connection escaped: ' + address)
os.environ['RES_OPTIONS'] = 'attempts:1 timeout:1'
for hostname in ('proxy', 'example.com'):
    try: socket.getaddrinfo(hostname, 443)
    except socket.gaierror: continue
    raise AssertionError('DNS escaped: ' + hostname)
for resolver in ('127.0.0.11', '8.8.8.8'):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(.4)
        try:
            sock.sendto(bytes.fromhex('123401000001000000000000076578616d706c6503636f6d0000010001'), (resolver, 53))
            sock.recv(1024)
        except OSError: continue
        raise AssertionError('UDP DNS escaped: ' + resolver)
print('direct-network-blocked')
