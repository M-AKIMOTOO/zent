#include "common.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
int parse_u64(const char *s, uint64_t *v) {
 const char *p; unsigned long long x; char *end;
 if (!s || !*s) return -1;
 for (p=s; *p; ++p) if (*p<'0' || *p>'9') return -1;
 errno=0; x=strtoull(s,&end,10);
 if (errno==ERANGE || *end || (uint64_t)x!=x) return -1;
 *v=(uint64_t)x; return 0;
}
int valid_port(const char *s) {
 uint64_t port;
 return parse_u64(s,&port)==0 && port>=PORT_MIN && port<=PORT_MAX;
}
const char *correlator_host(const char *s) {
 uint64_t number;
 if (parse_u64(s,&number)) return NULL;
 switch (number) {
 case 1: return "corr1_10G";
 case 2: return "corr2_10G";
 case 3: return "corr3_10G";
 case 5: return "corr5_10G";
 case 6: return "corr6_10G";
 case 8: return "corr8_10G";
 case 44: return "Rocky";
 case 101: return "yamaNAS1";
 default: return NULL;
 }
}
void put32(unsigned char *p, uint32_t v) { p[0]=(unsigned char)(v>>24); p[1]=(unsigned char)(v>>16); p[2]=(unsigned char)(v>>8); p[3]=(unsigned char)v; }
void put64(unsigned char *p, uint64_t v) { put32(p,(uint32_t)(v>>32)); put32(p+4,(uint32_t)v); }
uint32_t get32(const unsigned char *p) { return ((uint32_t)p[0]<<24)|((uint32_t)p[1]<<16)|((uint32_t)p[2]<<8)|p[3]; }
uint64_t get64(const unsigned char *p) { return ((uint64_t)get32(p)<<32)|get32(p+4); }
int read_full(int fd, void *buf, size_t len) {
 unsigned char *p=buf; ssize_t n;
 while (len) { n=read(fd,p,len); if (n<0 && errno==EINTR) continue; if (n<=0) { if (!n) errno=ECONNRESET; return -1; } p+=n; len-=(size_t)n; } return 0;
}
int write_full(int fd, const void *buf, size_t len) {
 const unsigned char *p=buf; ssize_t n;
 while (len) { n=write(fd,p,len); if (n<0 && errno==EINTR) continue; if (n<=0) { if (!n) errno=EIO; return -1; } p+=n; len-=(size_t)n; } return 0;
}
double now_sec(void) { struct timespec ts; if (clock_gettime(CLOCK_MONOTONIC,&ts)<0) return 0; return (double)ts.tv_sec+(double)ts.tv_nsec/1e9; }
static int progress_tty=-1;
static int progress_log=0;
static int progress_visible=0;
static uint64_t progress_base=0,progress_previous=0;
static double progress_start=0;
void progress_begin(uint64_t bytes, double start) {
 progress_base=progress_previous=bytes; progress_start=start;
}
void progress(const char *label, uint64_t done, uint64_t total, double start, double *last, unsigned interval) {
 double t, elapsed,rate;
 if (!interval) return;
 if (progress_tty<0) {
  const char *setting=getenv("ZENT_PROGRESS_LOG");
  progress_tty=isatty(STDERR_FILENO);
  progress_log=setting && !strcmp(setting,"1");
 }
 if (!progress_tty && !progress_log) return;
 t=now_sec(); elapsed=t-start;
 if (t-*last>=interval) {
  rate=done>=progress_previous?(double)(done-progress_previous)/(t-*last):0;
  fprintf(stderr,"%s%s %5.1f%% %llu/%llu B | %.1f s | %.1f MiB/s now | %.1f MiB/s avg",progress_tty?"\r\033[K":"",label,total ? 100.0*(double)done/(double)total : 0.0,(unsigned long long)done,(unsigned long long)total,elapsed>0 ? elapsed : 0.0,rate/1048576.0,t>progress_start && done>=progress_base ? (double)(done-progress_base)/1048576.0/(t-progress_start) : 0.0);
  if(rate>0 && total>=done) fprintf(stderr," | ETA %.0f s",(double)(total-done)/rate);
  if(!progress_tty) fputc('\n',stderr);
  fflush(stderr); progress_visible=progress_tty; *last=t; progress_previous=done;
 }
}
void progress_clear(void) {
 if (progress_visible) { fputs("\r\033[K",stderr); fflush(stderr); progress_visible=0; }
}
void progress_perror(const char *message) {
 int saved=errno; progress_clear(); errno=saved; perror(message); errno=saved;
}
void report_timing(uint64_t bytes, double start) {
 double elapsed=now_sec()-start;
 unsigned long long ms;
 if (elapsed<0) elapsed=0;
 ms=(unsigned long long)(elapsed*1000.0);
 fprintf(stderr,"Elapsed: %02llu:%02llu:%02llu.%03llu (%.3f s), average: %.2f MiB/s\n",ms/3600000ULL,(ms/60000ULL)%60ULL,(ms/1000ULL)%60ULL,ms%1000ULL,elapsed,elapsed>0 ? (double)bytes/1048576.0/elapsed : 0.0);
}
