# Build privacy-preserving usage batches for optional upload.
#
# This module intentionally uses only the Python standard library. Keeping the
# aggregation and payload code independent of python-box makes it straightforward
# to unit test and prevents topology-specific objects from leaking into payloads.

from __future__ import annotations

import datetime
import re
import typing
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, MutableMapping

SCHEMA_VERSION = 1
CUSTOM_ITEM = '_custom'
OTHER_ITEM = '_other'

Metric = MutableMapping[str, int]
Batch = MutableMapping[str, typing.Any]

# The client-side allowlist is deliberately conservative. A built-in added in a
# future netlab release is reported as _custom until this list is updated. That
# loses some granularity but never exposes a user-defined identifier.
ALLOWLISTS: dict[str, set[str]] = {
  'provider': {
    'clab', 'external', 'libvirt', 'none', 'podman', 'virtualbox', 'vmware'
  },
  'device': {
    'arubacx', 'bird', 'cat8000v', 'ceos', 'csr', 'cumulus', 'cumulus_nvue',
    'dellos10', 'eos', 'exos', 'fortios', 'frr', 'iol', 'ioll2', 'ios', 'iosv',
    'iosvl2', 'junos', 'linux', 'nxos', 'openbsd', 'routeros', 'routeros7',
    'sonic', 'srlinux', 'sros', 'vios', 'vjunos-router', 'vjunos-switch', 'vmx',
    'vpp', 'vptx', 'vsrx', 'vyos'
  },
  'module': {
    'bfd', 'bgp', 'dhcp', 'eigrp', 'evpn', 'gateway', 'isis', 'lag', 'ldp',
    'lldp', 'mpls', 'ospf', 'rip', 'routing', 'sr', 'srv6', 'stp', 'vlan',
    'vrf', 'vrrp', 'vxlan'
  },
  'plugin': {
    'bgp.session', 'files', 'multilab', 'nodeset', 'ospf.areas', 'validate',
    'vrf'
  },
  'command': {
    'capture', 'clab', 'collect', 'config', 'connect', 'create', 'defaults',
    'down', 'exec', 'graph', 'help', 'initial', 'install', 'inspect', 'libvirt',
    'report', 'restart', 'show', 'status', 'tc', 'test', 'tools', 'up',
    'validate', 'version'
  }
}

_FIXED_ITEMS = {
  'topology': {'all'},
  'node': {'all'},
  'link': {'all'}
}
_ALLOWED_DIMENSIONS = set(ALLOWLISTS) | set(_FIXED_ITEMS)
_SAFE_ITEM = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,63}$')


def utc_day(now: datetime.datetime | None = None) -> str:
  value = now or datetime.datetime.now(datetime.timezone.utc)
  if value.tzinfo is None:
    value = value.replace(tzinfo=datetime.timezone.utc)
  return value.astimezone(datetime.timezone.utc).date().isoformat()


def version_for_upload(version: str) -> str:
  """Return a coarse major/minor version, never a local build identifier."""
  match = re.search(r'(\d+)\.(\d+)', str(version))
  return f'{match.group(1)}.{match.group(2)}' if match else '0.0'


def normalize_item(dimension: str, item: typing.Any) -> str:
  """Map an item into a finite, privacy-preserving vocabulary."""
  dimension = str(dimension).lower()
  if dimension not in _ALLOWED_DIMENSIONS:
    raise ValueError(f'Unsupported usage dimension {dimension}')

  value = str(item).strip().lower()
  if not _SAFE_ITEM.fullmatch(value):
    value = ''

  if dimension in _FIXED_ITEMS:
    return 'all'

  if value in ALLOWLISTS[dimension]:
    return value

  return OTHER_ITEM if dimension == 'command' else CUSTOM_ITEM


def new_batch(now: datetime.datetime | None = None) -> dict[str, typing.Any]:
  day = utc_day(now)
  return {
    'schema': SCHEMA_VERSION,
    'batch_id': str(uuid.uuid4()),
    'period_start': day,
    'period_end': day,
    'metrics': {}
  }


def batch_has_data(batch: Mapping[str, typing.Any] | None) -> bool:
  if not isinstance(batch, Mapping):
    return False
  metrics = batch.get('metrics')
  if not isinstance(metrics, Mapping):
    return False
  return any(bool(items) for items in metrics.values() if isinstance(items, Mapping))


def valid_batch(batch: typing.Any) -> bool:
  return isinstance(batch, Mapping) \
    and batch.get('schema') == SCHEMA_VERSION \
    and isinstance(batch.get('batch_id'), str) \
    and isinstance(batch.get('metrics'), Mapping)


def ensure_batch(container: MutableMapping[str, typing.Any], key: str = '_pending') -> Batch:
  batch = container.get(key)
  if not valid_batch(batch):
    batch = new_batch()
    container[key] = batch
  return typing.cast(Batch, batch)


def update_metric(
      batch: Batch,
      dimension: str,
      item: typing.Any,
      instances: int,
      observations: int = 1,
      maximum: int | None = None,
      now: datetime.datetime | None = None) -> None:
  if isinstance(instances, bool) or not isinstance(instances, int) or instances < 0:
    raise ValueError('Usage metric instances must be a non-negative integer')
  if isinstance(observations, bool) or not isinstance(observations, int) or observations < 1:
    raise ValueError('Usage metric observations must be a positive integer')
  if maximum is None:
    maximum = instances
  if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
    raise ValueError('Usage metric maximum must be a non-negative integer')

  dimension = str(dimension).lower()
  item_name = normalize_item(dimension, item)
  metrics = batch.setdefault('metrics', {})
  dim_metrics = metrics.setdefault(dimension, {})
  metric = dim_metrics.setdefault(item_name, {
    'observations': 0,
    'instances': 0,
    'maximum': 0
  })
  metric['observations'] += observations
  metric['instances'] += instances
  metric['maximum'] = max(metric['maximum'], maximum)
  batch['period_end'] = utc_day(now)


def _mapping(value: typing.Any) -> Mapping[str, typing.Any]:
  return value if isinstance(value, Mapping) else {}


def _sequence(value: typing.Any) -> list[typing.Any]:
  if isinstance(value, (list, tuple, set)):
    return list(value)
  return []


def collect_topology(
      batch: Batch,
      topology: Mapping[str, typing.Any],
      provider_getter: Callable[[Mapping[str, typing.Any]], str] | None = None,
      now: datetime.datetime | None = None) -> None:
  """Aggregate one fully transformed topology into a pending upload batch."""
  nodes = _mapping(topology.get('nodes'))
  links = _sequence(topology.get('links'))

  update_metric(batch, 'topology', 'all', 1, now=now)
  update_metric(batch, 'node', 'all', len(nodes), now=now)
  update_metric(batch, 'link', 'all', len(links), now=now)

  primary_provider = str(topology.get('provider', CUSTOM_ITEM))
  provider_counts: Counter[str] = Counter()
  device_counts: Counter[str] = Counter()
  module_counts: Counter[str] = Counter()
  custom_config_nodes = 0

  plugin_names = [str(p) for p in _sequence(topology.get('plugin'))]
  plugin_set = set(plugin_names)

  for node_value in nodes.values():
    node = _mapping(node_value)
    provider = provider_getter(node) if provider_getter else str(node.get('provider', primary_provider))
    provider_counts[provider] += 1
    device_counts[str(node.get('device', CUSTOM_ITEM))] += 1

    for module in _sequence(node.get('module')):
      module_counts[str(module)] += 1

    node_has_custom_config = False
    for config_name in _sequence(node.get('config')):
      if str(config_name) not in plugin_set:
        node_has_custom_config = True
    if node_has_custom_config:
      custom_config_nodes += 1

  # Include topology-level modules even if no node-level instance was observed.
  for module in _sequence(topology.get('module')):
    module_counts.setdefault(str(module), 0)

  if not provider_counts:
    provider_counts[primary_provider] = 0

  for item, count in provider_counts.items():
    update_metric(batch, 'provider', item, count, now=now)
  for item, count in device_counts.items():
    update_metric(batch, 'device', item, count, now=now)
  for item, count in module_counts.items():
    update_metric(batch, 'module', item, count, now=now)

  # Plugin "instances" mean topology uses. Device-level application details are
  # intentionally not uploaded because custom configuration names may be private.
  for plugin in set(plugin_names):
    update_metric(batch, 'plugin', plugin, 1, now=now)
  if custom_config_nodes:
    update_metric(batch, 'plugin', CUSTOM_ITEM, custom_config_nodes, now=now)


def collect_command(
      batch: Batch,
      command: str,
      now: datetime.datetime | None = None) -> None:
  # The usage command is excluded to avoid creating another pending observation
  # merely by inspecting or uploading the pending batch.
  if command == 'usage':
    return
  update_metric(batch, 'command', command, 1, now=now)


def payload_from_batch(
      batch: Mapping[str, typing.Any],
      netlab_version: str) -> dict[str, typing.Any] | None:
  if not batch_has_data(batch):
    return None

  records: list[dict[str, typing.Any]] = []
  metrics = typing.cast(Mapping[str, typing.Any], batch['metrics'])
  for dimension in sorted(metrics):
    items = metrics[dimension]
    if not isinstance(items, Mapping):
      continue
    for item in sorted(items):
      metric = items[item]
      if not isinstance(metric, Mapping):
        continue
      observations = metric.get('observations')
      instances = metric.get('instances')
      maximum = metric.get('maximum')
      if not all(isinstance(v, int) and not isinstance(v, bool) for v in [observations, instances, maximum]):
        continue
      records.append({
        'dimension': dimension,
        'item': item,
        'observations': observations,
        'instances': instances,
        'maximum': maximum
      })

  if not records:
    return None

  return {
    'schema': SCHEMA_VERSION,
    'batch_id': str(batch['batch_id']),
    'period_start': str(batch['period_start']),
    'period_end': str(batch['period_end']),
    'netlab_version': version_for_upload(netlab_version),
    'metrics': records
  }


def preview_upload(
      stats: MutableMapping[str, typing.Any],
      netlab_version: str) -> dict[str, typing.Any] | None:
  inflight = stats.get('_inflight')
  if batch_has_data(inflight):
    return payload_from_batch(typing.cast(Mapping[str, typing.Any], inflight), netlab_version)
  pending = ensure_batch(stats, '_pending')
  return payload_from_batch(pending, netlab_version)


def prepare_upload(
      stats: MutableMapping[str, typing.Any],
      netlab_version: str) -> dict[str, typing.Any] | None:
  """Rotate pending data into an immutable retryable in-flight batch."""
  inflight = stats.get('_inflight')
  if batch_has_data(inflight):
    return payload_from_batch(typing.cast(Mapping[str, typing.Any], inflight), netlab_version)

  pending = ensure_batch(stats, '_pending')
  if not batch_has_data(pending):
    return None

  stats['_inflight'] = pending
  stats['_pending'] = new_batch()
  return payload_from_batch(pending, netlab_version)


def acknowledge_upload(stats: MutableMapping[str, typing.Any], batch_id: str) -> bool:
  inflight = stats.get('_inflight')
  if not isinstance(inflight, Mapping) or inflight.get('batch_id') != batch_id:
    return False
  stats.pop('_inflight', None)
  return True
