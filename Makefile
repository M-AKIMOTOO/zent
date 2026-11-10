CC ?= gcc
CPPFLAGS += -D_GNU_SOURCE -D_FILE_OFFSET_BITS=64
CFLAGS ?= -O2
CFLAGS += -std=gnu99 -Wall -Wextra
LDLIBS += -lrt
INSTALL ?= install
BINDIR ?= $(HOME)/bin

all: zent

zent: zent.o send.o recv.o common.o resume.o
	$(CC) $(LDFLAGS) -o $@ $^ $(LDLIBS)

send.o recv.o common.o: common.h
send.o recv.o resume.o: resume.h
resume.o: common.h

install: zent
	$(INSTALL) -d "$(BINDIR)"
	$(INSTALL) -m 755 zent "$(BINDIR)/zent"
	$(INSTALL) -m 755 mkzent.py "$(BINDIR)/mkzent.py"
	$(INSTALL) -m 755 zent-run.py "$(BINDIR)/zent-run"
	$(INSTALL) -m 755 zent-bench.py "$(BINDIR)/zent-bench"

test: all
	@test -f tests.py -a -f test_schedule.py || { echo "Local test files are not present; they are not included in this repository." >&2; exit 1; }
	python3 tests.py
	python3 test_schedule.py

clean:
	rm -f zent fastsend fastrecv *.o

.PHONY: all install test clean
