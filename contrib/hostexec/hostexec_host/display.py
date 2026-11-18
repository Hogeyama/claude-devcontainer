"""Showing a held request to a person, as web/app.js does in the browser.

Everything in a held request comes from the container and is hostile:
arguments, working directory, environment.

Escaping adapted from strait's web UI (nix-agent-sandbox/contrib/strait, MIT).
"""
import re
import unicodedata

PLAIN = re.compile(r'[A-Za-z0-9_@%+=:,./-]+')


def invisible(character):
    """Control and format characters, surrogates, and line and paragraph
    separators: what does not show as itself."""
    return unicodedata.category(character) in ('Cc', 'Cf', 'Cs') or character in '  '


def visible(value):
    """Text with control and format characters, and backslashes, escaped."""
    return ''.join(
        '\\\\' if c == '\\' else f'\\u{{{ord(c):x}}}' if invisible(c) else c
        for c in value)


def shell_word(arg):
    """One argument as a shell word, the way a person would type it. Nothing
    is special inside '...', so only an argument with a quote or an invisible
    character needs $'...', where every backslash starts an escape."""
    if PLAIN.fullmatch(arg):
        return arg
    if "'" not in arg and not any(invisible(c) for c in arg):
        return f"'{arg}'"
    out = []
    for c in arg:
        code = ord(c)
        if c in "\\'":
            out.append('\\' + c)
        elif c in '\n\t\r':
            out.append({'\n': '\\n', '\t': '\\t', '\r': '\\r'}[c])
        elif not invisible(c):
            out.append(c)
        elif code < 0x100:
            out.append(f'\\x{code:02x}')
        elif code < 0x10000:
            out.append(f'\\u{code:04x}')
        else:
            out.append(f'\\U{code:08x}')
    return "$'" + ''.join(out) + "'"


def command_line(argv):
    return ' '.join(shell_word(a) for a in argv)
