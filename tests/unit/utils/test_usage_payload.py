import datetime
import json
import unittest

from netsim.utils import usage_payload


NOW = datetime.datetime(2026, 8, 6, 2, 30, tzinfo=datetime.timezone.utc)


class UsagePayloadTests(unittest.TestCase):
  def test_topology_collection_uses_finite_vocabulary(self) -> None:
    topology = {
      'provider': 'clab',
      'module': ['bgp', 'ospf', 'private-module'],
      'plugin': ['files', 'private-plugin'],
      'nodes': {
        'r1': {
          'device': 'eos',
          'provider': 'clab',
          'module': ['bgp', 'ospf'],
          'config': ['files', 'private-template']
        },
        'r2': {
          'device': 'mycorp-router',
          'provider': 'private-provider',
          'module': ['bgp', 'private-module'],
          'config': []
        }
      },
      'links': [{}, {}]
    }

    batch = usage_payload.new_batch(NOW)
    usage_payload.collect_topology(batch, topology, now=NOW)
    payload = usage_payload.payload_from_batch(batch, '26.07.1.dev3')
    self.assertIsNotNone(payload)
    assert payload is not None

    encoded = json.dumps(payload, sort_keys=True)
    for private_value in [
      'mycorp-router', 'private-provider', 'private-module',
      'private-plugin', 'private-template', 'r1', 'r2'
    ]:
      self.assertNotIn(private_value, encoded)

    metrics = {
      (m['dimension'], m['item']): m
      for m in payload['metrics']
    }
    self.assertEqual(metrics[('node', 'all')]['instances'], 2)
    self.assertEqual(metrics[('link', 'all')]['instances'], 2)
    self.assertEqual(metrics[('device', 'eos')]['instances'], 1)
    self.assertEqual(metrics[('device', '_custom')]['instances'], 1)
    self.assertEqual(metrics[('provider', 'clab')]['instances'], 1)
    self.assertEqual(metrics[('provider', '_custom')]['instances'], 1)
    self.assertEqual(metrics[('module', 'bgp')]['instances'], 2)
    self.assertEqual(metrics[('module', '_custom')]['instances'], 1)
    self.assertEqual(metrics[('plugin', 'files')]['instances'], 1)
    self.assertEqual(metrics[('plugin', '_custom')]['instances'], 2)
    self.assertEqual(payload['netlab_version'], '26.07')

  def test_prepare_is_retryable_and_ack_preserves_new_pending_data(self) -> None:
    stats = {}
    pending = usage_payload.ensure_batch(stats)
    usage_payload.collect_command(pending, 'create', now=NOW)

    first = usage_payload.prepare_upload(stats, '26.07')
    self.assertIsNotNone(first)
    assert first is not None
    first_batch_id = first['batch_id']

    retry = usage_payload.prepare_upload(stats, '26.07')
    self.assertEqual(retry, first)

    new_pending = usage_payload.ensure_batch(stats)
    usage_payload.collect_command(new_pending, 'up', now=NOW)
    self.assertTrue(usage_payload.acknowledge_upload(stats, first_batch_id))
    self.assertNotIn('_inflight', stats)

    next_payload = usage_payload.prepare_upload(stats, '26.07')
    self.assertIsNotNone(next_payload)
    assert next_payload is not None
    self.assertNotEqual(next_payload['batch_id'], first_batch_id)
    self.assertEqual(next_payload['metrics'][0]['item'], 'up')

  def test_preview_does_not_rotate_pending_batch(self) -> None:
    stats = {}
    batch = usage_payload.ensure_batch(stats)
    batch_id = batch['batch_id']
    usage_payload.collect_command(batch, 'validate', now=NOW)

    preview = usage_payload.preview_upload(stats, '26.07')
    self.assertIsNotNone(preview)
    self.assertNotIn('_inflight', stats)
    self.assertEqual(stats['_pending']['batch_id'], batch_id)

  def test_usage_command_is_not_collected(self) -> None:
    batch = usage_payload.new_batch(NOW)
    usage_payload.collect_command(batch, 'usage', now=NOW)
    self.assertFalse(usage_payload.batch_has_data(batch))

  def test_unknown_command_becomes_other(self) -> None:
    batch = usage_payload.new_batch(NOW)
    usage_payload.collect_command(batch, 'private-command', now=NOW)
    payload = usage_payload.payload_from_batch(batch, '26.07')
    assert payload is not None
    self.assertEqual(payload['metrics'][0]['item'], '_other')

  def test_invalid_metric_is_rejected(self) -> None:
    batch = usage_payload.new_batch(NOW)
    with self.assertRaises(ValueError):
      usage_payload.update_metric(batch, 'node', 'all', -1)
    with self.assertRaises(ValueError):
      usage_payload.update_metric(batch, 'node', 'all', True)


if __name__ == '__main__':
  unittest.main()
