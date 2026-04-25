# Policy Matrix

Use this matrix as the default reasoning frame:

- Trusted + low risk + no network/filesystem: prefer the narrowest safe runtime.
- Untrusted code: never assume in-process execution is acceptable.
- Network access: expect approval or stronger containment.
- Filesystem access: expect approval unless the request is explicitly trusted and local.
- Missing isolation support: fail closed or return a clear policy error.

Translate the runtime question into four inputs:

1. `security_level`
2. `trusted`
3. `allow_network`
4. `allow_filesystem`

Then verify:

1. the chosen runtime tier
2. whether approval is required
3. whether missing infrastructure changes the outcome
4. whether the user-visible error is explicit and safe
