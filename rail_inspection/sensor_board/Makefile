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

PRU1_SRC = $(PRU_DIR)/encoder_pru1.c
PRU1_OBJ = $(PRU_DIR)/encoder_pru1.obj
PRU1_OUT = $(PRU_DIR)/encoder_pru1.out
PRU1_CMD = $(PRU_DIR)/am335x_pru1.cmd

.PHONY: all pru pru1 clean install install-pru1

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)
	@echo "Built: $(TARGET)"

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

pru: $(PRU_OUT)

pru1: $(PRU1_OUT)

$(PRU_OBJ): $(PRU_SRC)
	$(CLPRU) $(PRU_INC_FLAGS) -v3 -O2 --c99 -c $< \
		--obj_directory=$(PRU_DIR)/

$(PRU_OUT): $(PRU_OBJ) $(PRU_CMD)
	$(CLPRU) -z $(PRU_OBJ) $(PRU_CMD) -o $@ \
		--entry_point=main --warn_sections --diag_warning=225
	@echo "PRU firmware: $(PRU_OUT)"

$(PRU1_OBJ): $(PRU1_SRC)
	$(CLPRU) $(PRU_INC_FLAGS) -v3 -O2 --c99 -c $< \
		--obj_directory=$(PRU_DIR)/

$(PRU1_OUT): $(PRU1_OBJ) $(PRU1_CMD)
	$(CLPRU) -z $(PRU1_OBJ) $(PRU1_CMD) -o $@ \
		--entry_point=main --warn_sections --diag_warning=225
	@echo "PRU1 firmware: $(PRU1_OUT)"

clean:
	rm -f $(OBJS) $(TARGET) $(PRU_OBJ) $(PRU_OUT) $(PRU1_OBJ) $(PRU1_OUT)
	@echo "Cleaned."

# Install PRU0 firmware to /lib/firmware and restart PRU0
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

# Install PRU1 firmware to /lib/firmware and restart PRU1
install-pru1: pru1
	sudo cp $(PRU1_OUT) /lib/firmware/am335x-pru1-fw
	@for d in /sys/class/remoteproc/remoteproc*; do \
		if grep -q "4a338000.pru" $$d/name 2>/dev/null; then \
			echo stop  | sudo tee $$d/state; sleep 1; \
			echo am335x-pru1-fw | sudo tee $$d/firmware; sleep 1; \
			echo start | sudo tee $$d/state; \
			echo "PRU1 started via $$d"; break; \
		fi; \
	done
