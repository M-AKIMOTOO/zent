#!/usr/bin/env python3
import os
import pathlib
import subprocess
import sys
import tempfile
import time

root = pathlib.Path(__file__).resolve().parent
generator = root / 'mkzent.py'
help_result = subprocess.run([sys.executable, str(generator), '--help'],
                             check=True, capture_output=True, text=True)
for option in ('--drg', '--log', '--recstart', '--ifile', '--host', '--corr',
               '--bind', '--port', '--odir', '--ant', '--bs', '--file-offset',
               '--exclude-scan', '--exclude-target', '--out-prefix', '--no-deploy', '--force', '--rate-mib'):
    assert option in help_result.stdout, option
assert 'スケジュール' in help_result.stdout and '例（' in help_result.stdout
with tempfile.TemporaryDirectory(prefix='schedule test ') as temp:
    work = pathlib.Path(temp)
    drg = work / 'input.DRG'
    log = work / 'recording.log'
    source = work / "source's data.vdif"
    out = work / 'received data'
    drg.write_text('$EXPER EXAMPLE\n$SKED\n* comment\n'
                   'A 10 S2 PREOB 26365235959 2 MIDOB 0 POSTOB K- 1F\n'
                   'B 10 S2 PREOB 27001000001 3 MIDOB 0 POSTOB K- 1F\n$HEAD\n')
    log.write_text('$REQRECSTART 2026365235959\n')
    # Keep the New Year test small while proving that 2026 has 365 days.
    source.write_bytes(bytes(range(128)))
    prefix = str(work / 'pair')
    command = [sys.executable, str(generator), '--drg', str(drg), '--log', str(log),
               '--ifile', str(source), '--host', '127.0.0.1',
               '--port', '0', '--odir', str(out), '--ant', '1', '--bs', '4',
               '--file-offset', '3', '--out-prefix', prefix, '--no-deploy']
    # Select an unused port in the supported range without connecting to it.
    import socket
    for port in range(9000, 9011):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            s.close()
            continue
        s.close()
        break
    else:
        raise RuntimeError('no free test port in 9000..9010')
    command[command.index('--port') + 1] = str(port)
    rate_command = command + ['--rate-mib', '2', '--out-prefix', str(work / 'rate')]
    subprocess.run(rate_command, check=True, capture_output=True)
    assert "ZENT_RATE_MIB=${ZENT_RATE_MIB:-'2'}" in (work / 'rate_send.sh').read_text()
    invalid_rate = command + ['--rate-mib', '1048577', '--out-prefix', str(work / 'bad-rate')]
    assert subprocess.run(invalid_rate, capture_output=True).returncode != 0
    for bad_port in ('8999', '9011'):
        bad = command[:]
        bad[bad.index('--port') + 1] = bad_port
        result = subprocess.run(bad, capture_output=True)
        assert result.returncode != 0 and b'9000..9010' in result.stderr
    for boundary in ('9000', '9010'):
        valid = command[:]
        valid[valid.index('--port') + 1] = boundary
        valid[valid.index('--out-prefix') + 1] = str(work / ('boundary' + boundary))
        subprocess.run(valid, check=True, capture_output=True)
    # Test deployment without making a network connection.
    mockbin = work / 'mockbin'
    mockbin.mkdir()
    scp = mockbin / 'scp'
    scp.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$SCP_LOG"\nexit "${SCP_STATUS:-0}"\n')
    scp.chmod(0o755)
    scp_log = work / 'scp-args'
    scp_env = dict(os.environ, PATH=str(mockbin) + os.pathsep + os.environ.get('PATH', ''),
                   SCP_LOG=str(scp_log))
    deploy = [x for x in command if x != '--no-deploy']
    deploy[deploy.index('--out-prefix') + 1] = str(work / 'deployed')
    deploy[deploy.index('--odir') + 1] = '/data/raw'
    subprocess.run(deploy, check=True, capture_output=True, env=scp_env)
    assert scp_log.read_text().splitlines() == ['-p', str(work / 'deployed_recv.sh'), '127.0.0.1:/data/raw/']
    deploy[deploy.index('--out-prefix') + 1] = str(work / 'relative')
    deploy[deploy.index('--odir') + 1] = 'relative/raw'
    subprocess.run(deploy, check=True, capture_output=True, env=scp_env)
    assert scp_log.read_text().splitlines()[-1] == '127.0.0.1:relative/raw/'
    assert "ODIR='.'" in (work / 'relative_recv.sh').read_text()
    deploy[deploy.index('--out-prefix') + 1] = str(work / 'failed-copy')
    failed_env = dict(scp_env, SCP_STATUS='1')
    result = subprocess.run(deploy, capture_output=True, env=failed_env)
    assert result.returncode != 0 and b'generated files were retained' in result.stderr
    assert (work / 'failed-copy_send.sh').exists() and (work / 'failed-copy_recv.sh').exists()
    # Check the non-leap-year rollover: second scan is two seconds later.
    subprocess.run(command, check=True, capture_output=True)
    rows = (work / 'pair_manifest.tsv').read_text().splitlines()
    assert rows[1].split('\t')[4:6] == ['3', '8']
    assert rows[2].split('\t')[4:6] == ['11', '12']
    hosts = {1: 'corr1_10G', 2: 'corr2_10G', 3: 'corr3_10G',
             5: 'corr5_10G', 6: 'corr6_10G', 8: 'corr8_10G',
             44: 'Rocky', 101: 'yamaNAS1'}
    host_pos = command.index('--host')
    for num, host in hosts.items():
        by_corr = command[:host_pos] + ['--corr', str(num), '--bind', '127.0.0.1'] + command[host_pos+2:]
        by_corr[by_corr.index('--out-prefix') + 1] = str(work / ('corr%d' % num))
        generated = subprocess.run(by_corr, check=True, capture_output=True, text=True)
        assert ('Destination: %s; antenna: YAMAGU34 (L)' % host) in generated.stdout
        assert ("HOST='%s'" % host) in (work / ('corr%d_send.sh' % num)).read_text()
        assert "BIND='127.0.0.1'" in (work / ('corr%d_recv.sh' % num)).read_text()
    by_corr = command[:host_pos] + ['--corr', '44', '--bind', '127.0.0.1'] + command[host_pos+2:]
    by_corr[by_corr.index('--ant') + 1] = '0'
    by_corr[by_corr.index('--out-prefix') + 1] = str(work / 'antenna0')
    generated = subprocess.run(by_corr, check=True, capture_output=True, text=True)
    assert 'antenna: YAMAGU32 (K)' in generated.stdout
    assert 'YAMAGU32_' in (work / 'antenna0_manifest.tsv').read_text()
    assert subprocess.run(command + ['--corr', '1'], capture_output=True).returncode != 0
    invalid = command[:host_pos] + ['--corr', '4'] + command[host_pos+2:]
    assert subprocess.run(invalid, capture_output=True).returncode != 0
    override = command + ['--bind', '127.0.0.2', '--out-prefix', str(work / 'override')]
    subprocess.run(override, check=True, capture_output=True)
    assert "BIND='127.0.0.2'" in (work / 'override_recv.sh').read_text()
    # Only the generator/sender can resolve the host alias. Generated scripts
    # must use the selected IPv4 address on both sides.
    mockdns = work / 'mockdns'
    mockdns.mkdir()
    (mockdns / 'sitecustomize.py').write_text(
        'import socket\n'
        '_original = socket.getaddrinfo\n'
        'def _lookup(host, *args, **kwargs):\n'
        '    if host == "corr6_10G": host = "127.0.0.1"\n'
        '    return _original(host, *args, **kwargs)\n'
        'socket.getaddrinfo = _lookup\n')
    by_dns = command[:host_pos] + ['--corr', '6'] + command[host_pos+2:]
    by_dns[by_dns.index('--out-prefix') + 1] = str(work / 'dns')
    by_dns[by_dns.index('--odir') + 1] = str(work / 'dns output')
    dns_env = dict(os.environ, PYTHONPATH=str(mockdns))
    generated = subprocess.run(by_dns, check=True, capture_output=True, text=True, env=dns_env)
    assert 'Transfer address: 127.0.0.1; receiver bind: 127.0.0.1' in generated.stdout
    assert "HOST='127.0.0.1'" in (work / 'dns_send.sh').read_text()
    assert "BIND='127.0.0.1'" in (work / 'dns_recv.sh').read_text()
    # Existing generated output is not overwritten.
    before = (work / 'pair_send.sh').read_bytes()
    duplicate = subprocess.run(command, capture_output=True)
    assert duplicate.returncode != 0 and b'--force' in duplicate.stderr and b'--out-prefix' in duplicate.stderr
    assert (work / 'pair_send.sh').read_bytes() == before
    # Explicit regeneration replaces the three files with the new settings.
    subprocess.run(command + ['--force', '--file-offset', '4'], check=True, capture_output=True)
    assert (work / 'pair_manifest.tsv').read_text().splitlines()[1].split('\t')[4] == '4'
    assert "BASE='4'" in (work / 'pair_send.sh').read_text()
    subprocess.run(command + ['--force'], check=True, capture_output=True)
    assert (work / 'pair_manifest.tsv').read_text().splitlines()[1].split('\t')[4] == '3'
    # Refuse --force if a destination is not a regular file, without changing others.
    (work / 'pair_manifest.tsv').unlink()
    (work / 'pair_manifest.tsv').symlink_to(source)
    before = (work / 'pair_send.sh').read_bytes()
    result = subprocess.run(command + ['--force'], capture_output=True)
    assert result.returncode != 0 and b'only replaces regular files' in result.stderr
    assert (work / 'pair_send.sh').read_bytes() == before
    assert source.read_bytes() == bytes(range(128))
    (work / 'pair_manifest.tsv').unlink()
    subprocess.run(command + ['--force'], check=True, capture_output=True)
    # Exercise multiple consecutive listeners and transfers.
    env = dict(os.environ, ZENT=str(root / 'zent'))
    recv = subprocess.Popen(['bash', str(work / 'pair_recv.sh')], env=env, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        time.sleep(0.1)
        send = subprocess.run(['bash', str(work / 'pair_send.sh')], env=env, capture_output=True, timeout=10)
        stdout, stderr = recv.communicate(timeout=10)
        assert send.returncode == 0, send.stderr
        assert recv.returncode == 0, (stdout, stderr)
        assert (out / 'YAMAGU34_2026365235959.raw').read_bytes() == source.read_bytes()[3:11]
        assert (out / 'YAMAGU34_2027001000001.raw').read_bytes() == source.read_bytes()[11:23]
    finally:
        if recv.poll() is None:
            recv.kill(); recv.wait()
    dns_recv = subprocess.Popen(['bash', str(work / 'dns_recv.sh')], env=env,
                                stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        time.sleep(0.1)
        dns_send = subprocess.run(['bash', str(work / 'dns_send.sh')], env=env, capture_output=True, timeout=10)
        stdout, stderr = dns_recv.communicate(timeout=10)
        assert dns_send.returncode == 0, dns_send.stderr
        assert dns_recv.returncode == 0, (stdout, stderr)
        assert (work / 'dns output' / 'YAMAGU34_2026365235959.raw').read_bytes() == source.read_bytes()[3:11]
    finally:
        if dns_recv.poll() is None:
            dns_recv.kill(); dns_recv.wait()
    # Without DRG, transfer the entire file, including non-aligned byte counts.
    whole = [sys.executable, str(generator), '--ifile', str(source), '--host', '127.0.0.1',
             '--port', str(port), '--odir', str(work / 'whole output'),
             '--out-prefix', str(work / 'whole'), '--no-deploy']
    generated_whole = subprocess.run(whole, capture_output=True)
    assert generated_whole.returncode == 0, generated_whole.stderr
    whole_rows = (work / 'whole_manifest.tsv').read_text().splitlines()
    assert whole_rows[1].split('\t')[3:6] == ['-', '0', '128']
    assert whole_rows[1].split('\t')[6] == source.name
    whole_deploy = [x for x in whole if x != '--no-deploy']
    whole_deploy[whole_deploy.index('--odir') + 1] = '/data/raw'
    whole_deploy[whole_deploy.index('--out-prefix') + 1] = str(work / 'whole-deployed')
    subprocess.run(whole_deploy, check=True, capture_output=True, env=scp_env)
    assert scp_log.read_text().splitlines() == ['-p', str(work / 'whole-deployed_recv.sh'), '127.0.0.1:/data/raw/']
    source.write_bytes(bytes(range(129)))
    changed = subprocess.run(['bash', str(work / 'whole_send.sh')], env=env, capture_output=True, timeout=5)
    assert changed.returncode != 0 and b'Input size changed' in changed.stderr
    source.write_bytes(bytes(range(128)))
    whole_recv = subprocess.Popen(['bash', str(work / 'whole_recv.sh')], env=env,
                                  stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        time.sleep(0.1)
        whole_send = subprocess.run(['bash', str(work / 'whole_send.sh')], env=dict(env, ZENT_RATE_MIB='1'), capture_output=True, timeout=10)
        stdout, stderr = whole_recv.communicate(timeout=10)
        assert whole_send.returncode == 0 and b'Send rate limit: 1 MiB/s' in whole_send.stderr, whole_send.stderr
        assert whole_recv.returncode == 0, (stdout, stderr)
        assert (work / 'whole output' / source.name).read_bytes() == source.read_bytes()
    finally:
        if whole_recv.poll() is None:
            whole_recv.kill(); whole_recv.wait()
    empty = work / 'empty.vdif'
    empty.write_bytes(b'')
    bad = whole[:]
    bad[bad.index('--ifile') + 1] = str(empty)
    bad[bad.index('--out-prefix') + 1] = str(work / 'empty')
    assert subprocess.run(bad, capture_output=True).returncode != 0
    # Malformed or ambiguous log and a scan before recording are rejected.
    command += ['--exclude-scan', '2', '--out-prefix', str(work / 'single')]
    subprocess.run(command, check=True, capture_output=True)
    assert len((work / 'single_manifest.tsv').read_text().splitlines()) == 2
    command[-1] = str(work / 'invalid')
    log.write_text('$REQRECSTART 2026365235959\n$REQRECSTART 2026365235959\n')
    assert subprocess.run(command, capture_output=True).returncode != 0
    log.write_text('$REQRECSTART 2027001000000\n')
    assert subprocess.run(command, capture_output=True).returncode != 0
print('All schedule generator tests passed')
