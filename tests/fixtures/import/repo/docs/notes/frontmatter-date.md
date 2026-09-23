---
date: 2026-03-05
tags: [startup]
---
# Startup order

The entry point `src/app/main.py` opens the pool before it binds the listener; a failed pool
start exits with code 69.
