# The scripts in this directory call live services and are run by hand
# (python tests/live/<name>.py). pytest must never collect or import them.
collect_ignore_glob = ["*.py"]
