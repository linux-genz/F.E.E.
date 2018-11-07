#!/usr/bin/python3

# https://gist.github.com/berlincount/409a631d49c3be210abd

import attr
import json
import os
import struct
import sys

from collections import defaultdict, OrderedDict
from pdb import set_trace
from pprint import pformat, pprint

from klein import Klein                             # Uses default reactor...

from twisted.internet import reactor as TIreactor   # ...like twisted_server
from twisted.web import server as TWserver


class MailBoxReSTAPI(object):

    N = attr.make_class('Nodes', [])
    L = attr.make_class('Links', [])

    app = Klein()   # Now its decorators can be used on methods below
    isLeaf = True   # TWserver seems to need this

    mb = None
    mm = None
    nClients = None
    nEvents = None
    server_id = None
    nodes = None

    @classmethod
    def mb2dict(cls):
        thedict = OrderedDict((
            ('nClients', cls.nClients),
            ('server_ivshmsg_id', cls.server_ivshmsg_id),
        ))

        # The D3 Javascript framework refers to a node's name as its "id".
        for ivshmsg_id in range(1, cls.nEvents):
            this = cls.nodes[ivshmsg_id]
            this.ivshmsg_id = ivshmsg_id
            this.id = cls.mb.slots[ivsmsg_id].nodename
            this.cclass = cls.mb.slots[ivshmsg_id].cclass
            this.hardware = cls.cclass_to_hardware_type(this.cclass)

            # Since they all connect to the one switch (no P2P)
            this.group = 'port_%s' % ivshmsg_id

            # this.CID = 0        # Later
            # this.SID = 0
            # this.TXpackets = 0
            # this.RXpackets = 0
            # this.port = 0
        thedict['nodes'] = [ vars(n) for n in cls.nodes[1:] if n.id ]

        links = []
        server_id = cls.nodes[cls.server_ivshmsg_id].id    # a string
        for node in thedict['nodes']:
            id = node['id']             # also a string
            if id and id != server_id:
                links.append({'source': id, 'target': server_id})
        thedict['links'] = links
        return thedict

    def cclass_to_hardware_type(cclass_name):
        lower = cclass_name.lower()
        for hw in ('qemu', 'debugger', 'adapter', 'switch'):
            if hw in lower:
                return hw
        return lower

    @app.route('/system')
    def get_system(self, request):
        thedict = self.mb2dict()

        # Twisted "fixes" the case of headers and uses bytearrays.
        reqhdrs = dict(request.requestHeaders.getAllRawHeaders())
        request.setHeader('Access-Control-Allow-Origin', '*')

        return json.dumps(thedict)
        if b'Apiversion' in reqhdrs:
            return json.dumps(thedict)
        return('<PRE>%s</PRE>' % pformat(dict(thedict)))

    @app.route('/')
    def home(self, request):
        # print('Received "%s"' % request.uri.decode(), file=sys.stderr)
        reqhdrs = dict(request.requestHeaders.getAllRawHeaders())

        return '<PRE>\n%s\nUse /system\n</PRE>' % '\n'.join(
            sorted([k.decode() for k in reqhdrs.keys()]))

    # Must come after all Klein dependencies and @decorators
    def __init__(self, already_initialized_IVSHMSG_mailbox, port=1991):
        cls = self.__class__
        if cls.mb is not None:
            return
        cls.mb = already_initialized_IVSHMSG_mailbox
        cls.nClients = cls.mb.nClients
        cls.nEvents = cls.mb.nEvents
        cls.server_ivshmsg_id = cls.mb.server_id      # see mb2dict
        # Clients/ports are enumerated 1-nClients inclusive
        cls.nodes = [ cls.N() for _ in range(cls.nEvents) ]

        # Instead of this.app.run(), break it open and wait for
        # twister_server.py to finally invoke TIreactor.run() as all these
        # things use the default reactor.  Note that self.app was assigned
        # durng the class-level scan/eval of this source file.  See also
        # /usr/lib/python3/dist-packages/klein/app.py::run()
        s = TWserver.Site(self.app.resource())
        TIreactor.listenTCP(port, s)


if __name__ == '__main__':

    from twisted.python import log as TPlog	# Deprecated

    from ivshmsg_mailbox import IVSHMSG_MailBox

    # These things are done explicitly in twisted_server.py
    fname = '/dev/shm/ivshmsg_mailbox' if len(sys.argv) < 2 else sys.argv[1]
    if not fname or fname[0] == '-':
        raise SystemExit('usage: %s [ /path/to/mailbox ]' % sys.argv[0])
    print('Opening', fname)
    fd = os.open(fname, os.O_RDWR)
    mb = IVSHMSG_MailBox(fd=fd, client_id=99)
    tmp = MailBoxReSTAPI(mb)

    # This is done explicitly in twisted_server.py
    TPlog.startLogging(sys.stdout, setStdout=False)

    # This is done implicitly after protocol registration in full app.
    TIreactor.run()
