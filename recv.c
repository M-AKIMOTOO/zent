#include "common.h"
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <netdb.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
void zent_recv_usage(FILE *f) {
 fputs("zent --recv — 1ファイルを受信して保存\n"
 "\n"
 "使い方:\n"
 "  zent --recv (--host HOST | --corr NUM | --bind IP) --port PORT --output FILE\n"
 "\n"
 "必須:\n"
 "  --host HOST           受信側自身のIPv4アドレスまたはホスト名\n"
 "  --corr NUM            受信側を番号で指定（1,2,3,5,6,8,44,101）\n"
 "  --bind IP             従来の待ち受けIPv4アドレス指定。上の2つと排他\n"
 "  --port PORT           待ち受けTCPポート（9000〜9010）\n"
 "  --output FILE         完成ファイルの保存先。既存ファイルは上書きしない\n"
 "\n"
 "オプション:\n"
 "  --receive-mode MODE   splice（既定）または rw（recv/read + write）\n"
 "                        spliceが使えない場合はrwへ切り替える\n"
 "  --max-bytes N         受け入れる最大バイト数（既定: 9223372036854775807）\n"
 "  --expect-offset N     送信元の開始バイト位置を照合する\n"
 "  --expect-length N     宣言された転送バイト数を照合する\n"
 "  --keep-part           失敗時に一時ファイルを残す（既定: 削除）\n"
 "  --fsync               保存完了後、確定・成功応答の前にファイルを同期する\n"
 "  --progress-sec N      端末上での進捗更新間隔（秒、既定: 1、0で無効）\n"
 "  --no-progress         進捗表示を無効にする\n"
 "  --help, -h            このヘルプを表示\n", f);
}
void zent_recv_examples(FILE *f) {
 fputs(
 "\n"
 "例: 受信側のIPが192.168.10.2の場合、送信側より先に起動する:\n"
 "  ./zent --recv --host 192.168.10.2 --port 9002 --output /data/output.raw\n"
 "\n"
 "例: 期待する範囲を照合し、保存後にファイルを同期する:\n"
 "  ./zent --recv --host 192.168.10.2 --port 9002 --output output.raw \\\n"
 "    --expect-offset 604248 --expect-length 1048576 --fsync\n"
 "\n"
 "--host、--corr、--bind はどれか1つ指定します。解決したIPv4アドレスは\n"
 "受信側自身に設定されている必要があります（確認: ip -4 addr show）。\n"
 "待ち受け中・受信中の同じアドレスとポートは、別の受信処理と共有できません。\n"
 "接続を1回受け付けると、成功・失敗にかかわらず終了します。受信中は\n"
 "保存先と同じディレクトリに一時ファイルを作り、成功時に確定します。\n"
 "--fsync は親ディレクトリを同期しません。バイト数の照合だけでは\n"
 "内容の破損を検出できないため、必要に応じて別途ハッシュを比較してください。\n"
 "端末では進捗を1行で更新し、完了・失敗時には経過時間と平均速度を表示\n"
 "します。受信側の経過時間は接続受け付けからファイル確定・応答までです。\n", f);
}
static int listen_on(const char *addr,const char *port) {
 struct addrinfo hints,*a,*p; int fd=-1,one=1,e,saved=0;
 memset(&hints,0,sizeof(hints)); hints.ai_family=AF_INET; hints.ai_socktype=SOCK_STREAM;
 e=getaddrinfo(addr,port,&hints,&a); if(e) { fprintf(stderr,"bind address %s: %s\n",addr,gai_strerror(e)); return -1; }
 for(p=a;p;p=p->ai_next) {
  fd=socket(p->ai_family,p->ai_socktype,p->ai_protocol);
  if(fd<0) { saved=errno; continue; }
  setsockopt(fd,SOL_SOCKET,SO_REUSEADDR,&one,sizeof(one));
  if(bind(fd,p->ai_addr,p->ai_addrlen)==0 && listen(fd,1)==0) break;
  saved=errno; close(fd); fd=-1;
  if(saved!=EADDRNOTAVAIL) break;
 }
 if(fd<0) {
  if(saved==EADDRINUSE) fprintf(stderr,"Port conflict: %s:%s is already in use; choose another port or stop the other receiver\n",addr,port);
  else fprintf(stderr,"bind/listen %s:%s: %s\n",addr,port,strerror(saved));
 }
 freeaddrinfo(a); return fd;
}
static int unsupported(int e) { return e==EINVAL || e==ENOSYS || e==EOPNOTSUPP; }
static int drain_pipe(int p,int out,size_t count,unsigned char *buf,size_t cap,uint64_t *done) {
 while(count) {
  size_t want=count<cap?count:cap; ssize_t n;
  do { n=read(p,buf,want); } while(n<0 && errno==EINTR);
  if(n<=0) { if(!n) errno=EIO; return -1; }
  if(write_full(out,buf,(size_t)n)<0) return -1;
  count-=(size_t)n; *done+=(uint64_t)n;
 }
 return 0;
}
int zent_recv(int argc,char **argv) {
 enum { BIND=256,HOST,CORR,PORT,OUTPUT,MODE,MAX,EXPECT_LENGTH,EXPECT_OFFSET,KEEP,SYNC,PROGRESS,NO_PROGRESS };
 static const struct option opts[]={ {"bind",1,0,BIND},{"host",1,0,HOST},{"corr",1,0,CORR},{"port",1,0,PORT},{"output",1,0,OUTPUT},{"receive-mode",1,0,MODE},{"max-bytes",1,0,MAX},{"expect-length",1,0,EXPECT_LENGTH},{"expect-offset",1,0,EXPECT_OFFSET},{"keep-part",0,0,KEEP},{"fsync",0,0,SYNC},{"progress-sec",1,0,PROGRESS},{"no-progress",0,0,NO_PROGRESS},{"help",0,0,'h'},{0,0,0,0} };
 const char *addr=NULL,*host=NULL,*corr=NULL,*port=NULL,*output=NULL; char *temp=NULL; uint64_t max=INT64_MAX,x,total=0,done=0; int c,listener=-1,sock=-1,out=-1,pipes[2]={-1,-1},mode=1,keep=0,sync_file=0,rc=1,created=0; unsigned interval=1; unsigned char h[HEADER_SIZE],ack[ACK_SIZE],*buf=NULL; size_t cap=1024*1024,pipe_cap=4096; double start=0,last=0;
 uint64_t expected_length=0,expected_offset=0; int check_length=0,check_offset=0,timed=0;
 while((c=getopt_long(argc,argv,"h",opts,NULL))!=-1) switch(c) {
 case BIND: addr=optarg; break; case HOST: host=optarg; break; case CORR: corr=optarg; break; case PORT: port=optarg; break; case OUTPUT: output=optarg; break;
 case MODE: if(!strcmp(optarg,"splice")) mode=1; else if(!strcmp(optarg,"rw")) mode=0; else goto bad; break;
 case MAX: if(parse_u64(optarg,&max)||!max||max>(uint64_t)INT64_MAX) goto bad; break;
 case EXPECT_LENGTH: if(parse_u64(optarg,&expected_length)||!expected_length||expected_length>(uint64_t)INT64_MAX) goto bad; check_length=1; break;
 case EXPECT_OFFSET: if(parse_u64(optarg,&expected_offset)||expected_offset>(uint64_t)INT64_MAX) goto bad; check_offset=1; break;
 case KEEP: keep=1; break; case SYNC: sync_file=1; break;
 case PROGRESS: if(parse_u64(optarg,&x)||x>86400) goto bad; interval=(unsigned)x; break; case NO_PROGRESS: interval=0; break;
 case 'h': zent_recv_usage(stdout); zent_recv_examples(stdout); return 0; default: goto bad;
 }
 if(optind!=argc||((!!addr)+(!!host)+(!!corr)!=1)||!port||!output||!*output) goto bad;
 if(!valid_port(port)) { fprintf(stderr,"Invalid --port: choose a number from 9000 to 9010\n"); goto out; }
 if(host) addr=host;
 if(corr) { addr=correlator_host(corr); if(!addr) { fprintf(stderr,"Invalid --corr: choose 1, 2, 3, 5, 6, 8, 44 or 101\n"); goto out; } }
 signal(SIGPIPE,SIG_IGN);
 listener=listen_on(addr,port); if(listener<0) goto out;
 fprintf(stderr,"Waiting on %s:%s\n",addr,port);
 do { sock=accept(listener,NULL,NULL); } while(sock<0 && errno==EINTR);
 if(sock<0) { perror("accept"); goto out; }
 start=last=now_sec(); timed=1;
 if(read_full(sock,h,sizeof(h))<0) { perror("read header"); goto fail; }
 total=get64(h+16);
 if(memcmp(h,"FASTRAW1",8)||get32(h+8)!=1||get32(h+12)!=0||!total||total>max||get64(h+24)>(uint64_t)INT64_MAX-total) { fprintf(stderr,"Invalid header or transfer exceeds maximum\n"); goto fail; }
 if((check_length && total!=expected_length)||(check_offset && get64(h+24)!=expected_offset)) { fprintf(stderr,"Transfer does not match expected length or source offset\n"); goto fail; }
 if(strlen(output)>SIZE_MAX-13) { fprintf(stderr,"Output path too long\n"); goto fail; }
 temp=malloc(strlen(output)+13); if(!temp) { perror("malloc"); goto fail; }
 sprintf(temp,"%s.part.XXXXXX",output); out=mkstemp(temp); if(out<0) { perror("mkstemp"); goto fail; } created=1;
 buf=malloc(cap); if(!buf) { perror("malloc"); goto fail; }
 if(mode) {
  if(pipe(pipes)<0) { perror("pipe, switching to rw"); mode=0; }
#ifdef F_GETPIPE_SZ
  else { int n=fcntl(pipes[1],F_GETPIPE_SZ); if(n>0) pipe_cap=(size_t)n; }
#endif
 }
 while(done<total) {
  if(mode) {
   size_t want=(total-done<pipe_cap)?(size_t)(total-done):pipe_cap; ssize_t n; size_t pending;
   do { n=splice(sock,NULL,pipes[1],NULL,want,0); } while(n<0 && errno==EINTR);
   if(n<0 && unsupported(errno)) { progress_clear(); fprintf(stderr,"socket splice unavailable; switching to rw\n"); mode=0; continue; }
   if(n<=0) { if(!n) { progress_clear(); fprintf(stderr,"Unexpected EOF\n"); } else progress_perror("socket splice"); goto fail; }
   pending=(size_t)n;
   while(pending) {
    do { n=splice(pipes[0],NULL,out,NULL,pending,0); } while(n<0 && errno==EINTR);
    if(n<0 && unsupported(errno)) {
     progress_clear(); fprintf(stderr,"file splice unavailable; switching to rw\n");
     if(drain_pipe(pipes[0],out,pending,buf,cap,&done)<0) { perror("drain pipe"); goto fail; }
     mode=0; break;
    }
    if(n<=0) { if(!n) errno=EIO; progress_perror("file splice"); goto fail; }
    pending-=(size_t)n; done+=(uint64_t)n;
   }
  } else {
   size_t want=(total-done<cap)?(size_t)(total-done):cap; ssize_t n;
   do { n=read(sock,buf,want); } while(n<0 && errno==EINTR);
   if(n<=0) { if(!n) { progress_clear(); fprintf(stderr,"Unexpected EOF\n"); } else progress_perror("receive"); goto fail; }
   if(write_full(out,buf,(size_t)n)<0) { progress_perror("write output"); goto fail; }
   done+=(uint64_t)n;
  }
  progress("received",done,total,start,&last,interval);
 }
 progress_clear();
 { unsigned char extra; ssize_t n; do { n=read(sock,&extra,1); } while(n<0 && errno==EINTR); if(n!=0) { if(n<0) perror("read end"); else fprintf(stderr,"Unexpected trailing data\n"); goto fail; } }
 if(sync_file && fsync(out)<0) { perror("fsync"); goto fail; }
 if(close(out)<0) { out=-1; perror("close output"); goto fail; } out=-1;
 if(link(temp,output)<0) { perror("publish output (existing files are not overwritten)"); goto fail; }
 if(unlink(temp)<0) fprintf(stderr,"Warning: cannot remove temporary link %s: %s\n",temp,strerror(errno));
 created=0;
 /* Release before ACK so the next sender cannot connect to this receiver. */
 close(listener); listener=-1;
 memset(ack,0,sizeof(ack)); memcpy(ack,"FSTRACK1",8); put32(ack+8,1); put64(ack+16,done);
 if(write_full(sock,ack,sizeof(ack))<0) { perror("send ACK (file was published)"); goto out; }
 fprintf(stderr,"Transfer complete: %llu bytes saved to %s\n",(unsigned long long)done,output); report_timing(done,start); rc=0; goto out;
 fail:
 progress_clear();
 if(listener>=0) { close(listener); listener=-1; }
 memset(ack,0,sizeof(ack)); memcpy(ack,"FSTRACK1",8); put32(ack+8,1); put32(ack+12,1); put64(ack+16,done);
 if(sock>=0) (void)write_full(sock,ack,sizeof(ack));
 goto out;
 bad: zent_recv_usage(stderr); fprintf(stderr,"Invalid or missing argument\n");
 out:
 progress_clear();
 if(out>=0) close(out);
 if(created) { if(keep) fprintf(stderr,"Partial file retained: %s\n",temp); else if(unlink(temp)<0) perror("unlink partial"); }
 if(pipes[0]>=0) close(pipes[0]);
 if(pipes[1]>=0) close(pipes[1]);
 if(sock>=0) close(sock);
 if(listener>=0) close(listener);
 if(timed && rc) { fprintf(stderr,"Transfer failed: %llu/%llu bytes accounted for (partial writes may be excluded)\n",(unsigned long long)done,(unsigned long long)total); report_timing(done,start); }
 free(buf); free(temp); return rc;
}
