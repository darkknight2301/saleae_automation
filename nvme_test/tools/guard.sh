#!/bin/sh
# guard.sh <yes|no> COMMAND...   - runs COMMAND only when the first argument is `yes`.
# The framework has no skip/branch statement (every RUN always executes), so destructive commands are wrapped with this;
# `allow_destructive` in the variables file feeds it. When refused: message on stderr, exit 77 (the test then FAILs visibly).
g=$1; shift
if [ "$g" != yes ]; then echo "REFUSED (destructive disabled - set allow_destructive=yes in the variables file): $*" >&2; exit 77; fi
exec "$@"
