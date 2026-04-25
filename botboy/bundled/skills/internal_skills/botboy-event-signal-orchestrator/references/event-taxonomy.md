# Event Taxonomy

Classify BotBoy events into these buckets:

1. command events
   input arrived, route selected, command completed
2. approval events
   request denied, granted, escalated, deferred
3. auth and identity events
   login, refresh, principal change, key issuance
4. runtime events
   sandbox selected, timeout hit, isolation unavailable
5. quality events
   eval failed, replay drift detected, smoke failed
6. observability events
   trace created, latency spike, monitoring threshold crossed

Use the smallest sufficient bucket and keep naming stable.
