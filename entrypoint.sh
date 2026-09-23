#!/bin/sh
set -eu

dockerd >/tmp/dockerd.log 2>&1 &
i=0
until docker info >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    cat /tmp/dockerd.log >&2
    exit 1
  fi
  sleep 1
done
exec "$@"
