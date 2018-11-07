## FAME Background

The current Gen-Z situation is similar to the challenge faced with The Machine.  Actual hardware was many months away and the specifications were still mildly in flux.  However we wanted to start development on the system software required to manage the resources and that demanded a "suitable" development platform.

The Machine consists of up to 40 nodes of an SoC running independent instances of Linux.  All nodes share a 160 TB fabric-attached memory (FAM) global address space via the Gen-Z precursor fabric.  QEMU/KVM provided the basis for a "suitable" development platform.  A single node can be represented by a single VM.  The QEMU feature Inter-VM Shared Memory (IVSHMEM) presents a file in the host operating system as physical address space in a VM.  If all VMs use the same backing store, you get ["Fabric-Attached Memory Emulation" or FAME](https://github.com/FabricAttachedMemory/Emulation).  That project also makes bootable disk images and configures a libvirt network to run a complete setup.

![alt text][IVSHMEM]

[IVSHMEM]: https://github.com/linux-genz/F.E.E./blob/master/docs/images/IVSHMEM%20block.png "Figure 1"


### QEMU Configuration under FAME

When QEMU is invoked with an IVSHMEM configuration, a new PCI device appears in the VM.  The size/space of the file is represented as physical address space behind BAR2 of that device.  To configure a VM for IVSHMEM/FAME, first allocate the file somewhere (such as /home/rocky/FAME/FAM of 32G), then start QEMU with the added stanza

```
-object memory-backend-file,mem-path=/home/rocky/FAME/FAM,size=32G,id=FAM,share=on -device ivshmem-plain,memdev=FAM
```
or add these lines to the end of a libvirt XML domain declaration for a VM:
```XML
  <qemu:commandline>
    <qemu:arg value='-object'/>
    <qemu:arg value='memory-backend-file,mem-path=/home/rocky/FAME/FAM,size=32G,id=FAM,share=on'/>
    <qemu:arg value='-device'/>
    <qemu:arg value='ivshmem-plain,memdev=FAM'/>
  </qemu:commandline>

```
From such a VM configured with a 32G IVSHMEM file:
```bash
rocky@node02 $ lspci -v
  :
00:09.0 RAM memory: Red Hat, Inc Inter-VM shared memory (rev 01)
Subsystem: Red Hat, Inc QEMU Virtual Machine
Flags: fast devsel
Memory at fc05a000 (32-bit, non-prefetchable) [size=256]
Memory at 800000000 (64-bit, prefetchable) [size=32G]
Kernel modules: virtio_pci
```
The precise base address for the 32G space may vary depending on other VM settings.  All of this is handled by the setup script of the [FAME project](https://github.com/FabricAttachedMemory/Emulation).  Be sure and read the wiki there about [the difference between emulation and simulation](https://github.com/FabricAttachedMemory/Emulation/wiki/Emulation-and-Simulation) and [the full FAME setup](https://github.com/FabricAttachedMemory/Emulation/wiki/Emulation-via-Virtual-Machines).
