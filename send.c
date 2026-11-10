#include "common.h"
#include "resume.h"
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <netdb.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sendfile.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
void zent_send_usage(FILE *f) {
 fputs("zent --send — ファイルの指定範囲を送信\n"
 "\n"
 "使い方:\n"
 "  zent --send --input FILE --length SEC (--host HOST | --corr NUM) --port PORT [オプション]\n"
 "\n"
 "必須:\n"
 "  --input FILE          送信元の通常ファイル\n"
 "  --length SEC          送信する秒数（10進整数、1以上）\n"
 "  --host HOST           受信側のIPv4アドレスまたはホスト名\n"
 "  --corr NUM            受信側を番号で指定（--host とどちらか一方）\n"
 "                        1,2,3,5,6,8: corrN_10G / 44: Rocky / 101: yamaNAS1\n"
 "  --port PORT           受信側のTCPポート（9000〜9010）\n"
 "\n"
 "オプション:\n"
 "  --skip SEC            スキップする秒数（10進整数、既定: 0）\n"
 "  --bs BYTES            1秒あたりのバイト数（既定: 256000000 = 256 MB）\n"
 "  --offset BYTES        秒数換算に加算する基準バイト位置（既定: 0）\n"
 "  --rate-mib N          送信上限（MiB/秒、0〜1048576、0で無制限）\n"
 "                        未指定時は環境変数 ZENT_RATE_MIB、なければ0\n"
 "  --send-chunk-mib N    1回のsendfile要求量（1〜2047 MiB、既定: 16）\n"
 "  --readahead auto|off  auto: 順次読み出しをOSに助言（既定）\n"
 "                        off: 助言しない。OSの先読みは無効になりません\n"
 "  --prefetch-mib N      明示的な先読み窓（0〜2047 MiB、既定: 0で無効）\n"
 "  --connect-retries N   接続拒否時に接続前だけ再試行（1秒間隔、既定: 0）\n"
 "  --resume              両側で指定。確認済みの位置から再開する\n"
 "  --transfer-retries N  --resume 時の通信切断後の再試行（既定: 3）\n"
 "  --idle-timeout SEC    ソケットの無通信待ち上限（resume時の既定: 120秒）\n"
 "  --progress-sec N      端末上での進捗更新間隔（秒、既定: 1、0で無効）\n"
 "  --no-progress         進捗表示を無効にする\n"
 "  --help, -h            このヘルプを表示\n", f);
}
void zent_send_examples(FILE *f) {
 fputs(
 "\n"
 "開始位置 = --offset + --skip × --bs、送信バイト数 = --length × --bs。\n"
 "秒数は整数です。--offset と --bs の単位はバイトです。\n"
 "--rate-mib は転送速度の目安で、記録レートを指定する --bs とは別です。\n"
 "両側に --resume を付けると、同期済み位置から再開します。更新版が両側に\n"
 "必要です。送信元のホスト・絶対パス・ファイル情報・範囲を照合するため、\n"
 "再開前に送信元を変更・移動しないでください。内容のハッシュ照合はしません。\n"
 "\n"
 "例: 受信側を先に起動し、先頭から600秒分を送信する:\n"
 "  ./zent --send --input source.vdif --skip 0 --length 600 \\\n"
 "    --host 192.168.10.2 --port 9002\n"
 "  既定の --bs では153600000000バイトを送信します。\n"
 "\n"
 "例: 10秒分スキップし、120秒分を1秒あたり128000000バイトで送信する:\n"
 "  ./zent --send --input source.vdif --skip 10 --length 120 --bs 128000000 \\\n"
 "    --host 192.168.10.2 --port 9002\n"
 "\n"
 "例: バイト位置604248から1048576バイトを正確に送信する（--bs 1）:\n"
 "  ./zent --send --input source.vdif --skip 604248 --length 1048576 --bs 1 \\\n"
 "    --host 192.168.10.2 --port 9002\n"
 "\n"
 "注意: 以前の版の --length はバイト単位でした。以前の送信コマンドや\n"
 "生成スクリプトはそのまま使わず、秒数に修正するか再生成してください。\n"
 "指定範囲はファイル内に収まる必要があります。転送中は元ファイルを変更\n"
 "しないでください。受信側の成功応答を確認した場合にのみ成功終了します。\n"
 "応答が失われた場合は、送信側が失敗していても受信ファイルが完成している\n"
 "ことがあります。再実行前に受信側を確認してください。\n"
 "端末では進捗を1行で更新します。ログへリダイレクトした場合は途中経過を\n"
 "省き、完了・失敗時に経過時間と平均速度を表示します。送信側の経過時間は\n"
 "接続開始から成功応答までで、接続の再試行時間も含みます。\n", f);
}
static int connect_to(const char *host, const char *port, unsigned retries) {
 struct addrinfo hints,*list,*p; int fd=-1, e, saved=0; unsigned attempt=0; memset(&hints,0,sizeof(hints)); hints.ai_family=AF_INET; hints.ai_socktype=SOCK_STREAM;
 e=getaddrinfo(host,port,&hints,&list); if(e) { fprintf(stderr,"getaddrinfo: %s\n",gai_strerror(e)); return -1; }
 do {
  for(p=list;p;p=p->ai_next) { fd=socket(p->ai_family,p->ai_socktype,p->ai_protocol); if(fd<0) { saved=errno; continue; } if(connect(fd,p->ai_addr,p->ai_addrlen)==0) break; saved=errno; close(fd); fd=-1; }
  if(fd>=0||saved!=ECONNREFUSED||attempt>=retries) break;
  ++attempt; fprintf(stderr,"Connection refused; retry %u/%u in 1 second\n",attempt,retries); sleep(1);
 } while(1);
 freeaddrinfo(list); if(fd<0) { errno=saved; perror("connect"); errno=saved; } return fd;
}
static int pace_until(double deadline) {
 for (;;) {
  double now=now_sec(), remaining;
  struct timespec delay;
  if(now<=0) { errno=EIO; return -1; }
  remaining=deadline-now;
  if(remaining<=0) return 0;
  delay.tv_sec=(time_t)remaining;
  delay.tv_nsec=(long)((remaining-(double)delay.tv_sec)*1000000000.0);
  if(nanosleep(&delay,NULL)<0 && errno!=EINTR) return -1;
 }
}
int zent_send(int argc,char **argv) {
 enum { INPUT=256, OFFSET, SKIP, BS, LENGTH, HOST, CORR, PORT, RATE, CHUNK, READAHEAD, PREFETCH, RETRIES, PROGRESS, NO_PROGRESS, RESUME, TRANSFER_RETRIES, TIMEOUT };
 static const struct option opts[]={ {"input",1,0,INPUT},{"offset",1,0,OFFSET},{"skip",1,0,SKIP},{"bs",1,0,BS},{"length",1,0,LENGTH},{"host",1,0,HOST},{"corr",1,0,CORR},{"port",1,0,PORT},{"rate-mib",1,0,RATE},{"send-chunk-mib",1,0,CHUNK},{"readahead",1,0,READAHEAD},{"prefetch-mib",1,0,PREFETCH},{"connect-retries",1,0,RETRIES},{"progress-sec",1,0,PROGRESS},{"no-progress",0,0,NO_PROGRESS},{"resume",0,0,RESUME},{"transfer-retries",1,0,TRANSFER_RETRIES},{"idle-timeout",1,0,TIMEOUT},{"help",0,0,'h'},{0,0,0,0} };
 const char *input=NULL,*host=NULL,*port=NULL,*corr=NULL; uint64_t offset=0,length=0,base=0,skip=0,seconds=0,bs=256000000,rate_mib=0,chunk=16,prefetch=0,x,done=0; int havelen=0,fd=-1,sock=-1,c,rc=1,seq=1; unsigned interval=1; struct stat st; off_t pos; unsigned char h[HEADER_SIZE],ack[ACK_SIZE]; double start=0,last=0,deadline=0;
 unsigned retries=0; int timed=0,have_rate=0; const char *env_rate;
 unsigned transfer_retries=3,attempt=0,timeout=0; int resume=0,have_timeout=0,have_transfer_retries=0;
 uint64_t transmitted=0; struct transfer_id identity,current;
 while((c=getopt_long(argc,argv,"h",opts,NULL))!=-1) switch(c) {
 case INPUT: input=optarg; break; case HOST: host=optarg; break; case CORR: corr=optarg; break; case PORT: port=optarg; break;
 case OFFSET: if(parse_u64(optarg,&base)) goto bad; break;
 case SKIP: if(parse_u64(optarg,&skip)) goto bad; break;
 case BS: if(parse_u64(optarg,&bs)||!bs) goto bad; break;
 case LENGTH: if(parse_u64(optarg,&seconds)) goto bad; havelen=1; break;
 case RATE: if(parse_u64(optarg,&rate_mib)||rate_mib>1048576) goto bad; have_rate=1; break;
 case CHUNK: if(parse_u64(optarg,&chunk)||!chunk||chunk>2047) goto bad; break;
 case PREFETCH: if(parse_u64(optarg,&prefetch)||prefetch>2047) goto bad; break;
 case RETRIES: if(parse_u64(optarg,&x)||x>86400) goto bad; retries=(unsigned)x; break;
 case RESUME: resume=1; break;
 case TRANSFER_RETRIES: if(parse_u64(optarg,&x)||x>86400) goto bad; transfer_retries=(unsigned)x; have_transfer_retries=1; break;
 case TIMEOUT: if(parse_u64(optarg,&x)||x>86400) goto bad; timeout=(unsigned)x; have_timeout=1; break;
 case READAHEAD: if(!strcmp(optarg,"auto")) seq=1; else if(!strcmp(optarg,"off")) seq=0; else goto bad; break;
 case PROGRESS: if(parse_u64(optarg,&x)||x>86400) goto bad; interval=(unsigned)x; break; case NO_PROGRESS: interval=0; break;
 case 'h': zent_send_usage(stdout); zent_send_examples(stdout); return 0; default: goto bad;
 }
 if(optind!=argc||!input||!port||!havelen||!seconds||!!host==!!corr) goto bad;
 if(have_transfer_retries && !resume) goto bad;
 if(resume && !have_timeout) timeout=120;
 env_rate=getenv("ZENT_RATE_MIB");
 if(!have_rate && env_rate && *env_rate && (parse_u64(env_rate,&rate_mib)||rate_mib>1048576)) { fprintf(stderr,"Invalid ZENT_RATE_MIB: use an integer from 0 to 1048576\n"); goto out; }
 if(!valid_port(port)) { fprintf(stderr,"Invalid --port: choose a number from 9000 to 9010\n"); goto out; }
 if(corr) {
  host=correlator_host(corr);
  if(!host) { fprintf(stderr,"Invalid --corr: choose 1, 2, 3, 5, 6, 8, 44 or 101\n"); goto out; }
 }
 if(bs>(uint64_t)INT64_MAX || base>(uint64_t)INT64_MAX || skip>((uint64_t)INT64_MAX-base)/bs || seconds>(uint64_t)INT64_MAX/bs) {
  fprintf(stderr,"Transfer range exceeds the supported 64-bit limit\n"); goto out;
 }
 offset=base+skip*bs; length=seconds*bs;
 if(length>(uint64_t)INT64_MAX-offset) { fprintf(stderr,"Transfer range exceeds the supported 64-bit limit\n"); goto out; }
 fd=open(input,O_RDONLY); if(fd<0) { perror("open input"); goto out; } if(fstat(fd,&st)<0) { perror("fstat"); goto out; }
 if(!S_ISREG(st.st_mode)||st.st_size<0||offset>(uint64_t)st.st_size||length>(uint64_t)st.st_size-offset) { fprintf(stderr,"Input must be a regular file containing the requested range\n"); goto out; }
 if(resume && source_identity(fd,input,offset,length,&identity)<0) { perror("source identity"); goto out; }
 if(seq) { c=posix_fadvise(fd,(off_t)offset,(off_t)length,POSIX_FADV_SEQUENTIAL); if(c) fprintf(stderr,"posix_fadvise: %s (continuing)\n",strerror(c)); }
 signal(SIGPIPE,SIG_IGN);
 start=last=now_sec(); timed=1;
 chunk*=MIB; prefetch*=MIB;
 if(rate_mib) {
  uint64_t pace_chunk=(rate_mib*MIB)/20;
  if(pace_chunk>4*MIB) pace_chunk=4*MIB;
  if(chunk>pace_chunk) chunk=pace_chunk;
  fprintf(stderr,"Send rate limit: %llu MiB/s (average)\n",(unsigned long long)rate_mib);
 }
 attempt_transfer:
 if(resume && (source_identity(fd,input,offset,length,&current)<0 || current.size!=identity.size || memcmp(current.bytes,identity.bytes,identity.size))) {
  fprintf(stderr,"Source changed; refusing to resume\n"); goto out;
 }
 fprintf(stderr,"Connecting to %s:%s\n",host,port);
 sock=connect_to(host,port,retries); if(sock<0) { if(resume && attempt) goto disconnected; goto out; }
 if(socket_timeout(sock,timeout)<0) { perror("socket timeout"); goto out; }
 memset(h,0,sizeof(h)); memcpy(h,"FASTRAW1",8); put32(h+8,1); put64(h+16,length); put64(h+24,offset);
 if(write_full(sock,resume?identity.bytes:h,resume?identity.size:sizeof(h))<0) { progress_perror("send header"); goto disconnected; }
 done=0; deadline=0;
 if(resume) {
  if(read_full(sock,ack,sizeof(ack))<0) { progress_perror("read READY"); goto disconnected; }
  if(memcmp(ack,"ZENTRDY2",8) || get32(ack+8)!=2 || get32(ack+12)!=0 || get64(ack+16)>length) {
   fprintf(stderr,"Receiver rejected resume or is incompatible; use --resume on both updated binaries\n"); goto out;
  }
  done=get64(ack+16);
  fprintf(stderr,"Confirmed: %llu/%llu bytes; sending %llu remaining bytes\n",(unsigned long long)done,(unsigned long long)length,(unsigned long long)(length-done));
 }
 pos=(off_t)(offset+done);
 progress_begin(done,now_sec()); last=now_sec();
 while(done<length) {
  size_t nreq=(size_t)((length-done<chunk)?length-done:chunk); ssize_t n; double before=0;
  if(prefetch) { uint64_t remain=length-done, ahead=remain<prefetch?remain:prefetch; if(readahead(fd,pos,(size_t)ahead)<0) { progress_perror("readahead (continuing)"); prefetch=0; } }
  if(rate_mib) { before=now_sec(); if(before<=0) { progress_clear(); fprintf(stderr,"Cannot read monotonic clock for rate limit\n"); goto out; } if(deadline<before) deadline=before; }
  n=sendfile(sock,fd,&pos,nreq); if(n<0) { if(errno==EINTR) continue; progress_perror("sendfile"); goto disconnected; } if(!n) { progress_clear(); fprintf(stderr,"Unexpected input EOF\n"); goto out; } done+=(uint64_t)n; transmitted+=(uint64_t)n;
  if(rate_mib) { deadline+=(double)n/((double)rate_mib*1048576.0); if(pace_until(deadline)<0) { progress_perror("rate limit wait"); goto out; } }
  progress("sent",done,length,start,&last,interval);
 }
 progress_clear();
 if(resume && (source_identity(fd,input,offset,length,&current)<0 || current.size!=identity.size || memcmp(current.bytes,identity.bytes,identity.size))) { fprintf(stderr,"Source changed during transfer\n"); goto out; }
 if(resume) {
  unsigned char end[16]; memcpy(end,"ZENTEND2",8); put64(end+8,length);
  if(write_full(sock,end,sizeof(end))<0) { progress_perror("send end marker"); goto disconnected; }
 }
 if(shutdown(sock,SHUT_WR)<0) { progress_perror("shutdown"); goto disconnected; }
 if(resume) fprintf(stderr,"Payload sent; waiting for receiver to save and acknowledge\n");
 if(read_full(sock,ack,sizeof(ack))<0) { progress_perror("read ACK"); goto disconnected; }
 if(memcmp(ack,"FSTRACK1",8)||get32(ack+8)!=(resume?2U:1U)||get32(ack+12)!=0||get64(ack+16)!=length) { fprintf(stderr,"Receiver failed or sent an invalid ACK (status=%u, bytes=%llu)\n",get32(ack+12),(unsigned long long)get64(ack+16)); goto out; }
 fprintf(stderr,"Transfer complete: %llu bytes acknowledged\n",(unsigned long long)length); report_timing(transmitted,start); rc=0; goto out;
 disconnected:
 if(resume && network_error(errno) && attempt<transfer_retries) {
  progress_clear(); close(sock); sock=-1; ++attempt;
  fprintf(stderr,"Connection interrupted; resume retry %u/%u in 1 second\n",attempt,transfer_retries);
  sleep(1); goto attempt_transfer;
 }
 goto out;
 bad: zent_send_usage(stderr); fprintf(stderr,"Invalid or missing argument\n");
 out: progress_clear(); if(sock>=0) close(sock); if(fd>=0) close(fd); if(timed && rc) { fprintf(stderr,"Transfer failed: %llu/%llu bytes sent (not necessarily received)\n",(unsigned long long)done,(unsigned long long)length); report_timing(transmitted,start); } return rc;
}
