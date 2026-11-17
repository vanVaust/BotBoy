---
name: fetch
version: 1.0.0
description: >
  Fetch an http(s) URL and return readable text (HTML to text with title and links, JSON
  pretty-printed) with SSRF protection: public-address-only resolution, IP pinning, per-hop
  redirect re-validation, size, time and decompression limits.
triggers: [fetch]
security_level: INTERMEDIATE
needs_approval: false
allow_network: true
allow_filesystem: false
remote_policy: approval
cacheable: false
capabilities: [network, web, read-only]
input_schema:
  type: object
  properties:
    url: {type: string, format: uri}
    max_chars: {type: integer}
    max_bytes: {type: integer}
  required: [url]
output_schema:
  type: object
  properties:
    url: {type: string}
    status: {type: integer}
    title: {type: string}
    text: {type: string}
    links: {type: array}
layer:
  id: botboy.native.fetch
  resources: {compute: low, memory: 16MB, storage: none, network: true}
  security:
    sandbox: inprocess
    permissions: [net.http.public]
    protection: scheme and port allowlist, all resolved IPs must be global, pinned connection, redirects re-validated, https downgrade refused, 1MB default and 5MB hard body cap, 15s deadline
  runtime:
    type: builtin
    entry: botboy.skills.native.fetch:run
    timeout: 20
---

# fetch

    fetch https://example.org/article
    fetch https://example.org --links --max-chars 4000

## Threat model

- SSRF: http/https only, ports 80/443/8080/8443, no credentials in URLs. Every
  address the name resolves to must be public (loopback, RFC1918, CGNAT,
  link-local/metadata 169.254.169.254, multicast, reserved and IPv4 embedded in
  IPv6 mapped/6to4/Teredo/NAT64 forms are blocked). The TCP connection goes to the
  vetted IP while TLS verifies the certificate for the host name, so DNS
  rebinding cannot redirect the request. Redirects (max 5) are re-validated and
  https to http downgrades refused. `skills.fetch_allow_private_networks` lets an
  operator reach a home lab; link-local, multicast and reserved stay blocked.
- Exfiltration: a fetched URL can carry data out. Remote callers therefore need a
  server-issued approval for every fetch; the local operator does not.
- Resource use: 1 MB default (5 MB hard) body cap applied after bounded
  decompression, connect timeout 5 s, overall deadline 15 s, text types only.
- Proxies from the environment are ignored on purpose: a proxy resolves names
  itself, which would defeat IP pinning.
