#!/bin/bash -x
# run the Gen-Z Fabric Emulation Environment (F.E.E.) on Ubuntu 18.04.1
# your mileage will vary on other distros

# run this script after running setup-host-genz-emul-env.sh

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

# setup the virtual machines that were created by the FAME tool in virsh/libvirt/etc
# NOTE: You must belong to group libvirt-qemu (see logout at end of setup-genz-emul-env.sh script)
cd
cd FAME

# source the FAME variables
. node_env.sh

# create a virsh definition of the FAME VM's
./node_virsh.sh define

# add entries in /etc/hosts for nodeXX (if they don't exist)
if ! grep -q node01 /etc/hosts; then
 echo "192.168.42.1	node01" | sudo tee -a /etc/hosts
 echo "192.168.42.2	node02" | sudo tee -a /etc/hosts
fi

# setup the SSH keys (that FAME used in VM's) on this host system (if they are not setup)
if [ ! -f .ssh/config ]; then
 cp $HOME/github/Emulation/templates/id_rsa.nophrase* $HOME/.ssh
 chmod 600 $HOME/.ssh/*rsa*
 cat > $HOME/.ssh/config << EOF
ConnectTimeout 5
StrictHostKeyChecking No
AddKeysToAgent yes

Host node*
	User l4mdc
	IdentityFile ~/.ssh/id_rsa.nophrase
EOF
 rm -f $HOME/.ssh/known_hosts
fi

# start ivshmsg_server.py in another terminal window
# directly stolen from: https://github.com/linux-genz/F.E.E./blob/master/docs/desktop/server.desktop
# suggestion: type 'help' inside the ivshmsg_server.py terminal window
gnome-terminal -e 'bash -c "$HOME/github/F.E.E./ivshmsg_server.py --socket $HOME/FAME/node_socket || sleep 5"'

# let the ivshmsg_server.py get running so that it is listening for network connections
sleep 10

# start ivshmsg_client.py in another terminal window
# directly stolen from: https://github.com/linux-genz/F.E.E./blob/master/docs/desktop/client.desktop
# suggestion: type 'help' inside the ivshmsg_client.py terminal window
gnome-terminal -e 'bash -c "$HOME/github/F.E.E./ivshmsg_client.py --socket $HOME/FAME/node_socket || sleep 5"'

# start the VM's
sudo virsh start node01
sudo virsh start node02

# let the VM's start running before trying to ssh to them
sleep 30

# setup the EmerGen-Z environment in each of VM's
# for more info see https://github.com/linux-genz/F.E.E./blob/master/docs/VMconfig.md#running-linux-guests-with-ivshmsg-kernel-modules
scp $HOME/github/F.E.E./docs/setup-scripts/setup-guest-genz-emul-env.sh node01:.
scp $HOME/github/F.E.E./docs/setup-scripts/setup-guest-genz-emul-env.sh node02:.
ssh node01 ./setup-guest-genz-emul-env.sh
ssh node02 ./setup-guest-genz-emul-env.sh

# setup interactive ssh sessions to the VM's
gnome-terminal -e 'bash -c "ssh node01 || sleep 5"'
gnome-terminal -e 'bash -c "ssh node02 || sleep 5"'

