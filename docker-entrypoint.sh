#!/bin/sh
set -e

DATA="${DATA_DIR:-/app/data}"
mkdir -p "$DATA"

# A bind-mounted ./data is often created by Docker as root: hand it to the bot user,
# then drop root privileges for the bot itself.
if [ "$(id -u)" = "0" ]; then
    if [ "$(stat -c %u "$DATA")" != "$(id -u bot)" ]; then
        chown -R bot:bot "$DATA" || echo "warning: could not chown $DATA" >&2
    fi
    exec setpriv --reuid=bot --regid=bot --init-groups "$@"
fi

exec "$@"
