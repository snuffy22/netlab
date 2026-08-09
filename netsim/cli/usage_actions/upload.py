# Optional manual upload of anonymized aggregate usage statistics
#
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlparse

import requests

from ... import __version__
from ...utils import log, stats, strings

DEFAULT_ENDPOINT = 'https://usage.netlab.tools/v1/submissions'


def upload_parser(parser: argparse.ArgumentParser) -> None:
  parser.add_argument(
    '--show',
    action='store_true',
    help='Display the exact pending payload without submitting it')
  parser.add_argument(
    '-y','--yes',
    dest='confirm',
    action='store_true',
    help='Submit without an interactive confirmation')
  parser.add_argument(
    '--endpoint',
    default=os.environ.get('NETLAB_USAGE_ENDPOINT',DEFAULT_ENDPOINT),
    help=argparse.SUPPRESS)
  parser.add_argument(
    '--timeout',
    type=float,
    default=10.0,
    help=argparse.SUPPRESS)


def _validate_endpoint(endpoint: str) -> None:
  parsed = urlparse(endpoint)
  is_local = parsed.hostname in ['localhost','127.0.0.1','::1']
  if parsed.scheme != 'https' and not (is_local and parsed.scheme == 'http'):
    log.fatal('Usage statistics can only be uploaded over HTTPS',module='usage')
  if not parsed.netloc or parsed.username or parsed.password:
    log.fatal('Invalid usage statistics endpoint',module='usage')


def _print_payload(payload: dict) -> None:
  print(json.dumps(payload,indent=2,sort_keys=True))


def upload(args: argparse.Namespace) -> None:
  if args.show:
    payload = stats.preview_upload_payload(__version__)
    if payload is None:
      log.info('There are no pending usage statistics to upload',module='usage')
      return
    _print_payload(payload)
    return

  # Rotate first so the payload shown to the user is exactly the payload sent.
  # New observations collected while the request is running go into a fresh
  # pending batch and cannot be lost when this batch is acknowledged.
  payload = stats.prepare_upload_payload(__version__)
  if payload is None:
    log.info('There are no pending usage statistics to upload',module='usage')
    return

  _print_payload(payload)
  if not args.confirm and not strings.confirm('\nSubmit this anonymous aggregate usage batch'):
    log.info('Usage statistics were not submitted',module='usage')
    return

  _validate_endpoint(args.endpoint)
  try:
    response = requests.post(
      args.endpoint,
      json=payload,
      headers={
        'Accept': 'application/json',
        'User-Agent': f'netlab/{__version__} usage-upload/1'
      },
      timeout=args.timeout)
  except requests.RequestException as ex:
    log.error(
      'Cannot submit usage statistics',
      category=log.FatalError,
      module='usage',
      more_hints=[str(ex),'The batch remains pending and can be retried'],
      exit_on_error=True)
    return

  if response.status_code not in [200,202]:
    detail = response.text[:500].strip()
    hints = [f'HTTP status: {response.status_code}','The batch remains pending and can be retried']
    if detail:
      hints.append(detail)
    log.error(
      'The usage statistics service rejected the batch',
      category=log.FatalError,
      module='usage',
      more_hints=hints,
      exit_on_error=True)
    return

  if not stats.acknowledge_upload(payload['batch_id']):
    log.warning(
      text='The server accepted the batch, but local acknowledgement failed',
      module='usage',
      more_hints='The idempotent batch identifier prevents double counting on retry')
    return

  log.info('Anonymous aggregate usage statistics submitted',module='usage')
