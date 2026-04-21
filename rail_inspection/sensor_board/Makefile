# Makefile -- Rail Inspection Sensor Service
# ==========================================
# Targets:
#   make          -> build sensor_service binary
#   make pru      -> build PRU firmware (requires TI clpru toolchain)
#   make clean    -> remove all build artifacts

CC      = gcc
CFLAGS  = -Wall -Wextra -O2 -pthread -D_GNU_SOURCE -D_DEFAULT_SOURCE \
          -I./src
LDFLAGS = -lpthread -lrt -lm

SRCS    = src/sensor_service.c src/scl3300.c
OBJS    = $(SRCS:.c=.o)
TARGET  = sensor_service

# PRU toolchain (TI Code Generation Tools)
CLPRU   = clpru
PRU_DIR = ../pru
PRU_INC = /usr/share/ti/cgt-pru/include \
          /usr/lib/ti/pru-software-support-package/include \
          /usr/lib/ti/pru-software-support-package/include/am335x
PRU_INC_FLAGS = $(addprefix -I, $(PRU_INC))
PRU_SRC = $(PRU_DIR)/encoder_pru0.c
PRU_OBJ = $(PRU_DIR)/encoder_pru0.obj
PRU_OUT = $(PRU_DIR)/encoder_pru0.out
PRU_CMD = $(PRU_DIR)/am335x_pru0.cmd

.PHONY: all pru clean install

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)
	@echo "Built: $(TARGET)"

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

pru: $(PRU_OUT)

$(PRU_OBJ): $(PRU_SRC)
	$(CLPRU) $(PRU_INC_FLAGS) -v3 -O2 --c99 -c $< \
		--obj_directory=$(PRU_DIR)/

$(PRU_OUT): $(PRU_OBJ) $(PRU_CMD)
	$(CLPRU) -z $(PRU_OBJ) $(PRU_CMD) -o $@ \
		--entry_point=main --warn_sections --diag_warning=225
	@echo "PRU firmware: $(PRU_OUT)"

clean:
	rm -f $(OBJS) $(TARGET) $(PRU_OBJ) $(PRU_OUT)
	@echo "Cleaned."

# Install PRU firmware to /lib/firmware and restart PRU0
install: pru
	sudo cp $(PRU_OUT) /lib/firmware/am335x-pru0-fw
	@for d in /sys/class/remoteproc/remoteproc*; do \
		if grep -q "4a334000.pru" $$d/name 2>/dev/null; then \
			echo stop  | sudo tee $$d/state; sleep 1; \
			echo am335x-pru0-fw | sudo tee $$d/firmware; sleep 1; \
			echo start | sudo tee $$d/state; \
			echo "PRU0 started via $$d"; break; \
		fi; \
	done
