[Gen-Z is a new memory-semantic fabric](https://genzconsortium.org/) created
as the glue for constructing exascale computing.  It is an open specification
evolved from the fabric used in
[The Machine from Hewlett Packard Enterprise](https://www.hpe.com/TheMachine).
Such fabrics allow "wide-area" connectivity of computing resources such as CPU,
GPU, memory (legacy and persistent) and other devices via a memory-semantic
programming model.

The Gen-Z spec and working groups are evolving the standard and early
hardware is beginning to appear.  However there is not an open "platform"
on which to develop system software.  The success of QEMU and IVSHMEM as
[an emulated development platform for The Machine](docs/FAME_background.md)
suggests an extended use should be considered. 
  
### Beyond IVSHMEM - a rudimentary fabric

QEMU has another feature of interest in a multi-actor messaging environment
like that of Gen-Z.  By applying a slightly different stanza, the IVSHMEM
virtual PCI device is enabled to send and handle interrupts in a
"mailbox/doorbell" setup.   An interrupt to the virtual PCI device is generated
from an "event notification" issued to the QEMU process by a similarly
configured peer QEMU.  But how are these peers connected?

The scheme starts with a separate program delivered with QEMU. [
```/usr/bin/ivshmem-server ```](
https://github.com/qemu/qemu/blob/master/docs/specs/ivshmem-spec.txt)
establishes a UNIX-domain socket and must be started before any properly
configured QEMU VMs.  A new QEMU process starts by connecting to the socket
and receiving its own set of event channels, as well as those of all other
peers.  The mechanism in each guest OS is that writing a "doorbell" register
will signal the QEMU into an event against another QEMU.  The receiving QEMU
transforms that event into a PCI interrupt for its guest OS.  

```ivshmem-server``` only informs each QEMU of its other peers; it does not
participate in further peer-to-peer communcation.  A backing file must also
be specified to ivshmem-server for use as a message mailbox.  Obviously the
guests/clients must agree on the use of the mailbox file.  Standard
ivshmem-server never touches the file contents.

![alt text][IVSHMSG]

[IVSHMSG]: https://github.com/linux-genz/F.E.E./blob/master/docs/images/IVSHMSG%20block.png "Figure 1"

The final use case above is QEMU guest-to-guest communication over the "IVSHMSG
doorbell/mailbox fabric".  OS-to-OS communication will involve a (new) guest
kernel driver and other abstractions to hide the mechanics of IVSHMSG.
This IVSHMSG shim can serve as the foundation for higher-level protocols.

## Gen-Z Emulation on top of IVSHMSG

If the guest OS driver emulates a simple Gen-Z bridge, a great deal of
"pure Gen-Z" software development can be done on this simple platform.
Certain Gen-Z primitive operations for discovery and crawlout
would also be abetted by intelligence "in the fabric".  In fact, that 
intelligence could live in the ivshmem-server process if it were
extended to participate in actual messaging.

Modifying the existing ```ivshmem-server`` C program is not a simple challenge.
Written within the QEMU build framework, it is not standalone source code.
C is a also limited for higher-level data constructs anticipated for a Gen-Z
emulation.  Finally, it seems unlikely such changes would be accepted upstream.

F.E.E. is a rewrite of ivshmem-server in Python using Twisted
as the network-handling framework.  ```ivshmsg_server.py``` is run in place of
```ivshmem-server```.  It correctly serves real QEMU processes as well as
the stock QEMU ``ivshmem-client``, a test program that comes with QEMU.

![alt text][EMERGEN-Z]

[EMERGEN-Z]: https://github.com/linux-genz/F.E.E./blob/master/docs/images/FEE%20block.png "Figure 2"

A new feature for the Python version is server participation 
in the doorbell/mailbox messaging to serve as fabric intelligence
(ie, a smart switch).

___

## Running the Python rewrites

As ivshmsg_server.py was being created, it was tested with the QEMU
```/usr/bin/ivshmem-client```.  As might be expected, there is now an
ivshmsg_client.py rewrite.   It has an expanded command set and over
time its use as a monitor/debugger/injector will certainly grow.

To use these programs as a simple chat framework you don't even need QEMU.

### ivshmsg_server.py
1. Clone this repo
1. Install python3 packages ```twisted``` and ```klein``` (names will vary by distro)
1. In one terminal window run './ivshmsg_server.py'.
1. Type "help" and hit return.

By default it creates /tmp/ivshmsg_socket to which up to clients attach,
and /dev/shm/ivshmsg_mailbox which is shared among all clients for messaging.
The window expresses an interactive interface, the command set is quite simple
at the moment.  Try "dump".  This will get you a visual representation of a
14-port switch.  The port number is the raw messaging source/destination
of attached clients.  The soft-switch itself is ID 15; there is no ID 0.

In general, ivshmsg_server.py could be used for many more messaging 
protocols beyond Gen-Z.   However, it currently has a "personality"
that interprets some of the Link level messages at a semantic level.
That's best seen with a debugger client.

### ivshmsg_client.py

1. In a second (or more) terminal window(s) run 'ivshmsg_client.py'.  

You'll see them get added in the server window if default values were used 
in the server invocation.  Each client is assigned a random "port".

1. In one of the clients, hit return, then type "help".  Play with sending messages to the other client(s) or the server.

Hit "d" or "dump" to see local information.

Try "ping", as in "ping NN" or "ping <name>"

"Send" is the next message, and now we have an overly complicated chatroom.

Try "Link" or "Link Peer-Attributes"

Finally try "RFC", followed by "dump".   It should look different :-)

## Generating and connecting VMs

When you get the Python programs playing together (above), you'll be ready
for another story [which is going to be told here.](docs/VMconfig.md)

Once you have VMs configured and connected as "Driverless QEMU" actors,
[proceed to the prototype Gen-Z subsystem repo](http://github.com/linux-genz/EmerGen-Z/).

___

## Bugs and Performance

As the QEMU docs say, "(IVSHMSG) is simple and fragile" and sometimes
* A QEMU session will lose connection with the server, hang, die, or otherwise need a restart.
* There's a pseudo-hardware holdoff/interlock timeout in the guest kernel drivers which can cause a VM core to go into RCU stall which usually leads to a virtual panic.
* RARELY do you have to restart ivshmsg_server.py, but if you have to restart the QEMUs anyhow, it never hurts.
* Debugging under Python Twisted will make you go blind.

Most of these problems only show up in a stripped-down, dedicated speed run.
On "average" hardware the setup can reach 20k messages/second between
two VMs (100-bytes messages, or 2Mb/sec) sustained over minutes of time.  

Data rates are typically MUCH smaller when doing the type of programming
for which F.E.E. is reall intended (bridge-based inter-actor fabric 
management).  It's been stable enough to promote generation of a
prototype Gen-Z subsystem for the kernel.  

[Read all about that at this repo](http://github.com/linux-genz/EmerGen-Z/).

## Backlog

A few things the primary author has thought about or heard...

* A severe documentation review and refresh would help drive adoption.
* Extract the Gen-Z "personality" into a more modular plugin basis for re-use of F.E.E. for other fabrics.
* Move to shared code as the interactive command engine (or at least coalesce it more) as part of those extracted personalities.
* More intelligence in the switch
  * Particularly around fabric management
  * "True bridging", right now the clients cheat and talk directly to each other
  * Error injection
* More switches
