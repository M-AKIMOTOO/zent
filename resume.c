#include "resume.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

int source_identity(int fd, const char *input, uint64_t offset, uint64_t length, struct transfer_id *id) {
 struct stat st;
 char host[4096], *path;
 size_t hlen, plen;
 unsigned char *p=id->bytes;
 if(fstat(fd,&st)<0 || gethostname(host,sizeof(host))<0) return -1;
 if(!memchr(host,0,sizeof(host))) { errno=ENAMETOOLONG; return -1; }
 path=realpath(input,NULL); if(!path) return -1;
 hlen=strlen(host)+1; plen=strlen(path)+1;
 if(hlen+plen>ID_NAMES_MAX) { free(path); errno=ENAMETOOLONG; return -1; }
 memset(p,0,HEADER_SIZE+ID_INFO_SIZE);
 memcpy(p,"FASTRAW1",8); put32(p+8,2); put32(p+12,1);
 put64(p+16,length); put64(p+24,offset);
 p+=HEADER_SIZE;
 put64(p,(uint64_t)st.st_dev); put64(p+8,(uint64_t)st.st_ino);
 put64(p+16,(uint64_t)st.st_size);
 put64(p+24,(uint64_t)st.st_mtim.tv_sec); put64(p+32,(uint64_t)st.st_mtim.tv_nsec);
 put64(p+40,(uint64_t)st.st_ctim.tv_sec); put64(p+48,(uint64_t)st.st_ctim.tv_nsec);
 put32(p+56,(uint32_t)hlen); put32(p+60,(uint32_t)plen);
 memcpy(p+ID_INFO_SIZE,host,hlen); memcpy(p+ID_INFO_SIZE+hlen,path,plen);
 free(path); id->size=HEADER_SIZE+ID_INFO_SIZE+hlen+plen;
 return 0;
}

int receive_identity(int sock, const unsigned char *header, struct transfer_id *id) {
 uint32_t hlen,plen;
 unsigned char *p=id->bytes+HEADER_SIZE;
 memcpy(id->bytes,header,HEADER_SIZE);
 if(read_full(sock,p,ID_INFO_SIZE)<0) return -1;
 hlen=get32(p+56); plen=get32(p+60);
 if(!hlen || !plen || hlen>ID_NAMES_MAX || plen>ID_NAMES_MAX-hlen ||
    get64(p+32)>=1000000000 || get64(p+48)>=1000000000 ||
    get64(p+16)>INT64_MAX || get64(header+24)>get64(p+16) ||
    get64(header+16)>get64(p+16)-get64(header+24)) { errno=EPROTO; return -1; }
 if(read_full(sock,p+ID_INFO_SIZE,(size_t)hlen+plen)<0) return -1;
 p+=ID_INFO_SIZE;
 if(p[hlen-1] || p[hlen+plen-1] || memchr(p,0,hlen-1) || memchr(p+hlen,0,plen-1)) { errno=EPROTO; return -1; }
 id->size=HEADER_SIZE+ID_INFO_SIZE+(size_t)hlen+plen;
 return 0;
}

static char *suffix(const char *output, const char *ending) {
 size_t a=strlen(output), b=strlen(ending);
 char *p;
 if(a>SIZE_MAX-b-1) { errno=ENAMETOOLONG; return NULL; }
 p=malloc(a+b+1); if(p) { memcpy(p,output,a); memcpy(p+a,ending,b+1); }
 return p;
}

/* State is replaced atomically only after the corresponding data is synced.
 * The separate lock inode is never replaced/unlinked, including after success.
 * This also works across receivers on different hosts sharing an NFS export. */
int resume_checkpoint(struct resume_state *s, int fd, uint64_t done) {
 unsigned char record[64];
 struct stat st;
 char *tmp=NULL;
 int meta_fd=-1,rc=-1,saved;
 if(fsync(fd)<0 || fstat(fd,&st)<0) return -1;
 if(st.st_size<0 || (uint64_t)st.st_size!=done) { errno=EIO; return -1; }
 memset(record,0,sizeof(record)); memcpy(record,"ZENTRS02",8);
 put32(record+8,2); put32(record+12,(uint32_t)s->id.size); put64(record+16,done);
 put64(record+24,(uint64_t)st.st_dev); put64(record+32,(uint64_t)st.st_ino);
 put64(record+40,done); put64(record+48,(uint64_t)st.st_mtim.tv_sec);
 put64(record+56,(uint64_t)st.st_mtim.tv_nsec);
 tmp=suffix(s->meta,".tmp.XXXXXX"); if(!tmp) return -1;
 meta_fd=mkstemp(tmp); if(meta_fd<0) goto out;
 if(write_full(meta_fd,record,sizeof(record))<0 || write_full(meta_fd,s->id.bytes,s->id.size)<0 || fsync(meta_fd)<0) goto out;
 if(close(meta_fd)<0) { meta_fd=-1; goto out; } meta_fd=-1;
 if(rename(tmp,s->meta)<0) goto out;
 s->checkpoint=done; rc=0;
 out:
 saved=errno;
 if(meta_fd>=0) close(meta_fd);
 if(rc) unlink(tmp);
 free(tmp); errno=saved; return rc;
}

int resume_open(struct resume_state *s, const char *output, const struct transfer_id *id, int *out, uint64_t *done, int *complete) {
 unsigned char record[64],stored[ID_MAX];
 struct stat st,final_st;
 struct flock lock;
 uint64_t checkpoint=0,total=get64(id->bytes+16);
 int meta_fd=-1,fd=-1,have_state=0,have_output=0,saved,rc=-1;
 *out=-1; *complete=0; *done=0;
 s->part=suffix(output,".zent.part"); s->meta=suffix(output,".zent.state"); s->lock=suffix(output,".zent.lock");
 s->id=*id;
 if(!s->part || !s->meta || !s->lock) goto out;
 s->lock_fd=open(s->lock,O_CREAT|O_RDWR|O_NOFOLLOW|O_NONBLOCK,0600);
 if(s->lock_fd<0) goto out;
 if(fstat(s->lock_fd,&st)<0) goto out;
 if(!S_ISREG(st.st_mode)) { errno=EINVAL; goto out; }
 memset(&lock,0,sizeof(lock)); lock.l_type=F_WRLCK; lock.l_whence=SEEK_SET;
 if(fcntl(s->lock_fd,F_SETLK,&lock)<0) { fprintf(stderr,"Resume state is locked by another receiver\n"); goto out; }
 meta_fd=open(s->meta,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);
 if(meta_fd>=0) {
  have_state=1;
  if(fstat(meta_fd,&st)<0) goto out;
  if(!S_ISREG(st.st_mode) || st.st_size!=(off_t)(sizeof(record)+id->size)) { errno=EINVAL; goto mismatch; }
  if(read_full(meta_fd,record,sizeof(record))<0 || read_full(meta_fd,stored,id->size)<0) goto out;
  if(memcmp(record,"ZENTRS02",8) || get32(record+8)!=2 || get32(record+12)!=id->size || memcmp(stored,id->bytes,id->size)) { errno=EINVAL; goto mismatch; }
  checkpoint=get64(record+16);
  if(checkpoint>total || get64(record+40)!=checkpoint || get64(record+56)>=1000000000) { errno=EINVAL; goto mismatch; }
 } else if(errno!=ENOENT) goto out;
 if(lstat(output,&final_st)==0) have_output=1;
 else if(errno!=ENOENT) goto out;
 if(have_output) {
  if(!have_state || !S_ISREG(final_st.st_mode) || checkpoint!=total || final_st.st_size!=(off_t)total ||
     (uint64_t)final_st.st_dev!=get64(record+24) || (uint64_t)final_st.st_ino!=get64(record+32) ||
     (uint64_t)final_st.st_mtim.tv_sec!=get64(record+48) || (uint64_t)final_st.st_mtim.tv_nsec!=get64(record+56)) { errno=EEXIST; goto mismatch; }
  *done=total; *complete=1; s->checkpoint=total; rc=0; goto out;
 }
 if(have_state) {
  fd=open(s->part,O_RDWR|O_NOFOLLOW|O_NONBLOCK); if(fd<0) goto out;
  if(fstat(fd,&st)<0) goto out;
  if(!S_ISREG(st.st_mode) || st.st_size<0 || (uint64_t)st.st_dev!=get64(record+24) ||
     (uint64_t)st.st_ino!=get64(record+32) || (uint64_t)st.st_size<checkpoint || (uint64_t)st.st_size>total) { errno=EINVAL; goto mismatch; }
  /* Discard only the unconfirmed tail of our own partial file. */
  if(ftruncate(fd,(off_t)checkpoint)<0 || lseek(fd,(off_t)checkpoint,SEEK_SET)<0) goto out;
 } else {
  fd=open(s->part,O_CREAT|O_EXCL|O_RDWR|O_NOFOLLOW,0600); if(fd<0) goto out;
  if(resume_checkpoint(s,fd,0)<0) { saved=errno; unlink(s->part); errno=saved; goto out; }
 }
 s->checkpoint=checkpoint; *done=checkpoint; *out=fd; fd=-1; rc=0;
 goto out;
 mismatch:
 fprintf(stderr,"Resume refused: source/range or saved file does not match; existing files were retained\n");
 out:
 saved=errno;
 if(fd>=0) close(fd);
 if(meta_fd>=0) close(meta_fd);
 errno=saved; return rc;
}

void resume_release(struct resume_state *s) {
 if(s->lock_fd>=0) close(s->lock_fd);
 free(s->part); free(s->meta); free(s->lock);
 memset(s,0,sizeof(*s)); s->lock_fd=-1;
}

int socket_timeout(int fd, unsigned seconds) {
 struct timeval tv;
 tv.tv_sec=seconds; tv.tv_usec=0;
 return setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv))<0 ||
        setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv))<0 ? -1 : 0;
}
int network_error(int e) {
 return e==ECONNRESET || e==ECONNREFUSED || e==EPIPE || e==ETIMEDOUT || e==ECONNABORTED ||
        e==ENETDOWN || e==ENETUNREACH || e==EHOSTUNREACH || e==EAGAIN || e==EWOULDBLOCK;
}
int send_reply(int sock, const char *magic, unsigned version, unsigned status, uint64_t bytes) {
 unsigned char reply[ACK_SIZE];
 memcpy(reply,magic,8); put32(reply+8,version); put32(reply+12,status); put64(reply+16,bytes);
 return write_full(sock,reply,sizeof(reply));
}
