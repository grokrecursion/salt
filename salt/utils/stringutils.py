# -*- coding: utf-8 -*-
'''
Functions for manipulating or otherwise processing strings
'''

# Import Python libs
from __future__ import absolute_import, print_function
import errno
import fnmatch
import logging
import os
import shlex
import re
import string
import time

# Import Salt libs
from salt.utils.decorators.jinja import jinja_filter

# Import 3rd-party libs
from salt.ext import six
from salt.ext.six.moves import range  # pylint: disable=redefined-builtin

log = logging.getLogger(__name__)


@jinja_filter('to_bytes')
def to_bytes(s, encoding=None):
    '''
    Given bytes, bytearray, str, or unicode (python 2), return bytes (str for
    python 2)
    '''
    if six.PY3:
        if isinstance(s, bytes):
            return s
        if isinstance(s, bytearray):
            return bytes(s)
        if isinstance(s, six.string_types):
            return s.encode(encoding or __salt_system_encoding__)
        raise TypeError('expected bytes, bytearray, or str')
    else:
        return to_str(s, encoding)


def to_str(s, encoding=None):
    '''
    Given str, bytes, bytearray, or unicode (py2), return str
    '''
    # This shouldn't be six.string_types because if we're on PY2 and we already
    # have a string, we should just return it.
    if isinstance(s, str):
        return s
    if six.PY3:
        if isinstance(s, (bytes, bytearray)):
            # https://docs.python.org/3/howto/unicode.html#the-unicode-type
            # replace error with U+FFFD, REPLACEMENT CHARACTER
            return s.decode(encoding or __salt_system_encoding__, "replace")
        raise TypeError('expected str, bytes, or bytearray not {}'.format(type(s)))
    else:
        if isinstance(s, bytearray):
            return str(s)
        if isinstance(s, unicode):  # pylint: disable=incompatible-py3-code,undefined-variable
            return s.encode(encoding or __salt_system_encoding__)
        raise TypeError('expected str, bytearray, or unicode')


def to_unicode(s, encoding=None):
    '''
    Given str or unicode, return unicode (str for python 3)
    '''
    if not isinstance(s, (bytes, bytearray, six.string_types)):
        return s
    if six.PY3:
        if isinstance(s, (bytes, bytearray)):
            return to_str(s, encoding)
    else:
        # This needs to be str and not six.string_types, since if the string is
        # already a unicode type, it does not need to be decoded (and doing so
        # will raise an exception).
        if isinstance(s, str):
            return s.decode(encoding or __salt_system_encoding__)
    return s


@jinja_filter('str_to_num')  # Remove this for Neon
@jinja_filter('to_num')
def to_num(text):
    '''
    Convert a string to a number.
    Returns an integer if the string represents an integer, a floating
    point number if the string is a real number, or the string unchanged
    otherwise.
    '''
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def to_none(text):
    '''
    Convert a string to None if the string is empty or contains only spaces.
    '''
    if str(text).strip():
        return text
    return None


def is_quoted(value):
    '''
    Return a single or double quote, if a string is wrapped in extra quotes.
    Otherwise return an empty string.
    '''
    ret = ''
    if isinstance(value, six.string_types) \
            and value[0] == value[-1] \
            and value.startswith(('\'', '"')):
        ret = value[0]
    return ret


def dequote(value):
    '''
    Remove extra quotes around a string.
    '''
    if is_quoted(value):
        return value[1:-1]
    return value


@jinja_filter('is_hex')
def is_hex(value):
    '''
    Returns True if value is a hexidecimal string, otherwise returns False
    '''
    try:
        int(value, 16)
        return True
    except (TypeError, ValueError):
        return False


def is_binary(data):
    '''
    Detects if the passed string of data is binary or text
    '''
    if '\0' in data:
        return True
    if not data:
        return False

    text_characters = ''.join([chr(x) for x in range(32, 127)] + list('\n\r\t\b'))
    # Get the non-text characters (map each character to itself then use the
    # 'remove' option to get rid of the text characters.)
    if six.PY3:
        trans = ''.maketrans('', '', text_characters)
        nontext = data.translate(trans)
    else:
        trans = string.maketrans('', '')  # pylint: disable=no-member
        nontext = data.translate(trans, text_characters)

    # If more than 30% non-text characters, then
    # this is considered binary data
    if float(len(nontext)) / len(data) > 0.30:
        return True
    return False


@jinja_filter('random_str')
def random(size=32):
    key = os.urandom(size)
    return key.encode('base64').replace('\n', '')[:size]


@jinja_filter('contains_whitespace')
def contains_whitespace(text):
    '''
    Returns True if there are any whitespace characters in the string
    '''
    return any(x.isspace() for x in text)


def human_to_bytes(size):
    '''
    Given a human-readable byte string (e.g. 2G, 30M),
    return the number of bytes.  Will return 0 if the argument has
    unexpected form.

    .. versionadded:: Oxygen
    '''
    sbytes = size[:-1]
    unit = size[-1]
    if sbytes.isdigit():
        sbytes = int(sbytes)
        if unit == 'P':
            sbytes *= 1125899906842624
        elif unit == 'T':
            sbytes *= 1099511627776
        elif unit == 'G':
            sbytes *= 1073741824
        elif unit == 'M':
            sbytes *= 1048576
        else:
            sbytes = 0
    else:
        sbytes = 0
    return sbytes


def build_whitespace_split_regex(text):
    '''
    Create a regular expression at runtime which should match ignoring the
    addition or deletion of white space or line breaks, unless between commas

    Example:

    .. code-block:: python

        >>> import re
        >>> import salt.utils.stringutils
        >>> regex = salt.utils.stringutils.build_whitespace_split_regex(
        ...     """if [ -z "$debian_chroot" ] && [ -r /etc/debian_chroot ]; then"""
        ... )

        >>> regex
        '(?:[\\s]+)?if(?:[\\s]+)?\\[(?:[\\s]+)?\\-z(?:[\\s]+)?\\"\\$debian'
        '\\_chroot\\"(?:[\\s]+)?\\](?:[\\s]+)?\\&\\&(?:[\\s]+)?\\[(?:[\\s]+)?'
        '\\-r(?:[\\s]+)?\\/etc\\/debian\\_chroot(?:[\\s]+)?\\]\\;(?:[\\s]+)?'
        'then(?:[\\s]+)?'
        >>> re.search(
        ...     regex,
        ...     """if [ -z "$debian_chroot" ] && [ -r /etc/debian_chroot ]; then"""
        ... )

        <_sre.SRE_Match object at 0xb70639c0>
        >>>

    '''
    def __build_parts(text):
        lexer = shlex.shlex(text)
        lexer.whitespace_split = True
        lexer.commenters = ''
        if '\'' in text:
            lexer.quotes = '"'
        elif '"' in text:
            lexer.quotes = '\''
        return list(lexer)

    regex = r''
    for line in text.splitlines():
        parts = [re.escape(s) for s in __build_parts(line)]
        regex += r'(?:[\s]+)?{0}(?:[\s]+)?'.format(r'(?:[\s]+)?'.join(parts))
    return r'(?m)^{0}$'.format(regex)


def expr_match(line, expr):
    '''
    Evaluate a line of text against an expression. First try a full-string
    match, next try globbing, and then try to match assuming expr is a regular
    expression. Originally designed to match minion IDs for
    whitelists/blacklists.
    '''
    if line == expr:
        return True
    if fnmatch.fnmatch(line, expr):
        return True
    try:
        if re.match(r'\A{0}\Z'.format(expr), line):
            return True
    except re.error:
        pass
    return False


@jinja_filter('check_whitelist_blacklist')
def check_whitelist_blacklist(value, whitelist=None, blacklist=None):
    '''
    Check a whitelist and/or blacklist to see if the value matches it.

    value
        The item to check the whitelist and/or blacklist against.

    whitelist
        The list of items that are white-listed. If ``value`` is found
        in the whitelist, then the function returns ``True``. Otherwise,
        it returns ``False``.

    blacklist
        The list of items that are black-listed. If ``value`` is found
        in the blacklist, then the function returns ``False``. Otherwise,
        it returns ``True``.

    If both a whitelist and a blacklist are provided, value membership
    in the blacklist will be examined first. If the value is not found
    in the blacklist, then the whitelist is checked. If the value isn't
    found in the whitelist, the function returns ``False``.
    '''
    if blacklist is not None:
        if not hasattr(blacklist, '__iter__'):
            blacklist = [blacklist]
        try:
            for expr in blacklist:
                if expr_match(value, expr):
                    return False
        except TypeError:
            log.error('Non-iterable blacklist %s', blacklist)

    if whitelist:
        if not hasattr(whitelist, '__iter__'):
            whitelist = [whitelist]
        try:
            for expr in whitelist:
                if expr_match(value, expr):
                    return True
        except TypeError:
            log.error('Non-iterable whitelist %s', whitelist)
    else:
        return True

    return False


def check_include_exclude(path_str, include_pat=None, exclude_pat=None):
    '''
    Check for glob or regexp patterns for include_pat and exclude_pat in the
    'path_str' string and return True/False conditions as follows.
      - Default: return 'True' if no include_pat or exclude_pat patterns are
        supplied
      - If only include_pat or exclude_pat is supplied: return 'True' if string
        passes the include_pat test or fails exclude_pat test respectively
      - If both include_pat and exclude_pat are supplied: return 'True' if
        include_pat matches AND exclude_pat does not match
    '''
    ret = True  # -- default true
    # Before pattern match, check if it is regexp (E@'') or glob(default)
    if include_pat:
        if re.match('E@', include_pat):
            retchk_include = True if re.search(
                include_pat[2:],
                path_str
            ) else False
        else:
            retchk_include = True if fnmatch.fnmatch(
                path_str,
                include_pat
            ) else False

    if exclude_pat:
        if re.match('E@', exclude_pat):
            retchk_exclude = False if re.search(
                exclude_pat[2:],
                path_str
            ) else True
        else:
            retchk_exclude = False if fnmatch.fnmatch(
                path_str,
                exclude_pat
            ) else True

    # Now apply include/exclude conditions
    if include_pat and not exclude_pat:
        ret = retchk_include
    elif exclude_pat and not include_pat:
        ret = retchk_exclude
    elif include_pat and exclude_pat:
        ret = retchk_include and retchk_exclude
    else:
        ret = True

    return ret


def print_cli(msg, retries=10, step=0.01):
    '''
    Wrapper around print() that suppresses tracebacks on broken pipes (i.e.
    when salt output is piped to less and less is stopped prematurely).
    '''
    while retries:
        try:
            try:
                print(msg)
            except UnicodeEncodeError:
                print(msg.encode('utf-8'))
        except IOError as exc:
            err = "{0}".format(exc)
            if exc.errno != errno.EPIPE:
                if (
                    ("temporarily unavailable" in err or
                     exc.errno in (errno.EAGAIN,)) and
                    retries
                ):
                    time.sleep(step)
                    retries -= 1
                    continue
                else:
                    raise
        break