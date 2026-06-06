# Copyright (c) 2008 Agostino Russo
#
# Written by Agostino Russo <agostino.russo@gmail.com>
#
# This file is part of Wubi the Win32 Ubuntu Installer.
#
# Wubi is free software; you can redistribute it and/or modify
# it under 5the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation; either version 2.1 of
# the License, or (at your option) any later version.
#
# Wubi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import sys
import os
import hashlib
import subprocess
import shutil
import random
import ctypes
import logging

log = logging.getLogger("CommonBackendUtils")

def join_path(*args):
    if args and args[0] and args[0][-1] == ":":
        args = list(args)
        args[0] = args[0] + os.path.sep
    return os.path.abspath(os.path.join(*args))

def spawn_command(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, show_window=False):
    STARTF_USESHOWWINDOW = 1
    SW_HIDE = 0
    if show_window:
        startupinfo = None
    else:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = SW_HIDE
    # stdin, stdout, and stderr must not be None:
    # http://www.py2exe.org/index.cgi/Py2ExeSubprocessInteractions
    return subprocess.Popen(command, stderr=stderr, stdin=stdin,
                               stdout=stdout, startupinfo=startupinfo,
                               shell=False)

def decode_output(data):
    '''
    Decode bytes captured from a subprocess into text. Windows console tools
    emit their output in the OEM code page, so prefer that and fall back to a
    permissive decode rather than raising on stray bytes.
    '''
    if not isinstance(data, bytes):
        return data
    encodings = []
    try:
        encodings.append('cp%d' % ctypes.windll.kernel32.GetConsoleOutputCP())
    except Exception:
        pass
    encodings.append(sys.getfilesystemencoding() or 'utf-8')
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('latin-1', 'replace')

def run_command(command, show_window=False):
    '''
    return stdout on success or raise error
    '''
    process = spawn_command(command, show_window=show_window)
    process.stdin.close()
    output = decode_output(process.stdout.read())
    errormsg = decode_output(process.stderr.read())
    retval = process.wait()
    if retval == 0:
        return output
    else:
        raise Exception(
            "Error executing command\n>>command=%s\n>>retval=%s\n>>stderr=%s\n>>stdout=%s"
            % (" ".join(command), retval, output, errormsg))

def run_nonblocking_command(command, show_window=False):
    '''
    run command and return immediately
    '''
    process = spawn_command(command, show_window)
    return process.pid

_SHA512_CRYPT_B64 = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


def _sha512_b64_from_24bit(b2, b1, b0, n):
    w = (b2 << 16) | (b1 << 8) | b0
    out = ''
    for _ in range(n):
        out += _SHA512_CRYPT_B64[w & 0x3f]
        w >>= 6
    return out


def sha512_crypt(password, salt=None, rounds=5000):
    '''
    Pure-Python implementation of the glibc SHA-512 crypt ("$6$") scheme.

    The stdlib ``crypt`` module is Unix-only (and was removed entirely in
    Python 3.13), so it cannot be used in the Windows-frozen build. subiquity's
    autoinstall ``identity.password`` field expects a $6$ crypted password, so
    we compute it ourselves. Verified against Ulrich Drepper's published test
    vectors.
    '''
    if isinstance(password, str):
        password = password.encode('utf-8')
    if salt is None:
        salt_chars = './abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        salt = ''.join(random.choice(salt_chars) for _ in range(16)).encode('ascii')
    if isinstance(salt, str):
        salt = salt.encode('ascii')
    salt = salt[:16]
    plen = len(password)
    slen = len(salt)

    ctx = hashlib.sha512()
    ctx.update(password)
    ctx.update(salt)

    alt = hashlib.sha512()
    alt.update(password)
    alt.update(salt)
    alt.update(password)
    B = alt.digest()

    i = plen
    while i > 64:
        ctx.update(B)
        i -= 64
    ctx.update(B[:i])

    i = plen
    while i > 0:
        if i & 1:
            ctx.update(B)
        else:
            ctx.update(password)
        i >>= 1
    A = ctx.digest()

    dp = hashlib.sha512()
    for _ in range(plen):
        dp.update(password)
    DP = dp.digest()
    P = b''
    i = plen
    while i > 64:
        P += DP
        i -= 64
    P += DP[:i]

    ds = hashlib.sha512()
    for _ in range(16 + A[0]):
        ds.update(salt)
    DS = ds.digest()
    S = b''
    i = slen
    while i > 64:
        S += DS
        i -= 64
    S += DS[:i]

    C = A
    for r in range(rounds):
        c = hashlib.sha512()
        if r & 1:
            c.update(P)
        else:
            c.update(C)
        if r % 3:
            c.update(S)
        if r % 7:
            c.update(P)
        if r & 1:
            c.update(C)
        else:
            c.update(P)
        C = c.digest()

    order = [
        (0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4),
        (47, 5, 26), (6, 27, 48), (28, 49, 7), (50, 8, 29), (9, 30, 51),
        (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13), (56, 14, 35),
        (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19),
        (62, 20, 41),
    ]
    out = ''
    for b2, b1, b0 in order:
        out += _sha512_b64_from_24bit(C[b2], C[b1], C[b0], 4)
    out += _sha512_b64_from_24bit(0, 0, C[63], 2)

    salt_str = salt.decode('ascii')
    if rounds == 5000:
        return '$6$%s$%s' % (salt_str, out)
    return '$6$rounds=%d$%s$%s' % (rounds, salt_str, out)


def md5_password(password):
    # From http://mail.python.org/pipermail/python-list/2003-March/195202.html
    if isinstance(password, str):
        password = password.encode('utf-8')
    salt_chars = b'./abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    salt = bytes(random.choice(salt_chars) for i in range(5))

    hash = hashlib.md5()
    hash.update(password)
    hash.update(b'$1$')
    hash.update(salt)

    second_hash = hashlib.md5()
    second_hash.update(password)
    second_hash.update(salt)
    second_hash.update(password)
    second_hash = second_hash.digest()
    q, r = divmod(len(password), len(second_hash))
    second_hash = second_hash*q + second_hash[:r]
    assert len(second_hash) == len(password)
    hash.update(second_hash)
    del second_hash, q, r

    i = len(password)
    while i > 0:
        if i & 1:
            hash.update(b'\0')
        else:
            hash.update(password[0:1])
        i >>= 1

    hash = hash.digest()

    for i in range(1000):
        nth_hash = hashlib.md5()
        if i % 2:
            nth_hash.update(password)
        else:
            nth_hash.update(hash)
        if i % 3:
            nth_hash.update(salt)
        if i % 7:
            nth_hash.update(password)
        if i % 2:
            nth_hash.update(hash)
        else:
            nth_hash.update(password)
        hash = nth_hash.digest()

    # a different base64 than the MIME one
    base64 = './0123456789' \
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ' \
        'abcdefghijklmnopqrstuvwxyz'
    def b64_three_char(byte2, byte1, byte0, n):
        # In Python 3 indexing a bytes object yields ints directly.
        w = (byte2 << 16) | (byte1 << 8) | byte0
        s = []
        for _ in range(n):
            s.append(base64[w & 0x3f])
            w >>= 6
        return s

    result = ['$1$', salt.decode('ascii'), '$']
    result.extend(b64_three_char(hash[0], hash[6], hash[12], 4))
    result.extend(b64_three_char(hash[1], hash[7], hash[13], 4))
    result.extend(b64_three_char(hash[2], hash[8], hash[14], 4))
    result.extend(b64_three_char(hash[3], hash[9], hash[15], 4))
    result.extend(b64_three_char(hash[4], hash[10], hash[5], 4))
    result.extend(b64_three_char(0, 0, hash[11], 2))

    return ''.join(result)

def get_file_hash(file_path, hash_name='md5', associated_task=None):
    if not file_path or not os.path.isfile(file_path):
        return
    file_size = os.path.getsize(file_path)//(1024**2)
    if associated_task:
        associated_task.unit = "MB"
        associated_task.size = file_size
    file = open(file_path, "rb")
    hash = hashlib.new(hash_name)
    data_read = 0
    for i in range(file_size + 1):
        data = file.read(1024**2)
        data_read += 1
        if not data:
            break
        if associated_task:
            if associated_task.set_progress(data_read):
                file.close()
                return
        hash.update(data)
    file.close()
    hash = hash.hexdigest()
    if associated_task:
        associated_task.finish()
    return hash

def get_drive_space(drive_path):
    #Windows only
    freeuser = ctypes.c_int64()
    total = ctypes.c_int64()
    free = ctypes.c_int64()
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            str(drive_path),
            ctypes.byref(freeuser),
            ctypes.byref(total),
            ctypes.byref(free))
    return total.value

def copy_file(source, target, associated_task=None):
    '''
    Copy file with progress report
    '''
    if os.path.isfile(source):
        file_size = os.path.getsize(source)
    elif os.path.ismount(source):
        if sys.platform.startswith("win"):
            file_size = get_drive_space(source)
            source = "\\\\.\\%s" % source[:2]
    if associated_task:
        associated_task.size = file_size/1024**2
        associated_task.unit = "MB"
    source_file = open(source, "rb")
    target_file = open(target, "wb")
    data_read = 0
    while True:
        data = source_file.read(1024**2)
        data_read += 1
        if not data:
            break
        if associated_task:
            if associated_task.set_progress(data_read):
                source_file.close()
                target_file.close()
                return
        target_file.write(data)
        if data_read >= file_size:
            break
    source_file.close()
    target_file.close()
    if associated_task:
        associated_task.finish()

def reversed(list):
    list.reverse()
    return list

def read_file(file_path, binary=False):
    if not file_path or not os.path.isfile(file_path):
        return
    f = None
    if binary:
        f = open(file_path, 'rb')
    else:
        f = open(file_path, 'r')
    content = f.read()
    f.close()
    return content

def write_file(file_path, str):
    if not file_path:
        return
    f = None
    f = open(file_path, 'w')
    f.write(str)
    f.close()

def replace_line_in_file(file_path, old_line, new_line):
    if new_line[-1] != "\n":
        new_line += "\n"
    f = open(file_path, 'r')
    lines = f.readlines()
    f.close()
    f = open(file_path, 'w')
    for i,line in enumerate(lines):
        if line.startswith(old_line):
            lines[i] = new_line
    try:
        f.writelines(lines)
    except Exception as err:
        log.exception(err)
    f.close()

def remove_line_in_file(file_path, rm_line, ignore_case=False):
    f = open(file_path, 'r')
    lines = f.readlines()
    f.close()
    f = open(file_path, 'w')
    if ignore_case:
        rm_line = rm_line.lower()
    for i,line in enumerate(lines):
        if ignore_case:
            line = line.lower()
        if line.startswith(rm_line):
            lines[i] = ""
    f.writelines(lines)
    f.close()

def find_line_in_file(file_path, text, endswith=False):
    if not file_path or not os.path.isfile(file_path):
        return
    if endswith and text[-1] != "\n":
        text += "\n"
    f = open(file_path, 'r')
    lines = f.readlines()
    f.close()
    for line in lines:
        if (endswith and line.endswith(text)) \
        or (not endswith and line.startswith(text)):
            return line[:-1]

def unix_path(path):
    #TBD not a proper conversion but will do for now
    path = path.replace('\\', '/')
    if len(path)>1 and path[1] == ':':
        path = path[2:]
    if len(path)>1 and path[-1] == '/':
        path = path[:-1]
    return path

def rm_tree(target):
    if not os.path.exists(target):
        return
    if os.path.isfile(target):
            os.unlink(target)
    elif not os.path.isdir(target):
        return
    if sys.platform.startswith("win"):
        for dir, subdirs, files in os.walk(target):
            if not files:
                continue
            for file in files:
                file = join_path(dir, file)
                run_command(['attrib', '-R', '-S', '-H', file])
    shutil.rmtree(target)
