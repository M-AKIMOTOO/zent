#ifndef FAST_COMMON_H
#define FAST_COMMON_H
#include <stdint.h>
#include <stddef.h>
#include <sys/types.h>
#define HEADER_SIZE 32
#define ACK_SIZE 24
#define MIB (1024ULL * 1024ULL)
#define PORT_MIN 9000
#define PORT_MAX 9010
int parse_u64(const char *, uint64_t *);
int valid_port(const char *);
const char *correlator_host(const char *);
void put32(unsigned char *, uint32_t);
void put64(unsigned char *, uint64_t);
uint32_t get32(const unsigned char *);
uint64_t get64(const unsigned char *);
int read_full(int, void *, size_t);
int write_full(int, const void *, size_t);
double now_sec(void);
void progress_begin(uint64_t, double);
void progress(const char *, uint64_t, uint64_t, double, double *, unsigned);
void progress_clear(void);
void progress_perror(const char *);
void report_timing(uint64_t, double);
#endif
