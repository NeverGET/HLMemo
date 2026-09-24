---
name: Review style
description: code reviews list blocking findings first, nits last
metadata:
  type: feedback
---
List blocking findings first in a code review and put style nits in a separate, final section.

**Why:** a nit-first review buried a data-loss bug that shipped anyway.
**How to apply:** every review of the fixture project, including self-reviews before a merge.
