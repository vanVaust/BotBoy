---
name: text-stats
version: 2.0.0
description: >
  Text analysis and readability statistics: word count, sentence count, Flesch
  reading ease, average word length, and keyword extraction. Use whenever the
  user wants to analyse text complexity, count words, check readability, extract
  keywords, or get statistics about a body of text. Triggers on: text-stats,
  readability, wordcount, analyse-text.
triggers:
  - text-stats
  - readability
  - wordcount
  - analyse-text
security_level: BEGINNER
layer:
  id: botboy.skills.text-stats
  resources:
    compute: low
    memory: 8MB
    network: false
  security:
    sandbox: inprocess
    permissions: []
  runtime:
    type: builtin
---

## Usage

    text-stats "Four score and seven years ago..."
    readability "Your text content here"

## Metrics

Word count, unique words, sentence count, Flesch score (0-100),
average word length, reading time estimate, top 10 keywords.

## Implementation

```python
import re
from collections import Counter
text = payload.get("text", "")
words = re.findall(r"\b\w+\b", text.lower())
sents = [s for s in re.split(r"[.!?]+", text) if s.strip()]
n_w, n_s = len(words), max(len(sents), 1)
avg_wl = sum(len(w) for w in words) / max(n_w, 1)
syllables = sum(max(1, len(re.findall(r"[aeiou]", w))) for w in words)
flesch = 206.835 - 1.015*(n_w/n_s) - 84.6*(syllables/max(n_w,1))
STOP = {"the","a","an","in","on","at","to","of","and","or","but","is","are","was"}
kw = {w: c for w, c in Counter(words).most_common(20) if w not in STOP and len(w) > 2}
_result = {"words": n_w, "sentences": n_s, "flesch_score": round(max(0,min(100,flesch)),1),
           "avg_word_length": round(avg_wl,2), "reading_time_min": round(n_w/200,1),
           "top_keywords": dict(list(kw.items())[:10])}
```
