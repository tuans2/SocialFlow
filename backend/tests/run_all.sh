#!/usr/bin/env bash
# Run every test. From backend/:  bash tests/run_all.sh
set -u
cd "$(dirname "$0")/.." || exit 1
fail=0
for t in tests/test_*.py; do
    echo "── $t"
    if python3 "$t"; then :; else fail=1; fi
    echo
done
[ "$fail" -eq 0 ] && echo "ALL SUITES PASSED" || echo "SOME SUITES FAILED"
exit "$fail"
