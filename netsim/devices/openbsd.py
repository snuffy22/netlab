#
# OpenBSD quirks
#
from box import Box

from ..utils import log, routing
from . import _Quirks, report_quirk
from ._common import check_indirect_static_routes

"""
You cannot reach OpenBSD loopback interface unless it runs as a router (with IP forwarding)
This quirk changes the node 'mode' if the node has a loopback interface.
"""
def check_loopback(node: Box, topology: Box) -> None:
  if not node.get('loopback',None):
    return

  if node.get('role','host') == 'host':
    node.role = 'router'
    report_quirk(
      f'node {node.name} has a loopback interface. Changing node role to router',
      quirk='role_router',
      category=Warning,
      node=node)

"""
OpenBSD OSPFv3 implementation is not an ABR
"""
def check_ospf6_quirks(node: Box) -> None:
  if 'ospf' not in node.get('module',[]):                   # Is the device running OSPF?
    return

  for o_data,_,vrf in routing.rp_data(node,'ospf'):
    if 'ipv6' not in o_data.get('af',{}):
      continue

    if not o_data.get('_abr',False):
      continue

    report_quirk(
      f'node {node.name} cannot be an OSPFv3 ABR',
      more_hints=['OpenBSD OSPFv3 daemon does not implement the ABR functionality'],
      quirk='ospfv3_abr',
      category=log.IncorrectType,
      node=node)

  passive_list = []
  for intf in node.interfaces:                              # Next, check for 'passive' OSPF interfaces
    if 'ospf' not in intf or 'ipv6' not in intf:            # Skip interfaces not running OSPF or not having an IPv6 address
      continue
    if 'cost' in intf.ospf and 'passive' in intf.ospf:      # Find passive interfaces with explicit cost
      passive_list.append(intf.ifname)

  if passive_list:
    report_quirk(
      f'node {node.name} uses passive OSPFv3 interfaces with OSPF cost ({",".join(passive_list)})',
      more_hints=['OpenBSD OSPFv3 daemon sets the cost of passive interfaces to 65535'],
      quirk='ospfv3_passive',
      category=Warning,
      node=node)

class OpenBSD(_Quirks):

  @classmethod
  def device_quirks(self, node: Box, topology: Box) -> None:
    check_loopback(node,topology)
    check_indirect_static_routes(node)
    check_ospf6_quirks(node)
