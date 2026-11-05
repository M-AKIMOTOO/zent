#!/usr/bin/env python3
import os
import pathlib
import pty
import re
import select
import socket
import struct
import subprocess
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent
ZENT = str(ROOT / "zent")

def free_port():
    for port in range(9000, 9011):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            s.close()
            continue
        s.close()
        return port
    raise RuntimeError('no free test port in 9000..9010')

def receiver(out, *options):
    port = free_port()
    p = subprocess.Popen([ZENT, "--recv", "--bind", "127.0.0.1", "--port", str(port),
                          "--output", str(out), "--no-progress", *options],
                         stderr=subprocess.PIPE, text=True)
    line = p.stderr.readline()
    assert line.startswith("Waiting on"), (line, p.stderr.read())
    return p, port

def finish(p, good):
    _, err = p.communicate(timeout=10)
    assert (p.returncode == 0) == good, (p.returncode, err)
    assert 'Elapsed:' in err and '\r' not in err, err

def sender(src, port, offset, length, *options):
    return subprocess.run([ZENT, "--send", "--input", str(src), "--skip", str(offset),
                           "--length", str(length), "--bs", "1", "--host", "127.0.0.1",
                           "--port", str(port), "--no-progress", *options],
                          capture_output=True, timeout=10)

def raw(port, payload):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        ack = b""
        while len(ack) < 24:
            part = s.recv(24-len(ack))
            if not part:
                break
            ack += part
        return ack

def header(length, offset=0):
    return struct.pack("!8sIIQQ", b"FASTRAW1", 1, 0, length, offset)

help_result = subprocess.run([ZENT, '--help'], capture_output=True, text=True)
assert help_result.returncode == 0
for option in ('--input', '--length', '--skip', '--bs', '--offset', '--host',
               '--corr', '--bind', '--port', '--output', '--expect-length',
               '--expect-offset', '--max-bytes', '--receive-mode', '--keep-part',
               '--fsync', '--send-chunk-mib', '--rate-mib', '--readahead', '--prefetch-mib',
               '--connect-retries', '--progress-sec', '--no-progress'):
    assert re.search(r'^\s+' + re.escape(option) + r'(?:\s|,).+',
                     help_result.stdout, re.MULTILINE), option
assert '送信オプション' in help_result.stdout and '受信オプション' in help_result.stdout
assert help_result.stdout.index('受信オプション') < help_result.stdout.index('使用例と注意事項')
assert subprocess.run([ZENT, '--send', '--help'], capture_output=True).returncode == 0
assert subprocess.run([ZENT, '--recv', '--help'], capture_output=True).returncode == 0
bare_help = subprocess.run([ZENT], capture_output=True, text=True)
assert bare_help.returncode == 0 and bare_help.stdout == help_result.stdout
assert subprocess.run([ZENT, '--send', '--recv'], capture_output=True).returncode != 0
for corr in ('1', '2', '3', '5', '6', '8', '44', '101'):
    result = subprocess.run([ZENT, '--send', '--input', '/nonexistent-zent-test-file',
                             '--length', '1', '--corr', corr, '--port', '9002'],
                            capture_output=True)
    assert result.returncode != 0 and b'open input' in result.stderr, result.stderr
for corr in ('0', '4', '7', '102', 'bad'):
    result = subprocess.run([ZENT, '--send', '--input', '/nonexistent-zent-test-file',
                             '--length', '1', '--corr', corr, '--port', '9002'],
                            capture_output=True)
    assert result.returncode != 0 and b'open input' not in result.stderr
result = subprocess.run([ZENT, '--send', '--input', '/nonexistent-zent-test-file',
                         '--length', '1', '--corr', '1', '--host', '127.0.0.1',
                         '--port', '9002'], capture_output=True)
assert result.returncode != 0 and b'open input' not in result.stderr
for port in ('8999', '9011', '0', '65535', 'abc'):
    send_result = subprocess.run([ZENT, '--send', '--input', '/nonexistent-zent-test-file',
                                  '--length', '1', '--host', '127.0.0.1', '--port', port], capture_output=True)
    recv_result = subprocess.run([ZENT, '--recv', '--host', '127.0.0.1', '--output', '/unused',
                                  '--port', port], capture_output=True)
    assert send_result.returncode != 0 and b'9000 to 9010' in send_result.stderr
    assert recv_result.returncode != 0 and b'9000 to 9010' in recv_result.stderr
for port in ('9000', '9010'):
    result = subprocess.run([ZENT, '--send', '--input', '/nonexistent-zent-test-file',
                             '--length', '1', '--host', '127.0.0.1', '--port', port], capture_output=True)
    assert result.returncode != 0 and b'open input' in result.stderr

with tempfile.TemporaryDirectory(prefix="zent-test-") as tmp:
    d = pathlib.Path(tmp)
    data = bytes(range(256)) * 4097
    src = d / "source"
    src.write_bytes(data)
    # The receiving hostname is resolved on the receiving machine.
    port = free_port()
    out = d / 'hostname-output'
    p = subprocess.Popen([ZENT, '--recv', '--host', 'localhost', '--port', str(port),
                          '--output', str(out), '--no-progress'], stderr=subprocess.PIPE, text=True)
    assert p.stderr.readline().startswith('Waiting on localhost:')
    result = sender(src, port, 3, 7)
    assert result.returncode == 0, result.stderr
    assert b'Elapsed:' in result.stderr and b'average:' in result.stderr and b'\r' not in result.stderr
    finish(p, True)
    assert out.read_bytes() == data[3:10]
    # Pace one MiB at 1 MiB/s, including the final partial interval.
    out = d / 'rate-limited'
    p, port = receiver(out, '--receive-mode', 'rw')
    before_time = time.monotonic()
    result = sender(src, port, 0, 1048576, '--rate-mib', '1')
    elapsed = time.monotonic() - before_time
    assert result.returncode == 0 and b'Send rate limit: 1 MiB/s' in result.stderr, result.stderr
    finish(p, True)
    assert elapsed >= 0.90 and out.read_bytes() == data[:1048576], elapsed
    # The data itself is paced, rather than sent in a burst followed by a sleep.
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', free_port()))
    listener.listen(1)
    listener.settimeout(5)
    port = listener.getsockname()[1]
    proc = subprocess.Popen([ZENT, '--send', '--input', str(src), '--skip', '0',
                             '--length', '1048576', '--bs', '1', '--host', '127.0.0.1',
                             '--port', str(port), '--rate-mib', '1'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    conn, _ = listener.accept()
    try:
        conn.settimeout(5)
        wire_header = b''
        while len(wire_header) < 32:
            part = conn.recv(32-len(wire_header))
            assert part
            wire_header += part
        conn.settimeout(0.05)
        started = time.monotonic()
        payload = bytearray()
        while time.monotonic() - started < 0.35:
            try:
                part = conn.recv(65536)
                if not part:
                    break
                payload.extend(part)
            except socket.timeout:
                pass
        observed = time.monotonic() - started
        assert len(payload) <= (observed + 0.3) * 1048576, (len(payload), observed)
        conn.settimeout(5)
        while True:
            part = conn.recv(65536)
            if not part:
                break
            payload.extend(part)
        conn.sendall(struct.pack('!8sIIQ', b'FSTRACK1', 1, 0, len(payload)))
        _, err = proc.communicate(timeout=5)
        assert proc.returncode == 0 and bytes(payload) == data[:1048576], err
    finally:
        conn.close()
        listener.close()
        if proc.poll() is None:
            proc.kill(); proc.communicate(timeout=5)
    # An explicit zero overrides an inherited limit.
    out = d / 'rate-override'
    p, port = receiver(out, '--receive-mode', 'rw')
    result = subprocess.run([ZENT, '--send', '--input', str(src), '--skip', '0',
                             '--length', '100', '--bs', '1', '--host', '127.0.0.1',
                             '--port', str(port), '--rate-mib', '0'], capture_output=True,
                            env=dict(os.environ, ZENT_RATE_MIB='1'), timeout=10)
    assert result.returncode == 0 and b'Send rate limit:' not in result.stderr, result.stderr
    finish(p, True)
    assert out.read_bytes() == data[:100]
    result = subprocess.run([ZENT, '--send', '--input', str(src), '--length', '1',
                             '--bs', '1', '--host', '127.0.0.1', '--port', str(free_port())],
                            capture_output=True, env=dict(os.environ, ZENT_RATE_MIB='bad'))
    assert result.returncode != 0 and b'Invalid ZENT_RATE_MIB' in result.stderr
    invalid = subprocess.run([ZENT, '--recv', '--host', 'localhost', '--bind', '127.0.0.1',
                              '--port', '9002', '--output', str(d / 'ambiguous')], capture_output=True)
    assert invalid.returncode != 0
    invalid = subprocess.run([ZENT, '--recv', '--corr', '4', '--port', '9002',
                              '--output', str(d / 'invalid-corr')], capture_output=True)
    assert invalid.returncode != 0 and b'Invalid --corr' in invalid.stderr
    for mode in ("rw", "splice"):
        for i, (off, size) in enumerate(((1, 1), (3, 7), (0, len(data)),
                                         (len(data)-1, 1), (4095, 524289))):
            out = d / (mode + str(i))
            p, port = receiver(out, "--receive-mode", mode, "--fsync")
            send = sender(src, port, off, size, "--send-chunk-mib", "1")
            assert send.returncode == 0, send.stderr
            finish(p, True)
            assert out.read_bytes() == data[off:off+size]
            assert not list(d.glob(out.name + ".part.*"))
    # The port remains reserved even after accept and while data is arriving.
    out = d / 'port-owner'
    p, port = receiver(out, '--receive-mode', 'rw')
    def conflict(bind='127.0.0.1'):
        return subprocess.run([ZENT, '--recv', '--bind', bind, '--port', str(port),
                               '--output', str(d / 'port-duplicate'), '--no-progress'],
                              capture_output=True, timeout=5)
    second = conflict()
    assert second.returncode != 0 and b'Port conflict:' in second.stderr, second.stderr
    wildcard = conflict('0.0.0.0')
    assert wildcard.returncode != 0 and b'Port conflict:' in wildcard.stderr, wildcard.stderr
    other, other_port = receiver(d / 'port-independent')
    assert other_port != port and other.poll() is None
    other.terminate()
    other.communicate(timeout=5)
    client = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
        client.sendall(header(4) + b'ab')
        # Existence of the temporary file confirms accept and header handling.
        deadline = time.monotonic() + 5
        while not list(d.glob('port-owner.part.*')):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        second = conflict()
        assert second.returncode != 0 and b'Port conflict:' in second.stderr, second.stderr
        client.sendall(b'cd')
        client.shutdown(socket.SHUT_WR)
        ack = b''
        while len(ack) < 24:
            part = client.recv(24-len(ack))
            assert part
            ack += part
        assert struct.unpack('!8sIIQ', ack)[2:] == (0, 4)
    finally:
        client.close()
    finish(p, True)
    assert out.read_bytes() == b'abcd'
    # Terminal progress updates in place, while completion is a normal line.
    master, slave = pty.openpty()
    port = free_port()
    pty_proc = subprocess.Popen([ZENT, '--recv', '--host', '127.0.0.1', '--port', str(port),
                                 '--output', str(d / 'terminal-output'), '--receive-mode', 'rw'],
                                stdout=subprocess.DEVNULL, stderr=slave)
    os.close(slave)
    terminal_output = b''
    try:
        ready, _, _ = select.select([master], [], [], 5)
        assert ready
        terminal_output += os.read(master, 65536)
        assert b'Waiting on' in terminal_output
        with socket.create_connection(('127.0.0.1', port), timeout=5) as client:
            client.sendall(header(4) + b'a')
            time.sleep(1.1)
            client.sendall(b'b')
            time.sleep(1.1)
            client.sendall(b'c')
            time.sleep(0.1)
            client.sendall(b'd')
            client.shutdown(socket.SHUT_WR)
            ack = b''
            while len(ack) < 24:
                part = client.recv(24-len(ack))
                assert part
                ack += part
        assert pty_proc.wait(timeout=5) == 0
        while select.select([master], [], [], 0)[0]:
            try:
                terminal_output += os.read(master, 65536)
            except OSError:
                break
        assert terminal_output.count(b'\r\x1b[Kreceived ') >= 2, terminal_output
        assert b'Elapsed:' in terminal_output and b'average:' in terminal_output
        elapsed_match = re.search(rb'Elapsed: [0-9:.]+ \(([0-9.]+) s\)', terminal_output)
        assert elapsed_match and 2.0 <= float(elapsed_match.group(1)) < 10.0, terminal_output
    finally:
        if pty_proc.poll() is None:
            pty_proc.kill(); pty_proc.wait()
        os.close(master)
    # The same address and port can be used again after completion.
    again = subprocess.Popen([ZENT, '--recv', '--bind', '127.0.0.1', '--port', str(port),
                              '--output', str(d / 'port-next'), '--no-progress'],
                             stderr=subprocess.PIPE, text=True)
    try:
        assert again.stderr.readline().startswith('Waiting on')
    finally:
        again.terminate()
        again.communicate(timeout=5)
    # Seconds are converted using --bs; --offset adds a byte-level base.
    out = d / 'scaled'
    p, port = receiver(out, '--expect-offset', '9', '--expect-length', '12')
    send = sender(src, port, 2, 3, '--bs', '4', '--offset', '1')
    assert send.returncode == 0, send.stderr
    finish(p, True)
    assert out.read_bytes() == data[9:21]
    # Check the default 256 MB/s without sending a large payload.
    sparse = d / 'sparse'
    with sparse.open('wb') as f:
        f.truncate(512000000)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', free_port()))
    listener.listen(1)
    port = listener.getsockname()[1]
    proc = subprocess.Popen([ZENT, '--send', '--input', str(sparse), '--skip', '1',
                             '--length', '1', '--host', '127.0.0.1', '--port', str(port),
                             '--no-progress'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    listener.settimeout(5)
    conn, _ = listener.accept()
    conn.settimeout(5)
    with conn:
        wire = b''
        while len(wire) < 32:
            part = conn.recv(32 - len(wire))
            assert part
            wire += part
        _, _, _, wire_length, wire_offset = struct.unpack('!8sIIQQ', wire)
        assert (wire_offset, wire_length) == (256000000, 256000000)
    listener.close()
    proc.communicate(timeout=10)
    assert proc.returncode != 0  # The test peer deliberately sends no ACK.
    for label, payload, extra in (
        ("early", header(10)+b"abc", ()),
        ("trailing", header(3)+b"abcd", ()),
        ("badmagic", b"BADMAGIC"+header(3)[8:]+b"abc", ()),
        ("oversize", header(4)+b"abcd", ("--max-bytes", "3")),
        ("wrong_length", header(3)+b"abc", ("--expect-length", "4")),
        ("wrong_offset", header(3, 7)+b"abc", ("--expect-offset", "8")),
        ("kept", header(10)+b"abc", ("--keep-part",)),
    ):
        out = d / label
        p, port = receiver(out, *extra)
        ack = raw(port, payload)
        finish(p, False)
        assert not out.exists()
        parts = list(d.glob(out.name + ".part.*"))
        assert bool(parts) == (label == "kept"), parts
        if ack:
            assert len(ack) == 24 and struct.unpack("!8sIIQ", ack)[2] != 0
    out = d / "existing"
    out.write_bytes(b"untouched")
    p, port = receiver(out)
    send = sender(src, port, 0, 100)
    assert send.returncode != 0
    finish(p, False)
    assert out.read_bytes() == b"untouched"
    assert not list(d.glob(out.name + ".part.*"))
    for off, length in ((0, 0), (len(data), 1), (-1, 1),
                        (18446744073709551615, 2)):
        assert sender(src, free_port(), off, length).returncode != 0
    for off, length, bs in ((1, 1, 0), (2, 1, 9223372036854775807),
                            (0, 2, 9223372036854775807)):
        assert sender(src, free_port(), off, length, '--bs', str(bs)).returncode != 0
    assert sender(src, free_port(), 0, 10).returncode != 0
    # A peer that consumes the payload but sends no ACK or a failure ACK must
    # never cause the sender to report success.
    for reply in (b"", struct.pack("!8sIIQ", b"FSTRACK1", 1, 1, 100),
                  struct.pack("!8sIIQ", b"FSTRACK1", 1, 0, 99)):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", free_port()))
        listener.listen(1)
        port = listener.getsockname()[1]
        def peer():
            conn, _ = listener.accept()
            with conn:
                while conn.recv(65536):
                    pass
                if reply:
                    conn.sendall(reply)
            listener.close()
        thread = threading.Thread(target=peer)
        thread.start()
        result = sender(src, port, 0, 100)
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert result.returncode != 0 and b'Transfer failed:' in result.stderr and b'Elapsed:' in result.stderr
    # A refused connection before any transfer may be retried safely.
    port = free_port()
    def delayed_peer():
        time.sleep(0.2)
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
        listener.listen(1)
        conn, _ = listener.accept()
        with conn:
            received = b''
            while True:
                part = conn.recv(65536)
                if not part:
                    break
                received += part
            assert received[32:] == data[:100]
            conn.sendall(struct.pack('!8sIIQ', b'FSTRACK1', 1, 0, 100))
        listener.close()
    thread = threading.Thread(target=delayed_peer, daemon=True)
    thread.start()
    result = sender(src, port, 0, 100, '--connect-retries', '3')
    thread.join(timeout=5)
    assert not thread.is_alive() and result.returncode == 0, result.stderr
    assert b'retry' in result.stderr
print("All zent tests passed")
