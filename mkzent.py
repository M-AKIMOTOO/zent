#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Compatible with Python 2.6 (RHEL 6) and Python 3.
from __future__ import print_function
import datetime
import errno
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
from optparse import OptionParser, IndentedHelpFormatter

LIMIT = (1 << 63) - 1
CORRELATORS = {1: 'corr1_10G', 2: 'corr2_10G', 3: 'corr3_10G',
               5: 'corr5_10G', 6: 'corr6_10G', 8: 'corr8_10G',
               44: 'Rocky', 101: 'yamaNAS1'}
ANTENNAS = {'0': ('YAMAGU32', 'K'), '1': ('YAMAGU34', 'L')}

def fail(message):
    raise ValueError(message)

def number(value, name, allow_zero=False):
    if not re.match(r'^[0-9]+$', value or ''):
        fail('%s must be a decimal integer' % name)
    result = int(value)
    if result > LIMIT or (not allow_zero and result == 0):
        fail('%s is out of range' % name)
    return result

def parse_time(value):
    if re.match(r'^20[0-9]{11}$', value):
        year, rest = int(value[:4]), value[4:]
    elif re.match(r'^[0-9]{11}$', value):
        year, rest = 2000 + int(value[:2]), value[2:]
    else:
        fail('invalid UTC time %r; expected YYDDDhhmmss or YYYYDDDhhmmss' % value)
    day, hour, minute, second = int(rest[:3]), int(rest[3:5]), int(rest[5:7]), int(rest[7:9])
    if hour > 23 or minute > 59 or second > 59 or day < 1:
        fail('invalid UTC time %r' % value)
    start = datetime.datetime(year, 1, 1)
    result = start + datetime.timedelta(days=day-1, hours=hour, minutes=minute, seconds=second)
    if result.year != year:
        fail('invalid day of year in %r' % value)
    return result

def recording_start(path):
    values = []
    for line in open(path, 'r'):
        match = re.match(r'^\s*\$REQRECSTART\s+(\d+)\s*$', line)
        if match:
            values.append(match.group(1))
    if len(values) != 1:
        fail('log must contain exactly one $REQRECSTART line: %s' % path)
    return values[0]

def quote(value):
    return "'" + value.replace("'", "'\\''") + "'"

def receiver_ipv4(host):
    try:
        addresses = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror as e:
        fail('cannot resolve receiver %s to IPv4 on the sender: %s; check name resolution or specify --bind IP' % (host, e))
    if not addresses:
        fail('no IPv4 address for receiver %s; check name resolution or specify --bind IP' % host)
    return addresses[0][4][0]

def write_files(files, force):
    if not force:
        created = []
        try:
            for path, contents, mode in files:
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
                except OSError as e:
                    if e.errno == errno.EEXIST:
                        fail('file already exists: %s; use --out-prefix for another name, or --force to replace the generated files' % path)
                    raise
                created.append(path)
                f = os.fdopen(fd, 'w')
                try:
                    f.write(contents)
                finally:
                    f.close()
        except Exception:
            for path in created:
                os.unlink(path)
            raise
        return

    # Stage all contents first; never follow or replace existing symlinks or
    # special files. Renames are atomic per file, not across the whole set.
    for path, _, _ in files:
        try:
            info = os.lstat(path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                continue
            raise
        if not stat.S_ISREG(info.st_mode):
            fail('--force only replaces regular files: %s' % path)
    mask = os.umask(0)
    os.umask(mask)
    staged = []
    renamed = 0
    try:
        for path, contents, mode in files:
            fd, temporary = tempfile.mkstemp(prefix='.mkzent-', dir=os.path.dirname(os.path.abspath(path)))
            staged.append((temporary, path))
            f = os.fdopen(fd, 'w')
            try:
                os.fchmod(f.fileno(), mode & ~mask)
                f.write(contents)
            finally:
                f.close()
        for temporary, path in staged:
            os.rename(temporary, path)
            renamed += 1
    except Exception as e:
        if renamed:
            fail('failed after replacing %d file(s); inspect the generated files: %s' % (renamed, e))
        raise
    finally:
        for temporary, _ in staged:
            if os.path.exists(temporary):
                os.unlink(temporary)

def exclusions(items):
    result = set()
    for text in items:
        for part in text.split(','):
            m = re.match(r'^([0-9]+)(?:-([0-9]+))?$', part.strip())
            if not m:
                fail('invalid scan selection: %s' % part)
            first = int(m.group(1)); last = int(m.group(2) or first)
            if first < 1 or last < first or last > 1000000:
                fail('invalid scan selection: %s' % part)
            result.update(range(first, last + 1))
    return result

def scans(path, start, rate, base, antenna, excluded, targets):
    rows = []
    in_sked = False
    index = 0
    names = set()
    for line in open(path, 'r'):
        line = line.strip()
        if line.startswith('$'):
            if in_sked:
                break
            in_sked = (line.split()[0].upper() == '$SKED')
            continue
        if not in_sked or not line or line.startswith('*'):
            continue
        parts = line.split()
        if len(parts) < 9 or parts[3].upper() != 'PREOB' or parts[6].upper() != 'MIDOB' or parts[8].upper() != 'POSTOB':
            fail('unrecognized $SKED line: %s' % line)
        index += 1
        target, stamp, duration = parts[0], parts[4], number(parts[5], 'duration')
        if not re.match(r'^[A-Za-z0-9_.+\-]+$', target):
            fail('invalid target name: %s' % target)
        time = parse_time(stamp)
        if index in excluded or target.lower() in targets:
            continue
        delta = time - start
        seconds = delta.days * 86400 + delta.seconds
        if seconds < 0:
            fail('scan %d starts before recording' % index)
        offset = base + seconds * rate
        length = duration * rate
        if offset > LIMIT or length > LIMIT or offset + length > LIMIT:
            fail('scan %d exceeds the supported 64-bit range' % index)
        stamp13 = '%04d%03d%02d%02d%02d' % (time.year, int(time.strftime('%j')), time.hour, time.minute, time.second)
        outfile = '%s_%s.raw' % (antenna, stamp13)
        if outfile in names:
            fail('multiple scans would use output name %s' % outfile)
        names.add(outfile)
        rows.append((index, target, stamp13, duration, offset, length, outfile))
    if not index or not rows:
        fail('no selected scans found in $SKED')
    if any(x > index for x in excluded):
        fail('excluded scan number is outside the schedule')
    return rows

SENDER = r'''#!/bin/bash
set -eu
export LC_ALL=C
HOST=%(host)s
PORT=%(port)s
IFILE=%(ifile)s
BS=%(rate)s
BASE=%(base)s
ZENT=${ZENT:-zent}
RETRIES=${RETRIES:-30}
ZENT_RATE_MIB=${ZENT_RATE_MIB:-%(rate_limit)s}
export ZENT_RATE_MIB
case "$RETRIES" in ''|*[!0-9]*) echo 'invalid RETRIES' >&2; exit 1;; esac
%(file_check)s
while IFS=$'\t' read -r idx target stamp duration offset length outfile; do
    printf '[%%s] %%s %%s: offset=%%s length=%%s\n' "$idx" "$target" "$outfile" "$offset" "$length"
    if [ "$duration" = '-' ]; then
        "$ZENT" --send --input "$IFILE" --skip 0 --length "$length" --bs 1 --host "$HOST" --port "$PORT" --connect-retries "$RETRIES"
    else
        skip=$(( (offset - BASE) / BS ))
        "$ZENT" --send --input "$IFILE" --offset "$BASE" --skip "$skip" --length "$duration" --bs "$BS" --host "$HOST" --port "$PORT" --connect-retries "$RETRIES"
    fi
done <<'FAST_ROWS'
%(rows)s
FAST_ROWS
'''

RECEIVER = r'''#!/bin/bash
set -eu
BIND=%(bind)s
PORT=%(port)s
ODIR=%(odir)s
ZENT=${ZENT:-zent}
case "$ODIR" in /*) ;; *) ODIR="$(cd -- "$(dirname -- "$0")" && pwd -P)/$ODIR";; esac
mkdir -p -- "$ODIR"
while IFS=$'\t' read -r idx target stamp duration offset length outfile; do
    printf '[%%s] %%s: waiting for %%s, offset=%%s length=%%s\n' "$idx" "$target" "$outfile" "$offset" "$length"
    "$ZENT" --recv --host "$BIND" --port "$PORT" --output "$ODIR/$outfile" --expect-offset "$offset" --expect-length "$length"
done <<'FAST_ROWS'
%(rows)s
FAST_ROWS
'''

class HelpFormatter(IndentedHelpFormatter):
    def format_epilog(self, epilog):
        return u'\n' + epilog + u'\n' if epilog else u''

def main():
    p = OptionParser(
        usage=u'%prog --ifile FILE (--host HOST | --corr NUM) --port PORT --odir DIR [options]',
        description=u'ファイル全体、またはDRGスケジュールのスキャンごとの送受信スクリプトと範囲一覧を生成し、受信スクリプトをscpで配置します。観測データの転送は実行しません。',
        epilog=u'例（受信側のアドレスは実際のIPに置き換えてください）:\n'
               u'  python mkzent.py --drg I26274X.DRG --log recording.log \\\n'
               u'    --ifile /data/input.vdif --corr 1 \\\n'
               u'    --port 9002 --odir /data/raw --ant 1\n'
               u'\n'
               u'ファイルを丸ごと転送（DRG、記録ログ、アンテナ番号は不要）:\n'
               u'  python mkzent.py --ifile /data/input.vdif --corr 1 \\\n'
               u'    --port 9002 --odir /data/raw\n'
               u'\n'
               u'生成されるファイル: zent_<入力名>_send.sh、_recv.sh、_manifest.tsv。\n'
               u'受信スクリプトを --odir へscpで転送します（--no-deploy で省略）。\n'
               u'一覧を確認し、受信スクリプトを先に起動してください。\n'
               u'送信側・受信側で zent を用意し、ZENT=/path/to/zent を指定できます。\n'
               u'\n'
               u'範囲: 開始バイト = file-offset + (スキャン開始UTC - 記録開始UTC) × bs、\n'
               u'      転送バイト数 = スキャン継続秒数 × bs。記録が連続している前提です。\n'
               u'説明と実行例の詳細: README_DRG.md',
        formatter=HelpFormatter())
    p.add_option('--drg', metavar='FILE', help=u'$SKED を含むDRG。省略すると --ifile を丸ごと転送する。')
    p.add_option('--log', metavar='FILE', help=u'DRG使用時に必要な記録ログ。$REQRECSTART YYYYDDDhhmmss を含む。--recstart とどちらか一方を指定。')
    p.add_option('--recstart', metavar='TIME', help=u'ログの代わりに記録開始UTCを指定（YYDDDhhmmss または YYYYDDDhhmmss）。')
    p.add_option('--ifile', metavar='FILE', help=u'必須。送信元VDIFファイルのパス。生成時に絶対パスに変換。')
    p.add_option('--host', metavar='HOST', help=u'送受信で共通に使う受信側のIPv4アドレスまたはホスト名。--corr とどちらか一方を指定。')
    p.add_option('--corr', metavar='NUM', help=u'送受信に共通の受信先番号。1,2,3,5,6,8 → corrN_10G、44 → Rocky、101 → yamaNAS1。--host と排他。')
    p.add_option('--bind', metavar='IP', help=u'任意。受信側の待ち受け先だけを別に指定。省略時は送信側で接続先をIPv4に解決して両側に設定。')
    p.add_option('--port', metavar='PORT', help=u'必須。送受信に使うTCPポート（9000〜9010）。同時受信ごとに別のポートを使用。')
    p.add_option('--odir', metavar='DIR', help=u'必須。受信ファイルの保存先。通常は受信スクリプトのscp転送先にも使用。')
    p.add_option('--ant', metavar='NUM', help=u'DRG使用時は必須。0: YAMAGU32/K、1: YAMAGU34/L。受信ファイル名に使用。')
    p.add_option('--bs', '--data-rate', dest='data_rate', metavar='BYTES', default='256000000', help=u'記録データの1秒あたりのバイト数（既定: 256000000）。')
    p.add_option('--rate-mib', metavar='N', default='0', help=u'送信速度の上限（MiB/秒、0〜1048576、既定: 0で無制限）。生成スクリプトは ZENT_RATE_MIB でも変更可能。')
    p.add_option('--file-offset', metavar='BYTES', default='0', help=u'記録開始時刻に対応するファイル内の基準バイト位置（既定: 0）。')
    p.add_option('--exclude-scan', metavar='LIST', action='append', default=[], help=u'除外するDRG順のスキャン番号または範囲。例: 2,5-7。繰り返し指定可能。')
    p.add_option('--exclude-target', metavar='LIST', action='append', default=[], help=u'除外する天体名をカンマ区切りで指定。大文字・小文字を区別しない。繰り返し指定可能。')
    p.add_option('--out-prefix', metavar='PATH', help=u'生成ファイルのパスと接頭辞（既定: カレントディレクトリの zent_<入力名>）。')
    p.add_option('--force', action='store_true', default=False, help=u'同じ名前のローカル生成ファイルを上書きする。既定では上書きしない。')
    p.add_option('--no-deploy', action='store_true', default=False, help=u'scpを実行せず、スクリプトと一覧をローカルに生成するだけにする。')
    opt, args = p.parse_args()
    if args:
        fail('unexpected positional arguments')
    for key in ('ifile','port','odir'):
        if not getattr(opt, key):
            fail('--%s is required' % key)
    if bool(opt.host) == bool(opt.corr):
        fail('specify exactly one of --host and --corr')
    if opt.corr:
        corr = number(opt.corr, 'correlator number')
        if corr not in CORRELATORS:
            fail('--corr must be one of 1,2,3,5,6,8,44,101')
        opt.host = CORRELATORS[corr]
    if not opt.no_deploy:
        if not re.match(r'^[A-Za-z0-9_.-]+$', opt.host) or opt.host.startswith('-'):
            fail('unsafe scp destination host; use --no-deploy for manual copying')
        if not re.match(r'^[A-Za-z0-9_./+\-]+$', opt.odir) or opt.odir.startswith('-'):
            fail('scp output directory must use letters, digits, _, -, +, . or /; use --no-deploy for other paths')
    port = number(opt.port, 'port', True)
    if port < 9000 or port > 9010:
        fail('port must be 9000..9010')
    rate = number(opt.data_rate, 'data rate')
    rate_limit = number(opt.rate_mib, 'rate limit', True)
    if rate_limit > 1048576:
        fail('--rate-mib must be 0..1048576')
    base = number(opt.file_offset, 'file offset', True)
    file_check = ''
    if opt.drg:
        if bool(opt.log) == bool(opt.recstart):
            fail('specify exactly one of --log and --recstart with --drg')
        if opt.ant not in ANTENNAS:
            fail('--ant must be 0 (YAMAGU32) or 1 (YAMAGU34)')
        antenna, station_code = ANTENNAS[opt.ant]
        rawstart = recording_start(opt.log) if opt.log else opt.recstart
        start = parse_time(rawstart)
        targets = set(x.strip().lower() for group in opt.exclude_target for x in group.split(','))
        if '' in targets:
            fail('empty target exclusion')
        rows = scans(opt.drg, start, rate, base, antenna, exclusions(opt.exclude_scan), targets)
    else:
        if opt.log or opt.recstart or opt.exclude_target or opt.exclude_scan or base:
            fail('--log, --recstart, --exclude-target, --exclude-scan and nonzero --file-offset require --drg')
        if opt.ant is not None and opt.ant not in ANTENNAS:
            fail('--ant must be 0 (YAMAGU32) or 1 (YAMAGU34)')
        if opt.ant is not None or opt.data_rate != '256000000':
            print('Note: --ant and --bs are not used for whole-file transfers', file=sys.stderr)
        info = os.stat(opt.ifile)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > LIMIT:
            fail('whole-file input must be a nonempty regular file within the 64-bit limit')
        filename = os.path.basename(opt.ifile)
        if not filename or any(c in filename for c in '\t\r\n'):
            fail('input filename must not contain a tab or newline')
        rows = [(1, 'FILE', '-', '-', 0, info.st_size, filename)]
        rate, base = 1, 0
        file_check = ('EXPECTED_SIZE=%s\n' % info.st_size +
                      'if [ "$(stat -c %s -- "$IFILE")" != "$EXPECTED_SIZE" ]; then\n'
                      '    echo "Input size changed since script generation: $IFILE" >&2\n'
                      '    exit 1\nfi')
    prefix = opt.out_prefix or ('zent_' + os.path.splitext(os.path.basename(opt.ifile))[0])
    data = '\n'.join('\t'.join(str(v) for v in row) for row in rows)
    recv_odir = opt.odir
    # When deployed to a relative --odir, the script itself is already there.
    if not opt.no_deploy and not os.path.isabs(opt.odir):
        recv_odir = '.'
    bind_addr = opt.bind or receiver_ipv4(opt.host)
    send_addr = opt.host if opt.bind else bind_addr
    config = dict(host=quote(send_addr), bind=quote(bind_addr), port=quote(str(port)), ifile=quote(os.path.abspath(opt.ifile)), odir=quote(recv_odir), rate=quote(str(rate)), rate_limit=quote(str(rate_limit)), base=quote(str(base)), rows=data, file_check=file_check)
    files = [(prefix+'_send.sh', SENDER % config, 0o755), (prefix+'_recv.sh', RECEIVER % config, 0o755),
             (prefix+'_manifest.tsv', 'idx\ttarget\tstart_utc\tduration_sec\toffset_bytes\tlength_bytes\toutfile\n'+data+'\n', 0o644)]
    write_files(files, opt.force)
    if opt.drg:
        print('Recording start: %s UTC; data rate: %s bytes/s; base offset: %s' % (rawstart, rate, base))
        print('Destination: %s; antenna: %s (%s)' % (opt.host, antenna, station_code))
    else:
        print('Whole-file transfer: %s bytes; destination: %s' % (info.st_size, opt.host))
    print('Transfer address: %s; receiver bind: %s' % (send_addr, bind_addr))
    for path, _, _ in files:
        print('%s %s' % ('Wrote' if opt.force else 'Created', path))
    if not opt.no_deploy:
        destination = '%s:%s/' % (opt.host, opt.odir.rstrip('/'))
        print('Deploying receiver to %s' % destination)
        sys.stdout.flush()
        try:
            result = subprocess.call(['scp', '-p', os.path.abspath(files[1][0]), destination])
        except OSError as e:
            fail('cannot run scp: %s; generated files were retained' % e)
        if result != 0:
            fail('scp failed with status %d; generated files were retained' % result)
        print('Receiver copied to %s' % destination)

if __name__ == '__main__':
    try:
        main()
    except (ValueError, IOError, OSError) as e:
        print('ERROR: %s' % e, file=sys.stderr)
        sys.exit(1)
