zent (Zero-copy Efficient Network Transfer)
==========================================

Build on each machine:
  make
Sender supports the GNU99/Linux APIs available on RHEL 6; Receiver targets
RHEL 8. clock_gettime is linked with -lrt for older glibc.

Install zent, mkzent.py, zent-run and zent-bench in ~/bin:
  make install
The destination directory is created if needed. Existing files with these
names are replaced. To choose another directory: make install BINDIR=/path/bin

On the receiver (choose an address on a trusted, isolated network):
  ./zent --recv --host 192.168.10.2 --port 9002 --output /data/output.raw

On the sender:
  ./zent --send --input /data/source.raw --skip 0 \
    --length 600 --host 192.168.10.2 --port 9002

Instead of --host, --corr selects a destination name: 1,2,3,5,6,8 map to
corrN_10G, 44 to Rocky, and 101 to yamaNAS1. Use the same --host or --corr on
sender and receiver. Both machines must resolve the name to an IPv4 address;
the receiver must own that address. --bind remains an alternative for selecting
a different local address on the receiver. No SSH connection is used.

Use --send or --recv as the first argument; ./zent or ./zent --help shows help,
and ./zent --send --help or ./zent --recv --help lists mode-specific options.
--port must be between 9000 and 9010 inclusive on both sides.
--length and --skip are integer seconds; --bs is bytes per second (default
256000000). Byte offset = --offset + --skip * --bs, byte length = --length *
--bs. --offset is an optional byte base (default 0). With --bs 1, --skip and
--length can specify exact byte counts. The range must be fully contained in
a regular file that must not be modified during transfer. Both processes return
zero only after the receiver has written and published the file and the sender
has received the matching success ACK. ACK loss after publication can leave a
completed output even though the sender reports failure; inspect it before retry.
Previous versions used bytes for --length. Update old commands and regenerate
both scripts produced by mkzent.py before using this version.

Without --resume, the receiver handles one connection and exits. It rejects an
existing output (including a dangling symlink) before opening the listener.
It creates a mode-0600 temporary
file alongside the destination. A hard link publishes it atomically without
overwriting an existing path, and then the temporary name is removed. The
destination filesystem must support hard links. On failure the temporary file
is removed by default; --keep-part retains it. Forced termination can also leave
a partial file. --fsync synchronizes the file before publication and ACK, but
does not sync the parent directory; it does not guarantee the name survives a
power failure. Existing output files are never overwritten.

The receiving address and port remain reserved during the transfer. If another
listener already uses an overlapping address and port, the receiver reports a
port conflict and exits. Use different ports for concurrent receivers. Binding
0.0.0.0 overlaps all local IPv4 addresses. The same port on different hosts,
or distinct local addresses where the OS permits it, does not conflict.

Receiver options:
  --receive-mode splice|rw   default splice; unsupported splice falls back to rw
  --write-batch-mib N        accumulate 1..64 MiB before each rw write (opt-in)
  --pipe-mib N               request a 1..16 MiB splice pipe (opt-in)
  --max-bytes N              reject larger transfers (default 2^63-1)
  --expect-offset N          require the source offset in the header to match
  --expect-length N          require the transfer length in the header to match
  --keep-part                retain incomplete file on handled failures
  --fsync                    fsync file before publishing
  --resume                   resume a matching transfer; see below
  --checkpoint-mib N        sync and record every N MiB (default 256, resume only)
  --transfer-retries N      await reconnection after N failures (default 3, resume)
  --idle-timeout SEC        socket/reconnect wait; default 120 with resume, else 0
  --progress-sec N           progress interval in seconds (default 1, 0 disables)
  --no-progress              disable progress

Sender options:
  --length SEC               required duration in seconds (positive integer)
  --skip SEC                 skip seconds (default 0)
  --bs BYTES                 bytes per second (default 256000000)
  --offset BYTES             byte base added to skip (default 0)
  --rate-mib N               average send limit in MiB/s (0 means unlimited)
  --send-chunk-mib N         sendfile request size, 1..2047 (default 16)
  --connect-retries N        retry refused connections N times, once per second
  --resume                   resume a matching transfer; see below
  --transfer-retries N      retry interrupted connections N times (default 3)
  --idle-timeout SEC        socket wait; default 120 with resume, else 0
  --readahead auto|off       auto gives SEQUENTIAL advice; off gives no advice
  --prefetch-mib N           optional explicit window, 0..2047 (default 0)
  --progress-sec N / --no-progress

Progress shows total bytes, recent/average payload rate and an estimated
remaining payload time. On resume, rate calculations exclude bytes already
confirmed by the receiver; final averages count bytes transferred during this
invocation (including any retransferred tail). ETA excludes final storage sync.
Progress updates one terminal line after I/O calls return, so blocked I/O may
delay updates. When stderr is redirected, periodic progress is suppressed;
completion or failure and elapsed time are still reported. --no-progress also
keeps the final report. Sender time runs from before connection (including any
retries) until ACK; receiver time runs from accept through file publication and
ACK. The reported average rate uses those elapsed times. Without --fsync, a
successful write does not imply the data has reached permanent storage.
Explicit readahead is advisory and does not guarantee cached bytes. It may
affect other memory users; benchmark on the target system before enabling it.

Use --rate-mib 100 to pace sending at up to about 100 MiB/s. Alternatively,
ZENT_RATE_MIB=100 sets the limit for zent and existing generated sender scripts;
an explicit --rate-mib overrides the environment variable. The default is 0
(unlimited). This is a per-sender data rate, not an I/O priority or a strict
instantaneous limit on the receiver's disk: page-cache writeback may be bursty.
When multiple senders run, their limits add together.

Resuming large transfers (opt-in):
  receiver: ./zent --recv --host 192.168.10.2 --port 9002 \
              --output /data/output.raw --resume
  sender:   ./zent --send --input /data/source.raw --length 600 \
              --host 192.168.10.2 --port 9002 --resume

Both sides must use this updated binary and --resume. The receiver confirms a
saved byte position before the sender transmits payload. Every 256 MiB by
default, the receiver fsyncs the partial file, fsyncs a new state file, then
atomically replaces the saved state. A handled connection failure also saves
the last written position. A forced termination may leave an unconfirmed tail;
the next invocation truncates that tail and retransfers it. --checkpoint-mib
changes this tradeoff between sync overhead and the amount to retransmit.
Resume always syncs checkpoints and final data, even without --fsync.

Source identity includes hostname, canonical source path, device/inode, size,
mtime/ctime including nanoseconds, and the exact offset/length. Changes to this
identity cause a refusal, retaining existing data. Growing observation files
are not supported by this mode. Do not edit partial data or state files.
This identity check is not a content checksum and cannot detect all corruption;
use an external content hash when needed. Directory entries are not fsynced;
this is not a guarantee against machine/storage power loss.

For output.raw, resume uses output.raw.zent.part, output.raw.zent.state and
output.raw.zent.lock beside the output, with mode 0600 for new files. An
advisory record lock prevents cooperating receivers from sharing a state.
The lock inode must remain in place. Do not rename/delete these files while a
receiver is running. Metadata/partial files must be regular files; symlinks are
refused. Only the verified unconfirmed tail of a matching partial is truncated.

After successful publication the state and lock remain. Repeating the SAME
transfer reuses the completed output after matching its identity, size and
mtime to the saved state; it transmits zero payload bytes. Unrelated existing
files, legacy outputs without matching state, or changed completed outputs
are never adopted or overwritten. Keep the sidecars to retain this feature.
The same receiver host/mount should be used when restarting.

On transient connection errors, --resume retries up to --transfer-retries
(default 3); a receiver waits --idle-timeout seconds for each reconnect.
The first accept still waits indefinitely. Zero disables the socket/reconnect
timeout. Timeouts cover connected socket calls, not initial connect(), blocked
disk/NFS writes or fsync. Initial connect() uses the operating system's timeout.
--connect-retries remains a separate control for refused initial/retry connects.
If an ACK is lost after the receiver exits, automatic reconnection may fail or
find the next scheduled receiver. Stop both scripts and restart the same resume
pair from its beginning: matching completed scans will be reused and the
unfinished scan will resume. No source/range mismatch is automatically ignored.

Optional receiver tuning:
  --receive-mode rw --write-batch-mib 4
  --receive-mode splice --pipe-mib 1
Batching accumulates a bounded block before writing it; the last block is exact
and may be shorter. It also applies to a splice-to-rw fallback. It can delay
progress updates on slow input. Pipe enlargement is best-effort: the actual OS
capacity is used if the request is denied. Defaults preserve the previous I/O
path. Compare on the actual NFS/storage workload; neither option guarantees a
speed increase or removes observation/correlation I/O contention.

The legacy protocol consists of a 32-byte network-order header (FASTRAW1, u32 version
1, u32 zero flags, u64 length, u64 source offset), exactly length bytes, then
sender half-close. Receiver checks EOF, publishes, and returns a 24-byte ACK
(FSTRACK1, u32 version 1, u32 status, u64 bytes written). One file per TCP
connection. With --resume, version 2 uses flags=1 and appends source metadata:
seven u64 values (device, inode, size, mtime seconds/ns, ctime seconds/ns), two
u32 lengths, and NUL-terminated hostname/canonical-path strings (8192 bytes
combined maximum). The receiver sends a 24-byte ZENTRDY2/version=2/status/offset
reply before payload. Only length-offset bytes follow, then a 16-byte ZENTEND2
and u64 original length, followed by half-close. The end marker is sent only
after rechecking source identity. The final FSTRACK1 ACK uses version 2 and
the full original length. Failures have nonzero status. The .zent.state file
holds a 64-byte ZENTRS02 record followed by the exact version-2 source header.
It records the synced byte position and the receiving inode/size/mtime.
Data is neither authenticated, encrypted nor checksummed. Restrict
the bind address and firewall to a trusted network; run unprivileged. A byte
count does not detect corrupted data. Verify with an external hash as needed.

Generated one-command launcher:
  ./zent_<input>_run.sh

mkzent.py also writes a _run.sh beside the sender/receiver scripts. It calls
zent-run to start the current local receiver script over SSH, waits for its
actual listener message, then starts the sender. It works with --no-deploy;
no separate receiver script copy is needed. Data uses zent's direct TCP path;
SSH is used only for process control, script delivery and log output. It uses
the original --host/--corr SSH name, preserving SSH configuration aliases.
Relative output directories start at the remote home directory in this mode.

Set REMOTE_ZENT=/path/to/zent for the remote binary, ZENT=/path/to/zent for the
local sender, and ZENT_RUN=/path/to/zent-run if the helper is outside PATH.
Without REMOTE_ZENT, the remote uses zent in PATH, or ~/bin/zent. Receiver tuning
variables and resume retry/timeout settings are forwarded over SSH. The default
listener wait is 60 seconds; _run.sh --ready-timeout 300 changes it. SSH uses
normal authentication/settings and requires bash and setsid on the receiver.

The launcher prints both sides' progress and creates a mode-0600 combined UTC
log beside the sender script. Use _run.sh --log FILE to choose its path; existing
logs are never overwritten. ZENT_PROGRESS_LOG=1 enables periodic newline-based
progress even without a terminal; otherwise redirected progress remains quiet.
On local failure/interruption/control-channel EOF, the launcher terminates the
receiver process group that it created. It does not stop unrelated receivers.
Uninterruptible disk/NFS operations can still delay process termination.

Reproducible receiver comparison (run on PC2 to write through its NFS mount):
  zent-bench --input /local/stable.raw --directory /mnt/corr8_10G \
    --offset 0 --length 268435456 --runs 3 --csv receiver.csv

The source must already exist and remain unchanged. Default length is up to
256 MiB; an explicit --length is an exact byte count. This compares splice,
enlarged pipe, rw and 4/16-MiB batched writes on loopback, rotates case order,
includes fsync and final ACK in elapsed time, and verifies every output against
the selected source range using SHA-256. Hash time is excluded; the source is
read once before timing, so this is a warm-cache comparison. --resume includes
checkpoint overhead. --cases splice,rw selects a subset. --zent selects the
binary, --port selects a free local port in 9000..9010. It creates/removes its
own temporary output directory; input/existing outputs are not modified.
It writes the selected byte count for EACH mode/repeat and reads each result
for verification. It measures receiver/storage behavior, not the PC1-to-PC2
10Gbps link; it neither selects a mode automatically nor promises a speedup.

If the optional local test files are present, make test runs them with Python 3.
They are not included in this repository. Python is not needed to build or run
zent itself. The launcher and benchmark helpers need Python 2.6+ or Python 3.
Test real RHEL systems, storage, network and representative data
before production use; performance is workload-dependent.

For DRG schedule-based script generation, see README_DRG.md.
