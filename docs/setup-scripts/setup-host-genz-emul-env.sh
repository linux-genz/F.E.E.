#!/bin/bash -x
# setup Gen-Z Fabric Emulation Environment (F.E.E.) on Ubuntu 18.04.1
# your mileage will vary on other distros

# this script will NOT setup Executive Cardboard right now

# run this script first, then run-host-genz-emul-env.sh (which uses setup-guest-genz-emul-env.sh)

# Copyright 2018 Hewlett Packard Enterprise Development LP

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2  as
# published by the Free Software Foundation.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along
# with this program.  If not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA

# install additional SW (qemu/kvm, git, python libraries) on top of Ubuntu 18.04.1 needed for Gen-Z F.E.E. & FAME
sudo apt-get -y update
sudo apt-get -y upgrade
sudo apt-get -y dist-upgrade
sudo apt-get -y install git qemu-kvm libvirt-clients libvirt-daemon-system bridge-utils virt-manager virt-viewer vmdebootstrap python3 python3-daemonize python3-attr python3-twisted python3-klein

# enable this user to use virsh/libvirt (this seems to need a reboot to activate)
sudo usermod -a -G libvirt-qemu $USER

# create the FAME and github directories in the user's home directory
cd
mkdir {FAME,github}

# get the Gen-Z F.E.E. & FAME tools on the host system
cd github
git clone https://github.com/FabricAttachedMemory/Emulation.git
git clone https://github.com/linux-genz/F.E.E..git

# build the VM's using FAME that will support Gen-Z F.E.E. and run EmerGen-Z
# For the full set of options see https://github.com/linux-genz/F.E.E./blob/master/docs/VMconfig.md#method-3-fame
cd Emulation

# for more into see https://github.com/linux-genz/F.E.E./blob/master/docs/VMconfig.md#method-3-fame
export FAME_DIR=$HOME/FAME
export FAME_FAM=
export FAME_IVSHMSG=yes

# emulation_configure.bash frequently asks questions default to 'yes'
yes yes | ./emulation_configure.bash 2

# setup done, now logout to fully join the libvirt-qemu group, then run run-host-genz-emul-env.sh
echo "setup done, now logout to fully join the libvirt-qemu group, then run $HOME/github/F.E.E./docs/setup-scripts/run-host-genz-emul-env.sh"

