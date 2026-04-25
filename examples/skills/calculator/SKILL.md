---
name: calculator
version: 2.0.0
description: >
  AST-safe mathematical expression evaluator with full trigonometry, constants, and
  arbitrary-precision arithmetic. Triggers whenever the user wants to calculate,
  compute, evaluate, or solve any mathematical expression including algebra, geometry,
  trigonometry (sin/cos/tan), logarithms, square roots, or powers.
triggers:
  - calculate
  - compute
  - math
  - calc
  - sqrt
security_level: BEGINNER
layer:
  id: botboy.builtin.calculator
  resources:
    compute: low
    memory: 1MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
  runtime:
    type: builtin
    entry: BuiltinSkills.calculator
---

## Usage

    calculate sqrt(144) * pi
    compute sin(pi/4) + cos(pi/3)
    calc (100 - 32) * 5/9

## Supported Operations

Arithmetic: +, -, *, /, //, %, **
Trigonometry: sin, cos, tan, asin, acos, atan
Exponential: exp, log, log2, log10, sqrt, pow
Rounding: floor, ceil, round, abs
Constants: pi, e, tau, inf
Aggregation: min, max, sum

## Implementation

```python
# AST-based evaluation via BuiltinSkills.calculator
# Zero eval() calls — full whitelist validation
_result = None
```
