#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Start a generated receiver over SSH, wait for its listener, then send.

Python 2.6/3; remote side requires only bash, setsid and zent.
"""
from __future__ import print_function
import errno
import os
import re
import signal
import subprocess
import sys
import threading
import time
from optparse import OptionParser

def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"

def remote_command(script, odir, environ):
    # The receiver's process group is owned by this invocation. EOF on SSH's
    # control stdin kills that group, including on local failure/disconnection.
    delimiter = 'ZENT_SCRIPT_%d_%d' % (os.getpid(), int(time.time()))
    while delimiter in script.splitlines():
        delimiter += '_END'
    assignments = ['output_dir=' + quote(odir),
                   'case "$output_dir" in /*) ;; *) output_dir="$PWD/$output_dir";; esac',
                   'export ZENT_OUTPUT_DIR="$output_dir"',
                   'export ZENT_PROGRESS_LOG=1']
    for name in ('TRANSFER_RETRIES', 'IDLE_TIMEOUT', 'CHECKPOINT_MIB',
                 'RECEIVE_MODE', 'WRITE_BATCH_MIB', 'PIPE_MIB', 'PROGRESS_SEC'):
        if name in environ:
            assignments.append('export %s=%s' % (name, quote(environ[name])))
    if environ.get('REMOTE_ZENT'):
        assignments.append('export ZENT=' + quote(environ['REMOTE_ZENT']))
    else:
        assignments.append('if command -v zent >/dev/null 2>&1; then export ZENT=zent; else export ZENT="$HOME/bin/zent"; fi')
    return '''bash -c %s''' % quote('''set -eu
cd -- "$HOME"
session_dir=$(mktemp -d "${TMPDIR:-/tmp}/zent-run.XXXXXX")
receiver_pid=
monitor_pid=
cleanup() {
    result=$?
    trap - EXIT HUP INT TERM
    if [ -n "$receiver_pid" ]; then
        kill -TERM -- "-$receiver_pid" 2>/dev/null || :
        wait "$receiver_pid" 2>/dev/null || :
    fi
    if [ -n "$monitor_pid" ]; then
        kill "$monitor_pid" 2>/dev/null || :
        wait "$monitor_pid" 2>/dev/null || :
    fi
    rm -f -- "$session_dir/recv.sh"
    rmdir -- "$session_dir" 2>/dev/null || :
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
cat > "$session_dir/recv.sh" <<'%s'
%s
%s
%s
setsid bash "$session_dir/recv.sh" &
receiver_pid=$!
(
    while IFS= read -r control; do :; done
    kill -TERM -- "-$receiver_pid" 2>/dev/null || :
) <&0 &
monitor_pid=$!
set +e
wait "$receiver_pid"
result=$?
exit "$result"
''' % (delimiter, script, delimiter, '\n'.join(assignments)))

def stop_group(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        end = time.time() + 3
        while process.poll() is None and time.time() < end:
            time.sleep(0.05)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
    except OSError as error:
        if error.errno != errno.ESRCH:
            raise
    process.wait()

def main():
    parser = OptionParser(usage='%prog --host HOST --sender FILE --receiver FILE --odir DIR [options]')
    parser.add_option('--host', help='SSH destination (data still uses the generated TCP address)')
    parser.add_option('--sender', help='local generated sender script')
    parser.add_option('--receiver', help='local generated receiver script; sent over SSH')
    parser.add_option('--odir', help='remote output directory; relative paths start at remote HOME')
    parser.add_option('--ready-timeout', default='60', metavar='SEC', help='wait for receiver listener (default 60 seconds)')
    parser.add_option('--log', metavar='FILE', help='combined log; existing files are not overwritten')
    options, arguments = parser.parse_args()
    if arguments or any(not getattr(options, name) for name in ('host', 'sender', 'receiver', 'odir')):
        parser.error('--host, --sender, --receiver and --odir are required')
    if options.host.startswith('-') or not re.match(r'^[A-Za-z0-9_.@:-]+$', options.host):
        parser.error('invalid SSH host')
    if not re.match(r'^[0-9]+$', options.ready_timeout) or not 1 <= int(options.ready_timeout) <= 86400:
        parser.error('--ready-timeout must be 1..86400')
    with open(options.receiver, 'rb') as stream:
        script = stream.read().decode('utf-8')
    if not os.path.isfile(options.sender):
        parser.error('sender script not found')
    logfile = options.log or os.path.splitext(options.sender)[0] + '_run_' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '_%d.log' % os.getpid()
    logfd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    log = os.fdopen(logfd, 'wb')
    print('Combined log: %s' % logfile, file=sys.stderr)
    lock = threading.Lock()
    ready = threading.Event()
    closing = threading.Event()
    threads = []
    receiver = sender = None

    def record(label, stream):
        for line in iter(stream.readline, b''):
            if label == 'recv' and line.startswith(b'Waiting on '):
                ready.set()
            prefix = ('[%s] ' % label).encode('ascii')
            stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ ', time.gmtime()).encode('ascii')
            with lock:
                if not closing.is_set():
                    log.write(stamp + prefix + line); log.flush()
                    try:
                        os.write(2, prefix + line)
                    except OSError:
                        pass
        stream.close()

    def forward(label, process):
        thread = threading.Thread(target=record, args=(label, process.stdout))
        thread.daemon = True; thread.start(); threads.append(thread)

    try:
        command = remote_command(script, options.odir, os.environ).encode('utf-8')
        receiver = subprocess.Popen([os.environ.get('ZENT_SSH', 'ssh'), '-T',
                                     '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                                     options.host, command], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    preexec_fn=os.setsid)
        forward('recv', receiver)
        end = time.time() + int(options.ready_timeout)
        while not ready.is_set():
            if receiver.poll() is not None:
                raise RuntimeError('receiver/SSH exited before listening (status %s); see log' % receiver.returncode)
            if time.time() >= end:
                raise RuntimeError('timed out waiting for receiver; see log')
            time.sleep(0.05)
        environment = dict(os.environ, ZENT_PROGRESS_LOG='1')
        sender = subprocess.Popen(['bash', options.sender], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, env=environment, preexec_fn=os.setsid)
        forward('send', sender)
        while sender.poll() is None:
            if receiver.poll() is not None and receiver.returncode != 0:
                raise RuntimeError('receiver/SSH failed (status %s); see log' % receiver.returncode)
            time.sleep(0.05)
        if sender.returncode != 0:
            raise RuntimeError('sender failed (status %s); see log' % sender.returncode)
        # Keep control stdin open until the remote wrapper exits normally;
        # closing it immediately after ACK could kill a finishing scan loop.
        end = time.time() + 10
        while receiver.poll() is None and time.time() < end:
            time.sleep(0.05)
        if receiver.poll() is None or receiver.returncode != 0:
            raise RuntimeError('receiver/SSH did not finish successfully; see log')
        for thread in threads:
            thread.join(3)
        print('All selected transfers completed', file=sys.stderr)
        return 0
    finally:
        stop_group(sender)
        if receiver is not None:
            try:
                receiver.stdin.close()
            except (IOError, OSError):
                pass
            # Allow the remote stdin watcher to terminate its own process group.
            end = time.time() + 3
            while receiver.poll() is None and time.time() < end:
                time.sleep(0.05)
            stop_group(receiver)
        for thread in threads:
            thread.join(3)
        with lock:
            closing.set(); log.close()

if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Interrupted; this invocation\'s receiver was stopped', file=sys.stderr)
        sys.exit(130)
    except (IOError, OSError, RuntimeError) as error:
        print('ERROR: %s' % error, file=sys.stderr)
        sys.exit(1)
