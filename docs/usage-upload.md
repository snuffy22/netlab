# Anonymous usage statistics upload

Netlab continues to keep its existing lifetime usage statistics in
`~/.netlab/stats.json`. This change adds a separate aggregate batch intended
for optional, manual submission to the netlab project.

No upload occurs while installing, importing, or normally running netlab.
Submission happens only after the user runs:

```shell
netlab usage upload
```

Use `netlab usage upload --show` to display the exact JSON without changing
local state or making a network request.

## Data included

The payload has a finite vocabulary and contains only aggregate counters:

- coarse netlab major/minor version;
- command, provider, device, module, and plugin identifiers from a built-in
  allowlist;
- aggregate topology, node, and link counts;
- observations, total instances, and maximum instances per observation;
- a random, single-use batch identifier and UTC date range.

Unknown or user-defined provider, device, module, and plugin names are replaced
with `_custom`. Unknown command names are replaced with `_other`.

The payload does **not** contain the locally generated `_id`, topology names,
node names, filenames, paths, hostnames, usernames, IP/MAC addresses, AS
numbers, VLAN IDs, arbitrary topology attributes, exception text, or custom
configuration names.

## Reliability model

The pending aggregate is rotated into an immutable `_inflight` batch before a
submission attempt. New observations continue in a fresh `_pending` batch.
Failed uploads leave `_inflight` intact for retry. The server uses `batch_id`
for idempotency, and the client removes `_inflight` only after an accepted
response.

## Endpoint override for testing

The production endpoint is `https://usage.netlab.tools/v1/submissions`.
Developers can override it without adding a public CLI option:

```shell
NETLAB_USAGE_ENDPOINT=http://localhost:8787/v1/submissions \
  netlab usage upload --yes
```

Plain HTTP is accepted only for localhost.
