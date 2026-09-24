---
name: Deploy footguns
description: recurring deployment mistakes on the fixture stack and how to avoid them
metadata:
  type: feedback
---
Deployment footguns seen on the fixture stack:
- Never restart the database container while a migration runs: the half-applied schema needs a manual restore.
- Pass `-n` to every `ssh` call inside a deploy heredoc, otherwise it reads the rest of the script as its input.
- **Mistake:** the cache was warmed before the new release took traffic, so it served stale pages for an hour. **Fix:** warm the cache only after the health check of the new release passes.

**Why:** each of these cost at least one failed release.

Found later:
- Always pin the base image by digest; a floating tag pulled a different libc during a rebuild.
- Keep the maintenance page on a separate host, because the proxy goes down together with the app.
