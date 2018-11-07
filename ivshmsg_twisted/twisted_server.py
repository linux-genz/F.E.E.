#!/usr/bin/python3

# This work is licensed under the terms of the GNU GPL, version 2 or
# (at your option) any later version.  See the LICENSE file in the
# top-level directory.

# Rocky Craig <rocky.craig@hpe.com>

import argparse
import attr
import functools
import grp
import mmap
import os
import random
import struct
import sys
import time

from collections import OrderedDict
from pprint import pprint

# While deprecated, it has the best examples and is the only thing I
# could get working.  twisted.logger.Logger() is the new way.
from twisted.python import log as TPlog
from twisted.python.logfile import DailyLogFile

from twisted.internet import error as TIError
from twisted.internet import reactor as TIreactor

from twisted.internet.endpoints import UNIXServerEndpoint

from twisted.internet.protocol import ServerFactory as TIPServerFactory
from twisted.internet.protocol import Protocol as TIPProtocol

try:
    from commander import Commander
    from ivshmsg_mailbox import IVSHMSG_MailBox as MB
    from famez_requests import handle_request, send_payload, ResponseObject
    from ivshmsg_eventfd import ivshmsg_event_notifier_list, EventfdReader
    from ivshmsg_sendrecv import ivshmsg_send_one_msg
    from twisted_restapi import MailBoxReSTAPI
except ImportError as e:
    from .commander import Commander
    from .ivshmsg_mailbox import IVSHMSG_MailBox as MB
    from .famez_requests import handle_request, send_payload, ResponseObject
    from .ivshmsg_eventfd import ivshmsg_event_notifier_list, EventfdReader
    from .ivshmsg_sendrecv import ivshmsg_send_one_msg
    from .twisted_restapi import MailBoxReSTAPI

# Don't use peer ID 0, certain docs imply it's reserved.  Use its mailslot
# as global data storage, primarily the server command-line arguments.

IVSHMSG_LOWEST_ID = 1

PRINT = functools.partial(print, file=sys.stderr)
PPRINT = functools.partial(pprint, stream=sys.stderr)

###########################################################################
# Broken; need to get smarter to find actual module loggers.
# See the section below with "/dev/null" with the bandaid fix.

import logging

def shutdown_http_logging():
    for module in ('urllib3', 'requests', 'asdfjkl'):
        logger = logging.getLogger(module)
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.CRITICAL)
        logger.disabled = True
        logger.propagate = False

###########################################################################
# See qemu/docs/specs/ivshmem-spec.txt::Client-Server protocol and
# qemu/contrib/ivshmem-server.c::ivshmem_server_handle_new_conn() calling
# qemu/contrib/ivshmem-server.c::ivshmem_server_send_initial_info(), then
# qemu/contrib/ivshmem-client.c::ivshmem_client_connect()


class ProtocolIVSHMSGServer(TIPProtocol):

    SERVER_IVSHMEM_PROTOCOL_VERSION = 0

    SI = None           # Server Instance, contrived to be "me" not a peer

    def __init__(self, factory, args=None):
        '''"self" is a new client connection, not "me" the server.  As such
            it is a proxy object for the other end of each switch "port".
            args is NOT passed on instantiation via connection.'''
        assert isinstance(factory, TIPServerFactory), 'arg0 not my Factory'
        shutdown_http_logging()
        self.isPFM = False

        # Am I one of many peer proxies?
        if self.SI is not None and args is None:
            self.create_new_peer_id()
            # Python client will quickly fix this.  QEMU VM will eventually
            # modprobe, etc.
            self.peerattrs = {
                'CID0': '0',
                'SID0': '0',
                'cclass': 'Driverless QEMU'
            }
            MB.slots[self.id].cclass = self.peerattrs['cclass']
            return

        # This instance is voodoo from the first manual kick.  Originally
        # the server had "other" tracking but now it's a "reserved instance"
        # which is used as the callback for events (ie, incoming mailslot
        # doorbell for each active peer proxy).

        cls = self.__class__
        cls.SI = self                       # Reserve it and flesh it out.
        cls.verbose = args.verbose

        self.id = args.server_id
        assert self.id == MB.server_id, 'Server ID mismatch'
        self.quitting = False
        self.cclass = MB.cclass(self.id)
        self.logmsg = args.logmsg
        self.logerr = args.logerr
        self.stdtrace = sys.stderr
        self.smart = args.smart
        self.clients = OrderedDict()        # Order probably not necessary
        self.recycled = {} if args.recycle else None

        # For the ResponseObject/request().
        if self.smart:
            self.default_SID = 27
            self.SID0 = self.default_SID
            self.CID0 = self.id * 100
            self.isPFM = True
        else:
            self.default_SID = 0
            self.SID0 = 0
            self.CID0 = 0

        # Non-standard addition to IVSHMEM server role: this server can be
        # interrupted and messaged to particpate in client activity.
        # This variable will get looped even if it's empty (silent mode).
        # Usually create eventfds for receiving messages in IVSHMSG and
        # set up a callback.  This arming is not a race condition as any
        # peer for which this is destined has not yet been "listened/heard".

        self.EN_list = []
        if not args.silent:
            self.EN_list = ivshmsg_event_notifier_list(MB.nEvents)
            # The actual client doing the sending needs to be fished out
            # via its "num" vector.
            for i, EN in enumerate(self.EN_list):
                EN.num = i
                tmp = EventfdReader(EN, self.ServerCallback, cls.SI)
                if i:   # Skip mailslot 0, the globals "slot"
                    tmp.start()

    @property
    def promptname(self):
        '''For Commander prompt'''
        return 'Z-switch' if self.SI.smart else 'Z-server'

    def logPrefix(self):    # This override works after instantiation
        return 'ProtoIVSHMSG'

    def dataReceived(self, data):
        ''' TNSH :-) '''
        self.SI.logmsg('dataReceived, quite unexpectedly')
        raise NotImplementedError(self)

    # If errors occur early enough, send a bad revision to the client so it
    # terminates the connection.  Remember, "self" is a proxy for a peer.
    def connectionMade(self):
        recycled = self.SI.recycled                         # Does it exist?
        if recycled:
            recycled = self.SI.recycled.get(self.id, None)  # Am I there?
        if recycled:
            del self.SI.recycled[recycled.id]
        msg = 'new socket %d == peer id %d %s' % (
              self.transport.fileno(), self.id,
              'recycled' if recycled else ''
        )
        self.SI.logmsg(msg)
        if self.id == -1:           # set from __init__
            self.SI.logmsg('Max clients reached')
            self.send_initial_info(False)   # client complains but with grace
            return

        # The original original code was written around this variable name.
        # Keep that convention for easier comparison.
        server_peer_list = list(self.SI.clients.values())

        # Server line 175: create specified number of eventfds.  These are
        # shared with all other clients who use them to signal each other.
        # Recycling keeps QEMU sessions from dying when other clients drop,
        # a perk not found in original code.
        if recycled:
            self.EN_list = recycled.EN_list
        else:
            try:
                self.EN_list = ivshmsg_event_notifier_list(MB.nEvents)
            except Exception as e:
                self.SI.logmsg('Event notifiers failed: %s' % str(e))
                self.send_initial_info(False)
                return

        # Server line 183: send version, peer id, shm fd
        if self.verbose:
            PRINT('Sending initial info to new peer...')
        if not self.send_initial_info():
            self.SI.logmsg('Send initial info failed')
            return

        # Server line 189: advertise the new peer to others.  Note that
        # this new peer has not yet been added to the list; this loop is
        # NOT traversed for the first peer to connect.
        if not recycled:
            if self.verbose:
                PRINT('NOT recycled: advertising other peers...')
            for other_peer in server_peer_list:
                for peer_EN in self.EN_list:
                    ivshmsg_send_one_msg(
                        other_peer.transport.socket,
                        self.id,
                        peer_EN.wfd)

        # Server line 197: advertise the other peers to the new one.
        # Remember "this" new peer proxy has not been added to the list yet.
        if self.verbose:
            PRINT('Advertising other peers to the new peer...')
        for other_peer in server_peer_list:
            for other_peer_EN in other_peer.EN_list:
                ivshmsg_send_one_msg(
                    self.transport.socket,
                    other_peer.id,
                    other_peer_EN.wfd)

        # Non-standard voodoo extension to previous advertisment: advertise
        # this server to the new peer.  To QEMU it just looks like one more
        # grouping in the previous batch.  Exists only in non-silent mode.
        if self.verbose:
            PRINT('Advertising this server to the new peer...')
        for server_EN in self.SI.EN_list:
            ivshmsg_send_one_msg(
                self.transport.socket,
                self.SI.id,
                server_EN.wfd)

        # Server line 205: advertise the new peer to itself, ie, send the
        # eventfds it needs for receiving messages.  This final batch
        # where the embedded self.id matches the initial_info id is the
        # sentinel that communications are finished.
        if self.verbose:
            PRINT('Advertising the new peer to itself...')
        for peer_EN in self.EN_list:
            ivshmsg_send_one_msg(
                self.transport.socket,
                self.id,
                peer_EN.get_fd())   # Must be a good story here...

        # And now that it's finished:
        self.SI.clients[self.id] = self

        # QEMU did the connect but its VM is probably not yet running well
        # enough to respond.  Since there's no (easy) way to tell, this is
        # a blind shot...
        self.printswitch(self.SI.clients)   # default settling time
        if not self.SI.isPFM:
            send_payload('Link CTL Peer-Attribute',
                         self.SI.id,
                         self.EN_list[self.id])

    def connectionLost(self, reason):
        '''Tell the other peers that this one has died.'''
        dirty = reason.check(TIError.ConnectionDone) is None
        status = 'Dirty' if dirty else 'Clean'
        verb = 'server shutdown' if self.SI.quitting else 'disconnect'
        self.SI.logmsg('%s %s of peer id %d' % (status, verb, self.id))
        # For QEMU crashes and shutdowns (not the OS guest but QEMU itself).
        MB.clear_mailslot(self.id)

        if self.id in self.SI.clients:     # Only if everything was completed
            del self.SI.clients[self.id]
        if self.SI.recycled and not self.SI.quitting:
            self.SI.recycled[self.id] = self
        else:
            try:
                for other_peer in self.SI.clients.values():
                    ivshmsg_send_one_msg(other_peer.transport.socket, self.id)
                for EN in self.EN_list:
                    EN.cleanup()
            except Exception as e:
                self.SI.logmsg('Closing peer transports failed: %s' % str(e))
        self.printswitch(self.SI.clients)
        if self.SI.quitting and not self.SI.clients:    # last one exited
            self.SI.logmsg('Final client disconnected after "quit"')
            TIreactor.stop()                            # turn out the lights

    def create_new_peer_id(self):
        '''Determine the lowest unused client ID and set self.id.'''

        self.SID0 = 0   # When queried, the answer is in the context...
        self.CID0 = 0   # ...of the server/switch, NOT the proxy item.
        if len(self.SI.clients) >= MB.nClients:
            self.id = -1    # sentinel
            return  # Until a Link RFC is executed

        # Generate ID sets used by each.  The range includes the highest
        # client ID.  Two modes: dumb == monotonic from 1; smart == random.
        all_ids = frozenset((range(IVSHMSG_LOWEST_ID, self.SI.id)))
        active_ids = frozenset(self.SI.clients.keys())
        available_ids = all_ids - active_ids
        if self.SI.smart:
            self.id = random.choice(tuple(available_ids))
        else:
            if not self.SI.clients:   # empty
                self.id = 1
            else:
                self.id = (sorted(available_ids))[0]

        if self.SI.smart:
            self.SID0 = self.SI.default_SID
            self.CID0 = self.id * 100

    def send_initial_info(self, ok=True):
        thesocket = self.transport.socket   # self is a proxy for the peer.
        try:
            # 1. Protocol version without fd.
            if not ok:  # Violate the version check and bomb the client.
                PRINT('Early termination')
                ivshmsg_send_one_msg(thesocket, -1)
                self.transport.loseConnection()
                self.id = -1
                return
            if not ivshmsg_send_one_msg(thesocket,
                self.SERVER_IVSHMEM_PROTOCOL_VERSION):
                PRINT('This is screwed')
                return False

            # 2. The client's (new) id, without an fd.
            ivshmsg_send_one_msg(thesocket, self.id)

            # 3. -1 for data with the fd of the IVSHMEM file.  Using this
            # protocol a valid fd is required.
            ivshmsg_send_one_msg(thesocket, -1, MB.fd)
            return True
        except Exception as e:
            PRINT(str(e))
        return False

    # The server is the receiver, and the cbdata is the server SI class
    # attribute shared by all requester proxy objects.  The client instance
    # which which made the request (and provides the target for the response)
    # must be indirectly looked up.
    @staticmethod
    def ServerCallback(vectorobj):
        requester_id = vectorobj.num
        requester_name = MB.nodename(requester_id)
        request = MB.retrieve(requester_id)
        SI = vectorobj.cbdata

        # Recover the appropriate requester proxy object which can die between
        # its interrupt and this callback.
        try:
            requester_proxy = SI.clients[requester_id]
            assert requester_proxy.SI is SI, 'Say WHAT?'
            assert requester_proxy.id == requester_id, 'WTF MF?'
            requester_proxy.requester_id = requester_id   # Pedantic?
            # For QEMU/VM, this may be the first chance to grab this (if the
            # drivers hadn't come up before).  Just get it fresh each time.
            requester_proxy.nodename = requester_name
            requester_proxy.cclass = MB.cclass(requester_id)
            requester_proxy.peerattrs['cclass'] = requester_proxy.cclass
        except KeyError as e:
            SI.logmsg('Disappeering act by %d' % requester_id)
            return

        # The object passed has two sets of data:
        # 1. Id/target information on where to send the response
        # 2. Peer attributes used in two requests:
        #    readout to send them
        #    overwrite if they're being sent by a PFM
        # I'm "if blah blah" in handle_request() but I should just strip the
        # peer attribute here (the server in this case).  for the server,
        # requester_name and requester_proxy.nodename are the same
        # peer_attributes are from the server, not the proxy.
        # it's different for the client.

        ro = ResponseObject(
            this=SI,                # has "my" (server) CID0, SID0, and cclass
            proxy=requester_proxy,  # for certain server-only admin
            from_id=SI.id,
            to_doorbell=requester_proxy.EN_list[SI.id],
            logmsg=SI.logmsg,
            stdtrace=SI.stdtrace,
            verbose=SI.verbose
        )
        ret = handle_request(request, requester_name, ro)

        # ret is either True, False, or...

        if ret == 'dump':
            # Might be some other stuff, but finally
            ProtocolIVSHMSGServer.printswitch(SI.clients)

    #----------------------------------------------------------------------
    # ASCII art switch:  Left side and right sider are each half of the ports.

    @staticmethod
    def printswitch(clients, delay=0.5):
        if int(delay) < 0 or int(delay) > 2:
            delay = 1.0
        time.sleep(delay)
        lfmt = '%s %s [%s,%s]'
        rfmt = '[%s,%s] %s %s'
        half = (MB.MAILBOX_MAX_SLOTS - 1) // 2
        NSP = 32
        lspaces = ' ' * NSP
        PRINT('\n%s  ____ ____' % lspaces)
        notch = 'U'
        for i in range(1, half + 1):
            left = i
            right = MB.server_id - left     # 74XX TTL
            try:
                ldesc = lspaces
                c = clients[left]
                pa = c.peerattrs
                pa['cclass'] = MB.cclass(left)
                ldesc += lfmt % (pa['cclass'], MB.nodename(left),
                    pa['CID0'], pa['SID0'])
            except KeyError as e:
                pass
            try:
                c = clients[right]
                pa = c.peerattrs
                pa['cclass'] = MB.cclass(right)
                rdesc = rfmt % (pa['CID0'], pa['SID0'],
                    pa['cclass'], MB.nodename(right))
            except KeyError as e:
                rdesc = ''
            PRINT('%-s -|%1d  %c %2d|- %s' % (
                ldesc[-NSP:], left, notch, right, rdesc))
            notch = ' '
        PRINT('%s  =========' % lspaces)

    #----------------------------------------------------------------------
    # Command line parsing, picked up by commander.py.  This instance was
    # from the original "class-setting" call so it has no transport.

    def doCommand(self, cmd, args=None):

        if cmd in ('h', 'help') or '?' in cmd:
            print('h[elp]\n\tThis message')
            print('d[ump]\n\tPrint status of all ports')
            print('q[uit]\n\tShut it all down')
            return True

        if cmd in ('d', 'dump'):
            if self.verbose > 1:
                PRINT('')
                for id, peer in self.SI.clients.items():
                    PRINT('%10s: %s' % (MB.nodename(id), peer.peerattrs))
                    if self.verbose > 2:
                        PPRINT(vars(peer), stream=sys.stdout)
            self.printswitch(self.SI.clients, 0)
            return True

        if cmd in ('q', 'quit'):
            self.quitting = True                # self == SI, remember?
            self.logmsg('Interactive command to "quit"')
            if self.clients:                    # Trigger lostConnection
                for c in self.clients.values():
                    c.transport.loseConnection()    # Final callback exits
            else:
                TIreactor.stop()
            return False

        PRINT('Unrecognized command "%s", try "help"' % cmd)
        return True

###########################################################################
# Normally the Endpoint and listen() call is done explicitly, interwoven
# with passing this constructor.  This approach used here hides all the
# twisted things in this module.


class FactoryIVSHMSGServer(TIPServerFactory):

    _required_arg_defaults = {
        'title':        'IVSHMSG',
        'foreground':   True,       # Only affects logging choice in here
        'logfile':      '/tmp/ivshmsg_log',
        'mailbox':      'ivshmsg_mailbox',  # Will end up in /dev/shm
        'nClients':     2,
        'recycle':      False,      # Try to preserve other QEMUs
        'silent':       False,      # Does participate in eventfds/mailbox
        'socketpath':   '/tmp/ivshmsg_socket',
        'verbose':      0,
    }

    def __init__(self, args=None):
        '''Args must be an object with the following attributes:
           foreground, logfile, mailbox, nClients, silent, socketpath, verbose
           Suitable defaults will be supplied.'''

        # Pass command line args to ProtocolIVSHMSG, then open logging.
        if args is None:
            args = argparse.Namespace()
        for arg, default in self._required_arg_defaults.items():
            setattr(args, arg, getattr(args, arg, default))

        # Mailbox may be sized above the requested number of clients to
        # satisfy QEMU IVSHMEM restrictions.
        args.server_id = args.nClients + 1
        args.nEvents = args.nClients + 2

        # It's a singleton so no reason to keep the instance, however it's
        # the way I wrote the Klein API server so...
        mb = MB(args=args)
        MailBoxReSTAPI(mb)
        shutdown_http_logging()

        if args.foreground:
            if args.verbose > 1:
                TPlog.startLogging(sys.stdout, setStdout=False)
            else:
                TPlog.startLogging(open('/dev/null', 'a'), setStdout=False)
        else:
            PRINT('Logging to %s' % args.logfile)
            TPlog.startLogging(
                DailyLogFile.fromFullPath(args.logfile),
                setStdout=True)     # "Pass-through" explicit print() for debug
        args.logmsg = TPlog.msg
        args.logerr = TPlog.err

        # By Twisted version 18, "mode=" is deprecated and you should just
        # inherit the tacky bit from the parent directory.  wantPID creates
        # <path>.lock as a symlink to "PID".
        E = UNIXServerEndpoint(
            TIreactor,
            args.socketpath,
            mode=0o666,         # Deprecated at Twisted 18
            wantPID=True)
        E.listen(self)
        args.logmsg('%s server @%d ready for %d clients on %s' %
            (args.title, args.server_id, args.nClients, args.socketpath))

        # https://stackoverflow.com/questions/1411281/twisted-listen-to-multiple-ports-for-multiple-processes-with-one-reactor

        # Voodoo kick to a) set up one-time SI and b)setup commander.
        # Docs mislead, have to explicitly pass something to get persistent
        # state across protocol/transport invocations.  As there is only
        # one server object per process instantion, that's not necessary.

        protobj = ProtocolIVSHMSGServer(self, args)     # With "args"
        Commander(protobj)

    def buildProtocol(self, useless_addr):
        # Unfortunately this doesn't work.  Search for /dev/null above.
        shutdown_http_logging()

        protobj = ProtocolIVSHMSGServer(self)           # Without "args"
        return protobj

    def run(self):
        TIreactor.run()                                 # and hangs here

