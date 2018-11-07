#!/usr/bin/python3

# This work is licensed under the terms of the GNU GPL, version 2 or
# (at your option) any later version.  See the LICENSE file in the
# top-level directory.

# Rocky Craig <rocky.craig@hpe.com>

# This is modelled on the ivshmem-client program that comes with QEMU.

import argparse
import grp
import os
import sys

from ivshmsg_twisted.twisted_client import FactoryIVSHMSGClient

###########################################################################


def parse_cmdline(cmdline_args):
    '''cmdline_args does NOT lead with the program name.  Single-letter
       arguments reflect the stock QEMU "ivshmem-client".'''
    parser = argparse.ArgumentParser(
        description='IVSHMSG client files',
        epilog='Options reflect those in the QEMU "ivshmem-client".'
    )
    parser.add_argument('-?', action='help')  # -h and --help are built in
    parser.add_argument('--socketpath', '-S', metavar='/path/to/socket',
        help='Absolute path to UNIX domain socket created by the server',
        default='/tmp/ivshmsg_socket'
    )
    parser.add_argument('--verbose', '-v',
        help='Specify multiple times to increase verbosity',
        default=0,
        action='count'
    )
    args = parser.parse_args(cmdline_args)

    # Idiot checking.
    assert os.path.exists(args.socketpath), \
        'No socket %s (have you started ivshmsg_server?)' % args.socketpath

    return args

###########################################################################
# MAIN


def forever(cmdline_args=None):
    if cmdline_args is None:
        cmdline_args = sys.argv[1:]  # When being explicit, strip prog name
    try:
        args = parse_cmdline(cmdline_args)
    except Exception as e:
        raise SystemExit(str(e))

    client = FactoryIVSHMSGClient(args)
    client.run()

###########################################################################


if __name__ == '__main__':
    forever()

