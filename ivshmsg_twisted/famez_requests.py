#!/usr/bin/python3

# Common routines for both server (switch) and clients which drill down
# on messages retrieved by parsing and generating a response.

import attr
import os
import functools
import sys

from collections import OrderedDict
from pprint import pprint

try:
    from ivshmsg_mailbox import IVSHMSG_MailBox as MB
except ImportError as e:
    from .ivshmsg_mailbox import IVSHMSG_MailBox as MB

def PRINT(*args):
    print(*args, file=_stdtrace)

def PPRINT(*args):
    pprint(*args, stream=_stdtrace)

###########################################################################
# Better than __slots__ although maybe this should be the precursor for
# making this entire file a class.  It's all items that cover the gamut
# of requests.  "this" is the object with SID0, CID0, LinkState and cclass.


ResponseObject = attr.make_class('ResponseObject',
    ['this', 'proxy', 'from_id', 'to_doorbell',
     'logmsg', 'stdtrace', 'verbose'])

###########################################################################
# Create a subroutine name out of the elements passed in.  Return
# the remainder.  Start with the least-specific construct.


def _unprocessed(client, *args, **kwargs):
    return False
    if client.SI.verbose:
        _logmsg('NOOP', args, kwargs)
    return False


def chelsea(elements, verbose=0):
    entry = ''          # They begin with a leading '_', wait for it...
    G = globals()
    for i, e in enumerate(elements):
        e = e.replace('-', '_')         # Such as 'Link CTL Peer-Attribute'
        entry += '_%s' % e
        if verbose > 1:
            print('Looking for %s()...' % entry, end='', file=_stdtrace)
        if entry in G:
            args = elements[i + 1:]
            if verbose > 1:
                PRINT('found it->%s' % str(args))
            return G[entry], args
        if verbose > 1:
            PRINT('NOPE')
    return _unprocessed, elements

###########################################################################


def CSV2dict(oneCSVstr):
    kv = {}
    elems = oneCSVstr.strip().split(',')
    for e in elems:
        try:
            KeV = e.strip().split('=')
            kv[KeV[0].strip()] = KeV[1].strip()
        except Exception as e:
            continue
    return kv

###########################################################################
# Here instead of ivshmsg_mailbox to manage the tag.  Can be called as a
# "discussion initiator" usually from the REPL interpreters, or as a
# response to a received command from the callbacks.

_next_tag = 1               # Gen-Z tag field

_tagged = OrderedDict()     # By tag, just store receiver now

_tracker = 0                # EmerGen-Z addenda to watch conversations

_TRACKER_TOKEN = '!EZT='

def send_payload(payload, from_id, to_doorbell, reset_tracker=False,
                 tag=None, tagCID=0, tagSID=0):
    global _next_tag, _tracker

    # PRINT('Send "%s" from %d to %s' % (payload, from_id, vars(to_doorbell)))

    if tag is not None:     # zero-length string can trigger this
        payload += ',Tag=%d' % _next_tag
        _tagged[str(_next_tag)] = '%d.%d!%s|%s' % (
            tagCID, tagSID, payload, tag)
        _next_tag += 1

    # Put the tracker on the end where it's easier to find
    if reset_tracker:
        _tracker = 0
    _tracker += 1
    payload += '%s%d' % (_TRACKER_TOKEN, _tracker)

    ret = MB.fill(from_id, payload)     # True == no timeout, no stomp
    to_doorbell.ring()
    return ret

###########################################################################
# Gen-Z 1.0 "6.8 Standalone Acknowledgment"
# Received by server/switch


def _Standalone_Acknowledgment(response_receiver, args):
    retval = True
    tag = False
    try:
        kv = CSV2dict(args[0])
        stamp, tag = _tagged[kv['Tag']].split('|')
        del _tagged[kv['Tag']]
        tag = tag.strip()
        kv = CSV2dict(tag)
    except KeyError as e:
        _stdtrace('UNTAGGING %d:%s FAILED' %
            (response_receiver.from_id, response_receiver.nodename))
        retval = False
        kv = {}

    afterACK = kv.get('AfterACK', False)
    if afterACK:
        send_payload(afterACK,
                     response_receiver.from_id,
                     response_receiver.to_doorbell)

    if _tagged:
        PRINT('Outstanding tags:')
        PPRINT(_tagged)
    return 'dump'


def _send_SA(RO, tag, reason):
    payload = 'Standalone Acknowledgment Tag=%s,Reason=%s' % (tag, reason)
    return send_payload(payload, RO.from_id, RO.to_doorbell)

###########################################################################
# Gen-Z 1.0 "11.11 Link CTL" subfield
# Sent by clients


def send_LinkACK(RO, details, nack=False):
    if nack:
        payload = 'Link CTL NAK %s' % details
    else:
        payload = 'Link CTL ACK %s' % details
    return send_payload(payload, RO.from_id, RO.to_doorbell)

###########################################################################
# Gen-Z 1.0 "6.10.1 P2P Core..."
# Received by client, only really expecting RFC data


def _CTL_Write(RO, args):
    kv = CSV2dict(args[0])
    if int(kv['Space']) != 0:
        return False
    RO.this.CID0 = int(kv['CID'])
    RO.this.SID0 = int(kv['SID'])
    RO.this.PFMCID0 = int(kv['PFMCID'])
    RO.this.PFMSID0 = int(kv['PFMSID'])
    RO.this.linkattrs['State'] = 'configured'
    return _send_SA(RO, kv['Tag'], 'OK')

###########################################################################
# Gen-Z 1.0 "11.6 Link RFC"
# Received by switch


def _Link_RFC(RO, args):
    if not RO.this.isPFM:
        _logmsg('I am not a manager')
        return False
    try:
        kv = CSV2dict(args[0])
        delay = kv['TTC'].lower()
    except (IndexError, KeyError) as e:
        _logmsg('%d: Link RFC missing TTC' % RO.from_id)
        return False
    if not 'us' in delay:  # greater than cycle time of this server
        _logmsg('Delay %s is too long, dropping request' % delay)
        return False
    payload = 'CTL-Write Space=0,PFMCID=%d,PFMSID=%d,CID=%d,SID=%d' % (
        RO.this.CID0, RO.this.SID0, RO.proxy.CID0, RO.proxy.SID0)
    return send_payload(payload, RO.from_id, RO.to_doorbell,
                        tag='AfterACK=Link CTL Peer-Attribute',
                        tagCID=RO.this.CID0, tagSID=RO.this.SID0)

###########################################################################
# Gen-Z 1.0 "11.11 Link CTL"
# Entered on both client and server responses.


def _Link_CTL(RO, args):
    '''Subelements should be empty.'''
    arg0 = args[0] if len(args) else ''
    if len(args) == 1:
        if arg0 == 'Peer-Attribute':
            attrs = 'cclass=%s,CID0=%d,SID0=%d' % (
                RO.this.cclass, RO.this.CID0, RO.this.SID0)
            return send_LinkACK(RO, attrs)

    if arg0 == 'ACK' and len(args) == 2:
        # Update the local proxy values, ASS-U-ME it's peerattrs
        # FIXME: correlation ala _tagged?  How do I know it's peer attrs?
        # FIXME: add a key to the response...
        if RO.proxy is None:
            RO.this.peerattrs = CSV2dict(args[1])
        else:
            RO.proxy.peerattrs = CSV2dict(args[1])
        return 'dump'

    if arg0 == 'NAK':
        # FIXME: do I track the sender ala _tagged and deal with it?
        PRINT('Got a NAK, not sure what to do with it.')
        return False

    _logmsg('Got %s from %d' % (str(args), RO.from_id))
    return False

###########################################################################
# Finally a home


def _ping(RO, args):
    return send_payload('pong', RO.from_id, RO.to_doorbell)


def _dump(RO, args):
    return 'dump'      # Technically "True", but with baggage


###########################################################################
# Chained from EventReader callback in twisted_[client|server].py.
# Command streams are case-sensitive, read the spec.
# Return True if successfully parsed and processed.

_logmsg = None
_stdtrace = None


def handle_request(request, requester_name, response_object):
    global _logmsg, _stdtrace, _tracker

    assert isinstance(response_object, ResponseObject), 'Bad response object'
    if _logmsg is None:
        _logmsg = response_object.logmsg   # FIXME: logger.logger...
        _stdtrace = response_object.stdtrace

    elements = request.split(_TRACKER_TOKEN)
    payload = elements.pop(0)
    trace = '\n%10s -> "%s"' % (requester_name, payload)
    EZT = int(elements[0]) if elements else False
    if EZT:
        trace += ' (%d)' % EZT
        _tracker = EZT
    PRINT(trace)

    elements = payload.split()
    try:
        handler, args = chelsea(elements, response_object.verbose)
        return handler(response_object, args)
    except KeyError as e:
        _logmsg('KeyError: %s' % str(e))
    except Exception as e:
        _logmsg(str(e))
    return False
