import base64
import binascii

from box import Box

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from netsim.augment import devices
from netsim.augment import links as _links
from netsim.data import get_box
from netsim.utils import log

from ... import tunnel as _tunnel
from .. import _p2p

_config_name = 'tunnel.wireguard'


_WG_KEY_SIZE = 32


def _clamp_wireguard_private_key(key_data: bytes) -> bytes:
  '''
  Clamp an X25519 private key using the representation produced by wg genkey.
  '''
  if len(key_data) != _WG_KEY_SIZE:
    raise ValueError(
      f'WireGuard private key must contain {_WG_KEY_SIZE} bytes')

  clamped_key = bytearray(key_data)
  clamped_key[0] &= 248
  clamped_key[31] &= 127
  clamped_key[31] |= 64

  return bytes(clamped_key)

def _encode_wireguard_key(key_data: bytes) -> str:
  '''
  Encode a raw WireGuard key using the Base64 representation expected by
  WireGuard.
  '''
  return base64.b64encode(key_data).decode('ascii')

def _decode_wireguard_private_key(private_key: str) -> bytes:
  '''
  Decode and validate a Base64-encoded WireGuard private key.
  '''
  try:
    key_data = base64.b64decode(private_key,validate=True)
  except (binascii.Error,ValueError) as ex:
    raise ValueError('private key is not valid Base64') from ex

  if len(key_data) != _WG_KEY_SIZE:
    raise ValueError(
      f'private key must decode to {_WG_KEY_SIZE} bytes, '
      f'not {len(key_data)} bytes')

  return key_data

def public_key_from_private(private_key: str) -> str:
  '''
  Derive a WireGuard public key from a private key using wireguard-tools
  '''
  try:
    key_data = _decode_wireguard_private_key(private_key)
    key = X25519PrivateKey.from_private_bytes(key_data)

    return _encode_wireguard_key(key.public_key().public_bytes_raw())

  except ValueError as ex:
     log.fatal(
       f'Cannot derive WireGuard public key: {ex}',
       'or configure tunnel.private_key and tunnel.public_key',
      module='tunnel.wireguard')

def generate_keypair() -> tuple[str, str]:
  '''
  Generate a WireGuard-compatible private/public X25519 key pair.
  '''
  generated_key = X25519PrivateKey.generate()

  # cryptography preserves the generated raw 32-byte value. WireGuard's
  # `wg genkey` prints the clamped representation, so clamp the serialized
  # value before encoding it.
  private_key_data = _clamp_wireguard_private_key(generated_key.private_bytes_raw())

  # Re-create the key from the exact bytes that will be returned so the
  # private/public pair is explicitly derived from the same representation.
  private_key = X25519PrivateKey.from_private_bytes(private_key_data)

  return (
    _encode_wireguard_key(private_key_data),
    _encode_wireguard_key(private_key.public_key().public_bytes_raw())
    )

def ensure_tunnel_keys(
      node: str,
      intf: Box,
      topology: Box) -> bool:
  '''
  Ensure tunnel interface data has a matching private/public key pair
  '''
  private_key = intf.tunnel.get('private_key',None)
  public_key = intf.tunnel.get('public_key',None)

  if private_key and public_key:
    return True

  if public_key and not private_key:
    linkname = _links.get_linkname(topology,intf.linkindex)
    log.error(
      f'tunnel.public_key without tunnel.private_key on node {node} interface {intf.ifname} ({intf.name})',
      more_data=f'link {linkname}',
      category=log.IncorrectValue,
      module='tunnel.wireguard')
    return False

  if private_key:
    intf.tunnel.public_key = public_key_from_private(private_key)
    return True

  private_key, public_key = generate_keypair()
  intf.tunnel.private_key = private_key
  intf.tunnel.public_key = public_key
  if log.VERBOSE:
    log.info(
      f'Generated WireGuard key pair for node {node} interface {intf.ifname} ({intf.name})',
      module='tunnel.wireguard')
  return True

def add_linux_packages(node: Box, topology: Box) -> None:
  '''
  Add wireguard-tools to the node package list.

  Set netlab_linux_packages per node (not device-wide) to install
  wireguard-tools only on nodes with tunnels. The host var replaces the group
  var, so start from the device defaults. Installed during initial config.
  '''
  packages = get_box(devices.get_node_group_var(node,'netlab_linux_packages',topology.defaults) or {})
  packages['wireguard-tools'] = 'wg'
  node.netlab_linux_packages = packages

def wireguard_intf_defaults(ndata: Box, intf: Box, topology: Box) -> bool:
  '''
  Set WireGuard-specific interface defaults after tunnel._source is known
  '''
  if not ensure_tunnel_keys(ndata.name,intf,topology):
    return False

  # Auto-derive the transport AF from the selected source interface, preferring IPv4 for dual-stack tunnels.
  if 'tunnel.af' not in intf:
    intf.tunnel.af = 'ipv4' if 'ipv4' in intf.tunnel._source else 'ipv6'

  # Default the peer's allowed IPs (the inner/overlay prefixes carried by the
  # tunnel) to a default route per active address family. Use the node's global
  # active AFs, so dual-stack tunnels permit both ranges.
  if 'tunnel.allowed_ips' not in intf:
    af_active = ndata.get('af',{})
    ranges = [ prefix for af,prefix in (('ipv4','0.0.0.0/0'),('ipv6','::/0')) if af_active.get(af) ]
    intf.tunnel.allowed_ips = ','.join(ranges) or '0.0.0.0/0'

  # Derive the WireGuard interface MTU from the underlay source interface
  # MTU minus the encapsulation overhead (80 bytes for an IPv6 underlay,
  # 60 bytes for an IPv4 underlay) so it scales with jumbo-frame underlays.
  if 'mtu' not in intf:
    overhead = 80 if intf.tunnel.af == 'ipv6' else 60
    intf.mtu = intf.tunnel._source.mtu - overhead

  # Peer nodes reach us via the underlay addresses in tunnel._source. Copy the
  # WireGuard socket attributes there too so tunnel_destination can set
  # tunnel._destination in one step like other P2P tunnel plugins.
  intf.tunnel._source.public_key = intf.tunnel.public_key
  intf.tunnel._source.listen_port = intf.tunnel.listen_port
  return True

def pre_transform(topology: Box) -> None:
  '''
  pre_transform hook: set tunnel link type, check whether WireGuard tunnels are P2P
  '''
  _tunnel.set_tunnel_type(topology)
  for link in _tunnel.links(topology,'wireguard'):
    if len(link.interfaces) != 2:
      log.error(
        f'A WireGuard tunnel must have exactly two nodes attached to it (link {link._linkname})',
        category=log.IncorrectAttr,
        module='tunnel.wireguard')

def post_transform(topology: Box) -> None:
  '''
  post_transform hook: check device support, set tunnel interface source/peer data
  '''

  # Use shared P2P tunnel function to check feature support
  #
  node_iflist = _p2p.feature_check(topology,t_mode='wireguard',t_desc='WireGuard tunnels')
  _p2p.tunnel_source(topology,node_iflist,t_name='WireGuard')

  for node in node_iflist:
    ndata = topology.nodes[node]
    for intf in _tunnel.interfaces(ndata,'wireguard'):
      if 'wireguard-tools' not in ndata.netlab_linux_packages:
        add_linux_packages(ndata,topology)

      if not wireguard_intf_defaults(ndata,intf,topology):
        return

  _p2p.tunnel_destination(topology,node_iflist,t_mode='wireguard')
