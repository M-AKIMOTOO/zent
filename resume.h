#ifndef ZENT_RESUME_H
#define ZENT_RESUME_H
#include "common.h"
#include <sys/stat.h>

/* Version 2 adds source identity and a receiver READY before any payload. */
#define ID_INFO_SIZE 64
#define ID_NAMES_MAX 8192
#define ID_MAX (HEADER_SIZE + ID_INFO_SIZE + ID_NAMES_MAX)
struct transfer_id { unsigned char bytes[ID_MAX]; size_t size; };
struct resume_state {
 char *part, *meta, *lock;
 int lock_fd;
 uint64_t checkpoint;
 struct transfer_id id;
};
int source_identity(int, const char *, uint64_t, uint64_t, struct transfer_id *);
int receive_identity(int, const unsigned char *, struct transfer_id *);
int resume_open(struct resume_state *, const char *, const struct transfer_id *, int *, uint64_t *, int *);
int resume_checkpoint(struct resume_state *, int, uint64_t);
void resume_release(struct resume_state *);
int socket_timeout(int, unsigned);
int network_error(int);
int send_reply(int, const char *, unsigned, unsigned, uint64_t);
#endif
