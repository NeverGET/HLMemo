# Lessons learned

- Never run the test suite against the development database: it truncates every table.
- A heredoc that starts `ssh` must use `ssh -n`, or it swallows the rest of the script.
