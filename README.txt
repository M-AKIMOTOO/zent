zent (Zero-copy Efficient Network Transfer)
==========================================

Build on each machine:
  make
Sender supports the GNU99/Linux APIs available on RHEL 6; Receiver targets
RHEL 8. clock_gettime is linked with -lrt for older glibc.

Install zent and mkzent.py in ~/bin:
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

The receiver handles one connection and exits. It creates a mode-0600 temporary
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
  --max-bytes N              reject larger transfers (default 2^63-1)
  --expect-offset N          require the source offset in the header to match
  --expect-length N          require the transfer length in the header to match
  --keep-part                retain incomplete file on handled failures
  --fsync                    fsync file before publishing
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
  --readahead auto|off       auto gives SEQUENTIAL advice; off gives no advice
  --prefetch-mib N           optional explicit window, 0..2047 (default 0)
  --progress-sec N / --no-progress

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

The protocol consists of a 32-byte network-order header (FASTRAW1, u32 version
1, u32 zero flags, u64 length, u64 source offset), exactly length bytes, then
sender half-close. Receiver checks EOF, publishes, and returns a 24-byte ACK
(FSTRACK1, u32 version 1, u32 status, u64 bytes written). One file per TCP
connection. Data is neither authenticated, encrypted nor checksummed. Restrict
the bind address and firewall to a trusted network; run unprivileged. A byte
count does not detect corrupted data. Verify with an external hash as needed.

If the optional local test files are present, make test runs them with Python 3.
They are not included in this repository. Python is not needed to build or run
zent itself. Test real RHEL systems, storage, network and representative data
before production use; performance is workload-dependent.

For DRG schedule-based script generation, see README_DRG.md.
