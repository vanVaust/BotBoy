---
name: password-generator
version: 2.0.0
description: >
  Cryptographically secure password generator using Python secrets module (CSPRNG).
  Use whenever the user needs a password, secure token, passphrase, API key, or
  random string. Triggers on: password, passgen, generate-password, random-token,
  secure-string, or any request to create a strong password.
triggers:
  - password
  - passgen
  - passphrase
  - generate-password
security_level: BEGINNER
layer:
  id: botboy.skills.password-generator
  resources:
    compute: low
    memory: 1MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
    protection: csprng-only
  runtime:
    type: builtin
---

## Usage

    password 24
    passgen 32
    generate-password 16

## Implementation

```python
import secrets, string, math
length = max(8, min(int(payload.get("length", 24)), 128))
alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
pwd = "".join(secrets.choice(alphabet) for _ in range(length))
entropy = math.floor(length * math.log2(len(alphabet)))
_result = {"password": pwd, "length": length, "entropy_bits": entropy}
```
