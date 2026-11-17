"""In-container wrapper: run a command, report peak child RSS (KiB) on stderr.

Needed because the pinned image has no /usr/bin/time. Runs standalone:
    python3 measure.py echo hello
"""
import resource
import subprocess
import sys

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: measure.py CMD [ARGS...]")
    completed = subprocess.run(sys.argv[1:])
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(f"AE_MAXRSS_KB {usage.ru_maxrss}", file=sys.stderr)
    sys.exit(completed.returncode)
