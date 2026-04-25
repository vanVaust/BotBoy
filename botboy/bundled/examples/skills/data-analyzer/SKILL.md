---
name: data-analyzer
version: 2.0.0
description: >
  Statistical analysis of numeric datasets: mean, median, std deviation, variance,
  quartiles, IQR, skewness, and outlier detection (IQR method). Use whenever the
  user provides numbers for analysis, wants descriptive statistics, or needs to
  detect outliers. Triggers on: analyze, stats, data-analyze, descriptive-statistics.
triggers:
  - analyze
  - stats
  - data-analyze
security_level: BEGINNER
layer:
  id: botboy.skills.data-analyzer
  resources:
    compute: medium
    memory: 32MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
  runtime:
    type: builtin
---

## Usage

    analyze 1 2 3 4 5 6 7 8 9 10
    stats 15.2 18.7 14.1 22.3 19.8

## Implementation

```python
from statistics import mean, median, stdev
data = [float(x) for x in str(payload.get("values","")).split()]
if not data:
    _result = {"error": "No data"}
else:
    s = sorted(data); n = len(s)
    mn, md = mean(data), median(data)
    sd = stdev(data) if n > 1 else 0
    q1, q3 = median(s[:n//2]), median(s[(n+1)//2:])
    iqr = q3 - q1
    outliers = [x for x in data if x < q1-1.5*iqr or x > q3+1.5*iqr]
    _result = {"n": n, "mean": round(mn,4), "median": round(md,4),
               "std_dev": round(sd,4), "min": min(data), "max": max(data),
               "q1": round(q1,4), "q3": round(q3,4), "iqr": round(iqr,4),
               "outliers": outliers}
```
