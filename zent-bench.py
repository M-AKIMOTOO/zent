#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare receiver paths on one machine, optionally writing onto an NFS mount.

Python 2.6/3. Every run syncs output and verifies the exact selected source range.
"""
from __future__ import print_function
import csv
import hashlib
import os
import resource
import select
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from optparse import OptionParser

MIB = 1024 * 1024
CASES = [('splice', ['--receive-mode', 'splice']),
         ('pipe1', ['--receive-mode', 'splice', '--pipe-mib', '1']),
         ('rw', ['--receive-mode', 'rw']),
         ('batch4', ['--receive-mode', 'rw', '--write-batch-mib', '4']),
         ('batch16', ['--receive-mode', 'rw', '--write-batch-mib', '16'])]
clock = getattr(time, 'monotonic', time.time)

def digest(path, offset, length):
    result = hashlib.sha256()
    with open(path, 'rb') as stream:
        stream.seek(offset)
        while length:
            data = stream.read(min(4*MIB, length))
            if not data:
                raise RuntimeError('unexpected EOF during hash verification')
            result.update(data); length -= len(data)
    return result.digest()

def median(values):
    values = sorted(values); middle = len(values)//2
    return values[middle] if len(values) % 2 else (values[middle-1]+values[middle])/2.0

def fingerprint(path):
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime, st.st_ctime)

def main():
    p = OptionParser(usage='%prog --input FILE --directory DIR [options]')
    p.add_option('--input', help='stable regular source file (required)')
    p.add_option('--directory', help='existing destination directory, e.g. an NFS mount (required)')
    p.add_option('--offset', default='0', metavar='BYTES', help='source byte offset (default 0)')
    p.add_option('--length', metavar='BYTES', help='exact byte count (default: up to 256 MiB)')
    p.add_option('--runs', default='3', metavar='N', help='repeats per mode, 1..20 (default 3)')
    p.add_option('--cases', default='splice,pipe1,rw,batch4,batch16', help='comma-separated comparison modes')
    p.add_option('--port', default='9002', help='local TCP port, 9000..9010 (default 9002)')
    p.add_option('--zent', default=os.environ.get('ZENT', 'zent'), help='zent executable (default: ZENT or zent)')
    p.add_option('--resume', action='store_true', default=False, help='include the default resume checkpoint overhead')
    p.add_option('--csv', metavar='FILE', help='save per-run measurements; never overwrite a file')
    options, args = p.parse_args()
    if args or not options.input or not options.directory:
        p.error('--input and --directory are required')
    try:
        offset = int(options.offset); runs = int(options.runs); port = int(options.port)
        st = os.stat(options.input)
        length = int(options.length) if options.length is not None else min(256*MIB, st.st_size-offset)
    except (ValueError, OSError) as error:
        p.error(str(error))
    if not stat.S_ISREG(st.st_mode) or offset < 0 or length <= 0 or offset+length > st.st_size or offset+length > (1<<63)-1:
        p.error('selected range must fit a stable regular file and the 64-bit limit')
    if not 1 <= runs <= 20 or not 9000 <= port <= 9010:
        p.error('--runs must be 1..20 and --port must be 9000..9010')
    names = options.cases.split(',')
    cases = [case for case in CASES if case[0] in names]
    if len(cases) != len(names):
        p.error('unknown or duplicate case; use splice,pipe1,rw,batch4,batch16')
    if not os.path.isdir(options.directory):
        p.error('--directory must already exist')
    csvfile = None
    if options.csv:
        csvfile = os.fdopen(os.open(options.csv, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w')
    directory = None; sender = receiver = None
    try:
        directory = tempfile.mkdtemp(prefix='.zent-bench-', dir=options.directory)
        before = fingerprint(options.input)
        print('Local loopback; destination: %s' % os.path.abspath(options.directory))
        print('Range: offset=%d length=%d bytes; runs=%d; fsync=yes; resume=%s' % (offset, length, runs, options.resume))
        print('Source is read once for SHA-256 before timing (warm-cache comparison).')
        reference = digest(options.input, offset, length)
        rows = []; timings = dict((name, []) for name, _ in cases)
        for iteration in range(runs):
            # Rotate the order to reduce a fixed first/last-mode bias.
            order = cases[iteration % len(cases):] + cases[:iteration % len(cases)]
            for name, extra in order:
                output = os.path.join(directory, '%s-%d.raw' % (name, iteration))
                resume_args = ['--resume'] if options.resume else []
                receiver = subprocess.Popen([options.zent, '--recv', '--bind', '127.0.0.1',
                                             '--port', str(port), '--output', output, '--fsync',
                                             '--idle-timeout', '120', '--no-progress'] + extra + resume_args,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if not select.select([receiver.stderr], [], [], 10)[0]:
                    raise RuntimeError('receiver did not start')
                first = receiver.stderr.readline()
                if not first.startswith(b'Waiting on '):
                    raise RuntimeError('receiver could not listen: ' + first.decode('utf-8', 'replace'))
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                cpu_before = usage.ru_utime + usage.ru_stime
                start = clock()
                sender = subprocess.Popen([options.zent, '--send', '--host', '127.0.0.1',
                                           '--port', str(port), '--input', options.input,
                                           '--skip', str(offset), '--bs', '1', '--length', str(length),
                                           '--rate-mib', '0', '--idle-timeout', '120', '--no-progress'] + resume_args,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                _, send_error = sender.communicate()
                _, recv_error = receiver.communicate()
                elapsed = clock()-start
                usage = resource.getrusage(resource.RUSAGE_CHILDREN)
                cpu = usage.ru_utime + usage.ru_stime - cpu_before
                if sender.returncode or receiver.returncode or elapsed <= 0:
                    raise RuntimeError('transfer failed: ' + (send_error + recv_error).decode('utf-8', 'replace'))
                if fingerprint(options.input) != before:
                    raise RuntimeError('source changed during benchmark')
                if os.path.getsize(output) != length or digest(output, 0, length) != reference:
                    raise RuntimeError('received bytes failed SHA-256 verification')
                speed = length/float(MIB)/elapsed
                rows.append((name, iteration+1, length, elapsed, speed, cpu))
                timings[name].append(speed)
                print('%-8s run %d: %8.2f MiB/s  %7.3f s  CPU %6.3f s  SHA-256 OK' % (name, iteration+1, speed, elapsed, cpu))
                if b'pipe enlargement unavailable' in recv_error:
                    print('  Pipe enlargement denied by OS; this run used its original capacity.')
                sys.stdout.flush()
                os.unlink(output)
                # Only remove artifacts belonging to this uniquely named trial.
                for ending in ('.zent.state', '.zent.lock'):
                    path = output + ending
                    if os.path.exists(path):
                        os.unlink(path)
        print('\nMode       median MiB/s   min MiB/s   max MiB/s')
        for name, _ in cases:
            values = timings[name]
            print('%-8s %12.2f %11.2f %11.2f' % (name, median(values), min(values), max(values)))
        print('This isolates local receive/storage behavior; it does not measure the PC1-to-PC2 10Gbps link.')
        if csvfile is not None:
            writer = csv.writer(csvfile)
            writer.writerow(('mode', 'run', 'bytes', 'seconds', 'mib_per_second', 'child_cpu_seconds'))
            writer.writerows(rows)
        return 0
    finally:
        for process in (sender, receiver):
            if process is not None and process.poll() is None:
                process.kill(); process.wait()
        if directory is not None:
            shutil.rmtree(directory)
        if csvfile is not None:
            csvfile.close()

if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Benchmark interrupted', file=sys.stderr); sys.exit(130)
    except (IOError, OSError, RuntimeError) as error:
        print('ERROR: %s' % error, file=sys.stderr); sys.exit(1)
