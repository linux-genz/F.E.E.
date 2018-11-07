#!/usr/bin/python3

# The IVSHMEM protocol practiced by QEMU demands a memory-mappable file
# descriptor as part of the initial exchange, so give it one.  The mailbox
# is a shared common area split into "slots".   slot 0 is a global read-only
# area populated by the server, then "nClients" worth of slots, and a final
# entry "nClients + 1" is the server mailslot.  This is contiguous because it
# it aligns with how QEMU expects delivery of peer info, including the twisted
# server (which is an extension beyond stock QEMU IVSHMSG protocol).
# Right now a mailslot is 512 bytes: 128 bytes of metadata (currently about
# 96 used), then 384 of message buffer.
# Go for the max slots in the file to hardwire libvirt domain XML file size.
# VM guest kernel modules read global data to understand the mailbox layout.

# All numbers are unsigned of an appropriate size.  All strings are multiples
# of 32 (including the C terminating NULL) on 32-byte boundaries.  Then it
# all looks good in "od -Ad -c" and even better in "od -Ax -c -tu8 -tx8".

import ctypes
import mmap
import os
import struct
import sys

from os.path import stat as STAT    # for constants
from pdb import set_trace
from time import sleep
from time import time as NOW


class IVSHMSG_MailGlobals(ctypes.Structure):
    _fields_ = [        # A magic ctypes class attribute.
        ('slotsize',    ctypes.c_ulonglong),
        ('buf_offset',  ctypes.c_ulonglong),
        ('nClients',    ctypes.c_ulonglong),
        ('nEvents',     ctypes.c_ulonglong),
        ('server_id',   ctypes.c_ulonglong),
    ]


class IVSHMSG_MailSlot(ctypes.Structure):
    # c_char_p is variable length so force fixed size fields.

    _strsize = 32
    _bufsize = 384

    _fields_ = [            # A magic ctypes class attribute.
        ('_nodename',       ctypes.c_char * _strsize),
        ('_cclass',         ctypes.c_char * _strsize),
        ('buflen',          ctypes.c_ulonglong),
        ('peer_id',         ctypes.c_ulonglong),
        ('last_responder',  ctypes.c_ulonglong),
        ('peer_SID',        ctypes.c_ulonglong),
        ('peer_CID',        ctypes.c_ulonglong),
        ('pad',             ctypes.c_ulonglong * 3),
        ('buf',             ctypes.c_char * _bufsize)
    ]

    @property
    def nodename(self):
        return ctypes.string_at(self._nodename).decode()

    @property
    def cclass(self):
        return ctypes.string_at(self._cclass).decode()

    # This does not blank pad to the end, properly detects overrun, and
    # lays down a NUL properly UNLESS the entire space is filled.  Help
    # keep the length down and always preserve that final NUL.

    @nodename.setter
    def nodename(self, instr):
        inbytes = instr.encode()
        assert self._strsize > len(inbytes), '"%s" too big' % instr
        self._nodename = inbytes

    @cclass.setter
    def cclass(self, instr):
        inbytes = instr.encode()
        assert self._strsize > len(inbytes), '"%s" too big' % instr
        self._cclass = inbytes


class IVSHMSG_MailBox(object):

    # QEMU rules: file size (product of first two) must be a power of two.
    MAILBOX_MAX_SLOTS = 16    # Dummy + server leaves 14 actual clients
    MAILBOX_SLOTSIZE = 512
    FILESIZE = MAILBOX_MAX_SLOTS * MAILBOX_SLOTSIZE
    MS_BUF_off = 128
    MS_MAX_BUFLEN = 384
    assert MAILBOX_SLOTSIZE == MS_BUF_off + MS_MAX_BUFLEN, 'Big oops. Huge!'

    fd = None       # There can be only one
    mm = None       # Then I can access fill() from the class
    nClients = None
    nEvents = None
    server_id = None
    slots = None      # 0 == MailGlobal, 1 - server_id == MailSlot

    #-----------------------------------------------------------------------
    # Slots[] array: Globals at offset 0 (slot 0; each slot (1 through
    # nClients holds a peer, and the server is always at end.

    @classmethod
    def _initialize_mailbox(cls, args):
        cls.nClients = args.nClients
        cls.nEvents = args.nEvents
        cls.server_id = args.server_id

        # mm[] is bytes and that involves copies and manual indexing.  The
        # view is an overlay, especially when combined with ctype structures.
        cls.mm = mmap.mmap(cls.fd, 0)
        cls.view = memoryview(cls.mm)
        cls.slots = [ None, ] * cls.nEvents

        # Empty it.  Simple code that's never too demanding on size,
        # default of 16 slots == now 32k.
        data = b'\0' * cls.FILESIZE
        cls.mm[0:len(data)] = data

        # Fill in the globals; used by ivshmsg.ko and the C struct globals.
        # It's at the start of the memory area so no indexing is needed.
        mbg = IVSHMSG_MailGlobals.from_buffer(cls.view)
        mbg.slotsize = cls.MAILBOX_SLOTSIZE
        mbg.buf_offset = cls.MS_BUF_off
        mbg.nClients = cls.nClients
        mbg.nEvents = cls.nEvents
        mbg.server_id = cls.server_id
        cls.slots[0] = mbg

        # Get a data structure overlay for each slot, then set the peer_id
        # as a sentinel for other code.  Don't forget the server.

        for slot in range(1, cls.nEvents):
            cls.slots[slot] = IVSHMSG_MailSlot.from_buffer(
                cls.view[mbg.slotsize * slot : mbg.slotsize * (slot + 1)])
            cls.slots[slot].peer_id = slot

        # Server's "hostname" and Base Component Class.  Zero-padding occurs
        # because it was all zeroed out just above.
        name = 'Z-switch' if args.smart else 'Z-server'
        cls.slots[cls.server_id].nodename = name
        cls.slots[cls.server_id].cclass = 'FabricSwitch'

    #----------------------------------------------------------------------
    # Polymorphic.  Someday I'll learn about metaclasses.  While initialized
    # as an instance, use of the whole file and individual slots is done
    # as class methods and attributes, so __init__ is a singleton.

    _beentheredonethat = False

    def __init__(self, args=None, fd=-1, client_id=-1):
        '''Server: args with command line stuff from command line.
           Client: starts with an fd and id read from AF_UNIX socket.'''
        cls = self.__class__
        if cls._beentheredonethat:
            return
        cls._beentheredonethat = True

        if args is None:
            assert fd >= 0 and client_id > 0, 'Bad call, ump!'
            cls.fd = fd
            cls._init_mailslot(client_id)
            return
        assert fd == -1 and client_id == -1, 'Cannot assign fd/id to server'

        path = args.mailbox     # Match previously written code
        gr_gid = -1     # Makes no change.  Try Debian, CentOS, other
        for gr_name in ('libvirt-qemu', 'libvirt', 'libvirtd'):
            try:
                gr_gid = grp.getgrnam(gr_name).gr_gid
                break
            except Exception as e:
                pass

        if '/' not in path:
            path = '/dev/shm/' + path
        oldumask = os.umask(0)
        try:
            if not os.path.isfile(path):
                fd = os.open(path, os.O_RDWR | os.O_CREAT, mode=0o666)
                os.posix_fallocate(fd, 0, cls.FILESIZE)
                os.fchown(fd, -1, gr_gid)
            else:   # Re-condition and re-use
                lstat = os.lstat(path)
                assert STAT.S_ISREG(lstat.st_mode), 'not a regular file'
                assert lstat.st_size >= cls.FILESIZE, \
                    'existing size (%d) is < required (%d)' % (
                        lstat.st_size, cls.FILESIZE)
                if lstat.st_gid != gr_gid and gr_gid > 0:
                    print('Changing %s to group %s' % (path, gr_name))
                    os.chown(path, -1, gr_gid)
                if lstat.st_mode & 0o660 != 0o660:  # at least
                    print('Changing %s to permissions 666' % path)
                    os.chmod(path, 0o666)
                fd = os.open(path, os.O_RDWR)
        except Exception as e:
            raise RuntimeError('Problem with %s: %s' % (path, str(e)))

        os.umask(oldumask)

        cls.path = path                        # Final absolute path
        cls.fd = fd
        cls._initialize_mailbox(args)

    #----------------------------------------------------------------------
    # Dig the mail and node name out of the slot for peer_id (1:1 mapping).
    # It's not so much (passively) receivng mail as it is actively getting.

    @classmethod
    def retrieve(cls, peer_id, asbytes=False, clear=True):
        '''Return the message.'''
        ms = cls.slots[peer_id]
        # This next test seems paranoid, but also validates that id != 0
        # (which would have grabbed the MailGlobals).  Oh and it is self-
        # limiting past server_id.
        assert ms.peer_id == peer_id, '%d != %d: this is SO wrong' % (
            ms.peer_id, peer_id)
        buf = ms.buf[:ms.buflen]

        # The message is copied so mark the mailslot length zero as handshake
        # to the requester that its mailbox has been emptied.

        if clear:
            ms.buflen = 0

        return buf if asbytes else buf.decode()

    #----------------------------------------------------------------------
    # Post a message to the indicated mailbox slot but don't kick the
    # EventFD.  First, this routine doesn't know about them and second,
    # keeping it a separate operation facilitates sender spoofing.

    @classmethod
    def fill(cls, sender_id, buf):
        if isinstance(buf, str):
            buf = buf.encode()
        assert isinstance(buf, bytes), 'buf must be string or bytes'
        buflen = len(buf)
        assert buflen < cls.MS_MAX_BUFLEN, 'Message too long'

        # The previous responder needs to clear the msglen to indicate it
        # has pulled the message out of the sender's mailbox.
        ms = cls.slots[sender_id]
        stop = NOW() + 1.05
        intime = True
        while NOW() < stop and ms.buflen:
            sleep(0.1)
        if NOW() >= stop:
            intime = False
            print('pseudo-HW not ready to receive timeout: now stomping')

        ms.buflen = buflen
        ms.buf = buf
        return intime

    #----------------------------------------------------------------------
    # Called by Python client on graceful shutdowns, and always by server
    # when a peer dies.  This is mostly for QEMU crashes so the nodename
    # is not reused when a QEMU restarts, before loading ivshmsg.ko.

    @classmethod
    def clear_mailslot(cls, id):
        cls.slots[id].nodename = ''
        cls.slots[id].cclass = ''
        cls.slots[id].peer_id = id

    @classmethod
    def active_ids(cls):
        return sorted([
            slot.peer_id for slot in cls.slots[1:cls.server_id + 1] if
                slot.nodename])

    #----------------------------------------------------------------------
    # Called only by client.  mmap() the file and retrieve globals.

    @classmethod
    def _init_mailslot(cls, id):
        if cls.mm is None:
            # First time has some extra setup, not all vars need to be kept.
            buf = os.fstat(cls.fd)
            assert STAT.S_ISREG(buf.st_mode), 'Mailbox FD is not a regular file'
            cls.mm = mmap.mmap(cls.fd, 0)
            view = memoryview(cls.mm)
            mbg = IVSHMSG_MailGlobals.from_buffer(view)
            # Convenience
            cls.nClients = mbg.nClients
            cls.nEvents = mbg.nEvents
            cls.server_id = mbg.server_id

            # Create all the peer data structures now as it simplifies
            # connection logic removes interplay from twisted_client.py.
            cls.slots = [ None, ] * cls.nEvents
            cls.slots[0] = mbg
            for slot in range(1, cls.nEvents):
                cls.slots[slot] = IVSHMSG_MailSlot.from_buffer(
                    view[mbg.slotsize * slot : mbg.slotsize * (slot + 1)])
                assert cls.slots[slot].peer_id == slot, 'What happened?'

        if id > cls.server_id:  # Probably a test run of twisted_restapi
            return
        assert cls.slots[id].peer_id == id, 'What happened?'
        cls.clear_mailslot(id)

    #----------------------------------------------------------------------
    # Typing conveniences.  No setters, use the full expression.

    @classmethod
    def nodename(cls, index):
        return cls.slots[index].nodename

    @classmethod
    def cclass(cls, index):
        return cls.slots[index].cclass
