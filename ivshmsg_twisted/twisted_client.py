#!/usr/bin/python3

# This work is licensed under the terms of the GNU GPL, version 2 or
# (at your option) any later version.  See the LICENSE file in the
# top-level directory.

# Rocky Craig <rocky.craig@hpe.com>

import argparse
import grp
import mmap
import struct
import sys

from collections import OrderedDict

from twisted.internet import stdio
from twisted.internet import error as TIError
from twisted.internet import reactor as TIreactor

from twisted.internet.endpoints import UNIXClientEndpoint

from twisted.internet.interfaces import IFileDescriptorReceiver

from twisted.internet.protocol import ClientFactory as TIPClientFactory
from twisted.internet.protocol import Protocol as TIPProtocol

from zope.interface import implementer

try:
    from commander import Commander
    from ivshmsg_mailbox import IVSHMSG_MailBox as MB
    from famez_requests import handle_request, send_payload, ResponseObject
    from ivshmsg_eventfd import ivshmsg_event_notifier_list, EventfdReader
except ImportError as e:
    from .commander import Commander
    from .ivshmsg_mailbox import IVSHMSG_MailBox as MB
    from .famez_requests import handle_request, send_payload, ResponseObject
    from .ivshmsg_eventfd import ivshmsg_event_notifier_list, EventfdReader

###########################################################################
# See qemu/docs/specs/ivshmem-spec.txt::Client-Server protocol and
# qemu/contrib/ivshmem-server.c::ivshmem_server_handle_new_conn() calling
# qemu/contrib/ivshmem-server.c::ivshmem_server_send_initial_info(), then
# qemu/contrib/ivshmem-client.c::ivshmem_client_connect()

# The UNIX transport in the middle of this, at
# /usr/lib/python3/dist-packages/twisted/internet/unix.py line 174
# will properly glean a file descriptor if present.  There must be
# real data to go with this ancillary data.  Then, if this protocol
# is recognized as implementing IFileDescriptorReceiver, it will FIRST
# call fileDescriptorReceived before dataReceived.  So for the initial
# info exchange, version and my (new) id are put out without an fd,
# then a -1 is put out with the mailbox fd.   What triggers here is
# one fileDescriptorReceived, THEN a dataReceived of thre quad words.
# Then it pingpongs evenly between an fd and a single quadword for
# each grouping.


@implementer(IFileDescriptorReceiver)   # Energizes fileDescriptorReceived
class ProtocolIVSHMSGClient(TIPProtocol):

    CLIENT_IVSHMEM_PROTOCOL_VERSION = 0

    args = None
    id2fd_list = OrderedDict()     # Sent to me for each peer
    id2EN_list = OrderedDict()     # Generated from fd_list

    def __init__(self, cmdlineargs):
        try:                        # twisted causes blindness
            if self.args is None:
                cls = self.__class__
                cls.args = cmdlineargs
                cls.logmsg = print
                cls.logerr = print
                cls.stdtrace = sys.stdout
                cls.verbose = cls.args.verbose

            # The state machine major decisions about the semantics of blocks
            # of data have one predicate.  initial_pass is an extra guard.
            self.id = None       # Until initial info; state machine key
            self._latest_fd = None
            self.initial_pass = True

            # Other stuff
            self.quitting = False
            self.isPFM = False
            self.linkattrs = { 'State': 'up' }
            self.peerattrs = {}
            self.SID0 = 0
            self.CID0 = 0
            self.nodename = None
            self.cclass = None
            self.afterACK = []
        except Exception as e:
            print('__init__() failed: %s' % str(e))
            # ...and any number of attribute references will fail soon

    @property   # For Commander prompt
    def promptname(self):
        return self.nodename

    @staticmethod
    def parse_target(caller_id, instr):
        '''Return a list even for one item for consistency with keywords
           ALL and OTHERS.'''
        try:
            tmp = (int(instr), )
            if 1 <= tmp[0] <= MB.server_id:
                return tmp
        except TypeError as e:
            return None
        except ValueError as e:
            if instr.lower()[-6:] in ('server', 'switch'):
                return (MB.server_id,)
            active_ids = MB.active_ids()
            for id in active_ids:
                if MB.slots[id].nodename == instr:
                    return (id, )
            if instr.lower() == 'all':      # Includes caller_id
                return active_ids
            if instr.lower() == 'others':
                return active_ids.remove(caller_id)
        return None

    def place_and_go(self, dest, msg, src=None, reset_tracker=True):
        '''Yes, reset_tracker defaults to True here.'''
        dest_indices = self.parse_target(self.id, dest)
        if src is None:
            src_indices = (self.id,)
        else:
            src_indices = self.parse_target(self.id, src)
        if self.verbose > 1:
            print('P&G dest %s=%s src %s=%s' %
                      (dest, dest_indices, src, src_indices))
        assert src_indices, 'missing or unknown source(s)'
        assert dest_indices, 'missing or unknown destination(s)'
        for S in src_indices:
            for D in dest_indices:
                if self.verbose > 1:
                    print('P&G(%s, "%s", %s)' % (D, msg, S))
                try:
                    # First get the list for dest, then the src ("from me")
                    # doorbell EN.
                    doorbell = self.id2EN_list[D][S]

                    # This repeat-loads the source mailslot D times per S
                    # but I don't care.
                    send_payload(msg, S, doorbell, reset_tracker=reset_tracker)
                except KeyError as e:
                    print('No such peer id', str(e))
                    continue
                except Exception as e:
                    print('place_and_go(%s, "%s", %s) failed: %s' %
                        (D, msg, S, str(e)))
                    return

    def fileDescriptorReceived(self, latest_fd):
        assert self._latest_fd is None, 'Latest fd has not been consumed'
        self._latest_fd = latest_fd     # See the next property

    @property
    def nodename(self):
        return self._nodename

    @nodename.setter
    def nodename(self, name):
        self._nodename = name
        if name:
            MB.slots[self.id].nodename = name

    @property
    def cclass(self):
        return self._cclass

    @cclass.setter
    def cclass(self, name):
        self._cclass = name
        if name:
            MB.slots[self.id].cclass = name

    @property
    def latest_fd(self):
        '''This is NOT idempotent!'''
        tmp = self._latest_fd
        self._latest_fd = None
        return tmp

    def retrieve_initial_info(self, data):
        # 3 longwords: protocol version w/o FD, my (new) ID w/o FD,
        # and then a -1 with the FD of the IVSHMEM file which is
        # delivered before this.
        assert self.initial_pass, 'Internal state error (1)'
        assert len(data) == 24, 'Initial data needs three quadwords'

        # Enough idiot checks.
        mailbox_fd = self.latest_fd
        version, tmpid, minusone = struct.unpack('qqq', data)
        assert version == self.CLIENT_IVSHMEM_PROTOCOL_VERSION, \
            'Unxpected protocol version %d' % version
        assert minusone == -1, \
            'Expected -1 with mailbox fd, got %d' % minusone

        # Initialize my mailbox slot.  Get other parameters from the
        # globals because the IVSHMSG protocol doesn't allow values
        # beyond the intial three.  The constructor does some work then
        # returns a few attributes pulled out of the globals, but work
        # is only actually done on the first call.  It's a singleton with
        # class variables so there's no reason to keep the instance.
        # SI is the object passed to the event callback so flesh it out.
        MB(fd=mailbox_fd, client_id=tmpid)

        # Wait for initialized mailbox to finally (re)assign the sentinel.
        self.id = tmpid
        self.nodename = 'z%02d' % self.id
        self.cclass = 'Debugger'
        print('This ID = %2d (%s)' % (self.id, self.nodename))

    # Called multiple times so keep state info about previous calls.
    def dataReceived(self, data):
        if self.id is None:
            self.retrieve_initial_info(data)
            return      # But I'll be right back :-)

        # Now into the stream of <peer id><eventfd> pairs.  Unless it's
        # a single <peer id> which is a disconnect notification.
        latest_fd = self.latest_fd
        assert len(data) == 8, 'Expecting a signed long long'
        thisbatch = struct.unpack('q', data)[0]
        if self.verbose > 1:
            print('Just got index %s, fd %s' % (thisbatch, latest_fd))
        assert thisbatch >= 0, 'Latest data is negative number'

        if latest_fd is None:   # "thisbatch" is a disconnect notification
            print('%s (%d) has left the building' %
                (MB.slots[thisbatch].nodename, thisbatch))
            for collection in (self.id2EN_list, self.id2fd_list):
                try:
                    del collection[thisbatch]
                except Exception as e:
                    pass
            return

        # Collect all the fds for this batch, max batch length == nEvents
        # (the dummy slot 0, nClients, and the server).  This batch may one
        # one of many, or one of one for two different reasons:
        # 1. 1 of 1: Another new client has joined the cluster.   This batch
        #    is for that new client.
        # 2. First contact by this client instance: There will be one batch
        #    for each existing peer, terminated by a batch of "my" fds.
        #    That last batch is tagged with "my" self.id AND must come last
        #    (an assumption of QEMU).
        #    A. if using the stock QEMU "ivshmem_server" there will NOT be
        #       a batch for the server itself.  Thus it's possible to receive
        #       only one batch during first_contact, if this is the only peer.
        #    B. if using twisted_server, on first contact you will always get
        #       at least the server batch first, so the minimum batch run
        #       length is two.
        # Keep track of all batches then post-process when they stop coming.

        # Am I starting the final batch of my initial connection?  This
        # paranoia check is on MB and its assignment during _initial_info().
        if thisbatch == self.id and not MB.server_id:
            assert MB.server_id == self.prevbatch, 'Then dont assign it'
        self.prevbatch = thisbatch     # corner case where I am first peer

        # Just save the eventfd now, generate objects later.
        try:
            tmp = len(self.id2fd_list[thisbatch])
            assert tmp <= MB.server_id, 'fd list is too long'
            if tmp == MB.nEvents:   # Beginning of client reconnect
                assert thisbatch != self.id, \
                    'Updating MY eventfds??? off-by-one'
                raise KeyError('Forced update')
            self.id2fd_list[thisbatch].append(latest_fd)    # order matters
        except KeyError as e:
            self.id2fd_list[thisbatch] = [latest_fd, ]      # first one

        if self.verbose > 1:
            print('fd list is now %s' % str(self.id2fd_list.keys()))
            for id, eventfds in self.id2fd_list.items():
                print(id, eventfds)

        # Assumes all vector lists are the same length.
        batchneeds = MB.nEvents - len(self.id2fd_list[thisbatch])
        if batchneeds > 0:
            if self.verbose > 1:
                print('Batch for peer id %d expecting %d more fds...\n' %
                    (thisbatch, batchneeds))
            return

        # Batch is complete, it's either
        # 1. one of many, so more to come in this initial contact
        #    (unless it was me)
        # 2. Just a single batch, ie, a new peer
        if self.initial_pass:
            if thisbatch != self.id:
                if self.verbose > 1:
                    print('%d is not the final batch' % thisbatch)
                return
            print('Active client list now complete')
        else:
            print('New client %d complete' % thisbatch)

        # Generate event notifiers from each (new) fd_list for signalling
        # to other peers.
        for id in self.id2fd_list:          # Triggers message pickup
            if id not in self.id2EN_list:   # already processed?
                self.id2EN_list[id] = ivshmsg_event_notifier_list(
                    self.id2fd_list[id])

        if not self.initial_pass:           # It was just one additional peer
            return

        # Finally arm my incoming events and announce readiness.
        assert thisbatch == self.id, 'Cuz it\'s not paranoid if you catch it'
        for i, N in enumerate(self.id2EN_list[self.id]):
            N.num = i
            tmp = EventfdReader(N, self.ClientCallback, self)
            tmp.start()
        print('Ready player %s' % self.nodename)
        self.place_and_go('server', 'Link CTL Peer-Attribute')
        self.initial_pass = False

    def connectionMade(self):
        if self.verbose:
            print('Connection made on fd', self.transport.fileno())

    def connectionLost(self, reason):
        print(reason.value)
        if reason.check(TIError.ConnectionDone) is None:    # Dirty
            print('Client was probably interrupted or killed.')
        else:
            if self.quitting:
                print('Last interactive command was "quit".')
            else:
                print('The server was probably shut down.')
        MB.clear_mailslot(self.id)  # In particular, nodename
        if TIreactor.running:       # Stopped elsewhere on SIGINT
            TIreactor.stop()

    # The cbdata is precisely the object which can be used for the response.
    # In other words, it's directly "me", with "my" identity data.
    @staticmethod
    def ClientCallback(vectorobj):
        requester_id = vectorobj.num
        requester_name = MB.nodename(requester_id)
        request = MB.retrieve(requester_id)
        requester_obj = vectorobj.cbdata
        # print('Raw Req ID = %d\n%s' % (requester_id, vars(requester_obj)))

        # [dest][src]
        ro = ResponseObject(
            this=requester_obj,         # has CID0, SID0, and cclass
            proxy=None,                 # I don't manage ever (for now)
            from_id=requester_obj.id,
            to_doorbell=requester_obj.id2EN_list[requester_id][requester_obj.id],
            logmsg=requester_obj.logmsg,
            stdtrace=requester_obj.stdtrace,
            verbose=requester_obj.verbose,
        )
        ret = handle_request(request, requester_name, ro)

    #----------------------------------------------------------------------
    # Command line parsing.


    def doCommand(self, cmd, args):
        cmd = cmd.lower()
        if cmd in ('p', 'ping', 's', 'send'):
            if cmd.startswith('p'):
                assert len(args) == 1, 'Missing dest'
                cmd = 'send'
                args.append('ping')    # Message payload
            else:
                assert len(args) >= 1, 'Missing dest'
            dest = args.pop(0)
            msg = ' '.join(args)       # Empty list -> empty string
            self.place_and_go(dest, msg)
            return True

        if cmd in ('sp', 'spoof'):     # Like send but specify a src
            assert len(args) >= 2, 'Missing src and/or dest'
            src = args.pop(0)
            dest = args.pop(0)
            msg = ' '.join(args)   # Empty list -> empty string
            self.place_and_go(dest, msg, src)
            return True

        if cmd in ('d', 'dump'):    # Include the server
            if self.verbose > 1:
                print('Peer list keys (%d max):' % (MB.nClients + 1))
                print('\t%s' % sorted(self.id2EN_list.keys()))

                print('\nActor event fds:')
                for key in sorted(self.id2fd_list.keys()):
                    print('\t%2d %s' % (key, self.id2fd_list[key]))
                print()

            print('Client node/host names:')
            for key in sorted(self.id2fd_list.keys()):
                print('\t%2d %s' % (key, MB.slots[key].nodename))

            print('\nMy CID0:SID0 = %d:%d' % (self.CID0, self.SID0))
            print('Link attributes:\n', self.linkattrs)
            print('Peer attributes:\n', self.peerattrs)

            return True

        if cmd in ('h', 'help') or '?' in cmd:
            print('dest/src can be integer, hostname, or "server"\n')
            print('h[elp]\n\tThis message')
            print('l[ink]\n\tLink commands (CTL and RFC)')
            print('p[ing] dest\n\tShorthand for "send dest ping"')
            print('q[uit]\n\tJust do it')
            print('r[fc]\n\tSend "Link RFC ..." to the server')
            print('s[end] dest [text...]\n\tSend text from this client')
            print('sp[oof] src dest [text...]\n\tLike send but fake the src')
            print('w[ho]\n\tList all peers')
            return True

        if cmd in ('w', 'who'):
            print('\nThis ID = %2d (%s)' % (self.id, self.nodename))
            for id in self.id2fd_list.keys():
                if id == self.id:
                    continue
                print('Peer ID = %2d (%s)' % (id, MB.slots[id].nodename))
            return True

        if cmd in ('l', 'link'):
            assert len(args) >= 1, 'Missing directive'
            msg = 'Link %s' % ' '.join(args)
            self.place_and_go('server', msg)
            return True

        if cmd in ('r', 'rfc'):
            msg = 'Link RFC TTC=27us'
            self.place_and_go('server', msg)
            return True

        if cmd in ('q', 'quit'):
            self.quitting = True
            self.transport.loseConnection()
            return False

        print('Unrecognized command "%s", try "help"' % cmd)
        return True

###########################################################################
# Normally the Endpoint and listen() call is done explicitly,
# interwoven with passing this constructor.  This approach hides
# all the twisted things in this module.


class FactoryIVSHMSGClient(TIPClientFactory):

    _required_arg_defaults = {
        'socketpath':   '/tmp/ivshmsg_socket',
        'verbose':      0,
    }

    def __init__(self, args=None):
        '''Args must be an object with the following attributes:
           socketpath, verbose
           Suitable defaults will be supplied.'''

        # Pass command line args to ProtocolIVSHMSG, then open logging.
        if args is None:
            args = argparse.Namespace()
        for arg, default in self._required_arg_defaults.items():
            setattr(args, arg, getattr(args, arg, default))

        self.args = args

        # checkPID looks for <socketpath>.lock which the server sets up
        # as a symlink to file named <PID>
        E = UNIXClientEndpoint(
            TIreactor,
            args.socketpath,
            timeout=1,
            checkPID=False)
        E.connect(self)

    def buildProtocol(self, addr):
        if self.args.verbose > 1:
            print('buildProtocol', addr.name)
        protobj = ProtocolIVSHMSGClient(self.args)
        Commander(protobj)
        return protobj

    def startedConnecting(self, connector):
        print('Started connecting')

    def clientConnectionFailed(self, connector, reason):
        print('Failed connection:', str(reason))

    def clientConnectionLost(self, connector, reason):
        print('Lost connection:', str(reason))

    def run(self):
        TIreactor.run()

if __name__ == '__main__':
    from pdb import set_trace
    set_trace()
    pass
