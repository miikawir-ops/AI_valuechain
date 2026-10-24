#!/usr/bin/env bash
# Runs every test script; exits non-zero if any fails. No pytest here — each
# test_*.py is a plain script. Usage: bash run_tests.sh [python]
PY="${1:-python}"
export PYTHONIOENCODING=utf-8
failed=0
for t in test_*.py; do
    # test_gemini.py is a live API-key check, not a unit test.
    [ "$t" = "test_gemini.py" ] && continue
    if out=$("$PY" "$t" 2>&1); then
        echo "ok    $t"
    else
        echo "FAIL  $t"; echo "$out" | tail -8 | sed 's/^/      /'
        failed=1
    fi
done
exit $failed
