#!/bin/sh
# bin/tmux from cvcpkg (recipes/tmux): run the real tmux, libexec/tmux/tmux,
# with this prefix's terminfo database first in TERMINFO_DIRS.
#
# cvcpkg's ncurses ships share/terminfo, but the search path compiled into it
# is the directory it was built in, which does not exist where a bundle is
# unpacked, so ncurses finds share/terminfo only through TERMINFO_DIRS.  tmux
# reads terminfo for the terminal each client attaches from, and the panes it
# starts inherit the variable, so TERM=tmux-256color (this tmux's default
# terminal) resolves inside them too, even where the host's database lacks it.
# The host's own directories are still searched after it.
self=$(readlink -f -- "$0" 2>/dev/null) || self=$0
prefix=$(cd -- "$(dirname -- "$self")/.." && pwd -P) || exit 1
case ":${TERMINFO_DIRS-}:" in
    *":$prefix/share/terminfo:"*) ;;
    *) TERMINFO_DIRS="$prefix/share/terminfo${TERMINFO_DIRS:+:$TERMINFO_DIRS}" ;;
esac
export TERMINFO_DIRS
exec "$prefix/libexec/tmux/tmux" "$@"
