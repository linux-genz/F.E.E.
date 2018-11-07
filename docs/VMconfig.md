THIS INFO IS STALE AND/OR INACCURATE BUT WILL BE UPDATED IN EARLY NOVEMBER

## Create QEMU VM(s) for Linux guest OS

These is offered as a suggestion, not an officially supported directive.  YMMV
so please create an issue if you have problems or a better answer.
All three methods assume proper installation of QEMU, libvirtd, and other
tools and utilities.

### Method 1: Use your favorite/current method

If you have a preferred method, use it, then alter the invocation of the
virtual machines to invoke IVSHMSG.  Either add the following stanza to
a qemu command line:

```
-chardev socket,id=IVSHMSG,path=/tmp/ivshmsg_socket -device ivshmem-doorbell,chardev=IVSHMSG,vectors=16
```
or add this to a libvirt domain XML file, at the end:
```
  <qemu:commandline>
    <qemu:arg value='-chardev'/>
    <qemu:arg value='socket,id=IVSHMSG,path=/tmp/ivshmsg_socket'/>
    <qemu:arg value='-device'/>
    <qemu:arg value='ivshmem-doorbell,chardev=IVSHMSG,vectors=16'/>
  </qemu:commandline>
```

The security configuration of some distros will not allow QEMU to open a
socket in /tmp.  "path" may need to be changed.

### Method 2: qemu-img and virt-install

Two references were selected and mixed/matched to get a working invocation:

https://raymii.org/s/articles/virt-install_introduction_and_copy_paste_distro_install_commands.html#virt-install
    
https://docs.openstack.org/image-guide/virt-install.html

A different graphics option was chosen to allow headless invocation.  The
virtual console can be opened with "virsh console &lt;VMNAME&gt;".  The
following stanzas were replaced:

    --graphics none --console pty,target_type=serial
    --extra-args 'console=ttyS0,115200n8 serial'

1. qemu-img create -f qcow2 ./TARGET.qcow2 4G
1. virt-install --name &lt;VMNAME&gt; --virt-type kvm --vcpus 1 --ram 1024 \
	--disk path=./TARGET.qcow2 \
	--network network=default \
	--graphics spice --video qxl --channel spicevmc \
	--os-type linux --os-variant auto \
	--location &lt;DISTRO-DEPENDENT&gt;

    Debian: --location
http://ftp.us.debian.org/debian/dists/stable/main/installer-amd64/

    Ubuntu Bionic: --location
http://us.archive.ubuntu.com/ubuntu/dists/bionic/main/installer-amd64/

    CentOS: --location
http://www.gtlib.gatech.edu/pub/centos/7/os/x86_64/

1. Alter the VM invocation as explained in "Method 1"

### Method 3: FAME

If your host OS is a Debian distro (or derivative like Ubuntu) you can use
the [emulated development platform for The Machine](FAME_background.md).
It will do all the necessary things:

* Set up a dedicted virtual network (no need to use default)
* Build and customize multiple VMs
* Use correct startup for IVSHMSG
* Optionally use IVSHMEM as Fabric Attached Memory (FAM)

First export these variables.  Yes, FAME_FAM should be empty because it's
not needed for the fabric emulation of this project.

```
    FAME_DIR=/some/where/useful	# Under $HOME is fine
    FAME_FAM=
    FAME_IVSHMSG=yes
    FAME_HOSTBASE=genz		# Optional, default is "node"
    FAME_USER=genz		# Optional, default is "l4mdc"
```

Then you can run the `emulation_configure.bash` script per the documentation
on that project.  Note that FAME_IVSHMSG is ***not*** documented there.
If you use FAM, it will appear as another virtual PCI device in the
guest OS.  The Librarian can find it from there.

Review the $FAME_DIR/${FAME_HOSTBASE}_env.sh to see the pathname chosen
for the Unix domain socket.

## Running Linux guests with IVSHMSG kernel modules

While a QEMU process makes the network connection to ivshmsg_server.py, it's
the guest OS inside QEMU where the messaging endpoints take place.

1. Start ivshmsg_server.py.  If you used FAME to create your VMs, use the
   --socketpath value that matches FAME's assignment.
1. Start the VM(s).
1. Log in to the VM.  Look for the IVSHMSG pseudo-device with *sudo lspci -v*:
1. In the VM, git clone two repos into one directory (so that it contains
   two directories when finished):
   1. cd && mkdir github && cd github
   1. git clone https://github.com/linux-genz/EmerGen-Z.git
   1. git clone https://github.com/?????/executivecardboard.git
1. cd EmerGen-Z/subsystem
   1. *make modules_install* which should create and install one kernel module, 
      `genz.ko`
1. cd ../shim_bridge
   1. *make modules_install* which should create and install two kernel modules,
      `ivshmsg.ko` and `emergenz_bridge.ko`
1. *sudo modprobe ivshmsg.ko verbose=2*  dmesg output should indicate the driver
   found and attached to the IVSHMSG pseudo-device.
1. *sudo insmod emergenz_bridge.ko verbose=2*  dmesg output should show the
   driver bound to the ivshmsg driver.  There should also be a new device file
   /dev/genz_bridge_xx where xx matches the PCI pseudo-device address in lspci.

## Quick messaging tests

1. In another host window, run *ivshmsg_client.py --socketpath ....*.  Note its
   IVSHMSG ID in the server monitor window, or execute "dump" in the client.
1. On the VM, echo "C:hello there" > /dev/genz_bridge_xx, where "C" is the
   IVHSHMSG client number of the client.

