#!/bin/bash -x
# setup the EmerGen-Z on Debian Stretch VM (as setup by FAME)
# for more info see https://github.com/linux-genz/F.E.E./blob/master/docs/VMconfig.md#running-linux-guests-with-ivshmsg-kernel-modules

# this script is usually used by run-host-genz-emul-env.sh

# Copyright 2018 Hewlett Packard Enterprise Development LP
# Author: Bill Hayes <first.last@hpe.com>

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

# install git
sudo apt-get -y update
sudo apt-get -y install git-core

# create github directory in the user's home directory
cd
mkdir github
cd github

# pull the EmerGen-Z source code (via git)
git clone https://github.com/linux-genz/EmerGen-Z.git

# build the EmerGen-Z drivers
cd EmerGen-Z/subsystem
make modules_install
cd ../shim_bridge
make modules_install
# drivers have been installed here: ls -lR /lib/modules/`uname -r`/genz/

# load the EmerGen-Z drivers (and watch the Gen-Z state in ivshmsg_server.py)
sudo modprobe genz verbose=2
sudo modprobe genz_fee_bridge verbose=2
