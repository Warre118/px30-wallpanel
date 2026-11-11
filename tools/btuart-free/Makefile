obj-m := btuart_free.o
KDIR ?= $(HOME)/px30-build/linux
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- modules
clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
