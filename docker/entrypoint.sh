#!/bin/sh
set -eu

# CLI login state and saved clock settings are private, persistent files.
umask 077
exec "$@"
