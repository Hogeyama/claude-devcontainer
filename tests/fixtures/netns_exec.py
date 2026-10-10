"""unshare creates lo DOWN; bring it up like bubblewrap before namespace probes."""
import fcntl
import os
import socket
import struct
import sys

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    request = struct.pack('16sH14s', b'lo', 0, b'')
    flags = struct.unpack('16sH14s', fcntl.ioctl(sock.fileno(), 0x8913, request))[1]
    fcntl.ioctl(sock.fileno(), 0x8914, struct.pack('16sH14s', b'lo', flags | 1, b''))
os.execvp(sys.argv[1], sys.argv[1:])
