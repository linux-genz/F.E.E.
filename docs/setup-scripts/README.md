These steps were created for HPE Discover November 2018 in Madrid for the 
"Hack Shack" hands-on development presentation.  They are written assuming your
laptop is running a recent version of Windows.  The instructions should also work
on a MacOS laptop.

1. Reboot your Windows system and Enable VT & VTd in BIOS (so that Virtualbox can use VT technology in VM)
1. [Download VirtualBox]( https://download.virtualbox.org/virtualbox/5.2.22/VirtualBox-5.2.22-126460-Win.exe)
1. Download a Debian-based install ISO:
   1. [Ubuntu 18.04.1 desktop](http://releases.ubuntu.com/18.04.1/ubuntu-18.04.1-desktop-amd64.iso)
   1. [Debian Stretch 9.x with "amd64" via http option](https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-9.3.0-amd64-netinst.iso)
1. Install Virtualbox 5.2.22 for Windows using the VirtualBox-5.2.22-126460-Win.exe
1. Use the Linux ISO to install a virtualbox VM.  Create a disk image of at least 25GB so that there is enough space to create the 2 VMs
1. Boot the Linux image and login.  THis is your QEMU host for the F.E.E. VMs.
1. Start up a BASH (terminal) and then pull the F.E.E. setup scripts:
   1. wget  https://raw.githubusercontent.com/linux-genz/F.E.E./master/docs/setup-scripts/setup-host-genz-emul-env.sh
   1. bash -c setup-host-genz-emul-env.sh
1. logout of the QEMU host and log in to enable participation in new groups:
   1. run "groups" and verify that you are in these groups: libvirt and libvirt-qemu
1. Now pull and run the next setup script from this directory:
   1. github/F.E.E./docs/setup-scripts/run-host-genz-emul-env.sh

