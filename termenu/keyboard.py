import os
import sys
import fcntl
import termios
import select
import errno
import string
from contextlib import contextmanager

try:
    STDIN = sys.stdin.fileno()
except ValueError:
    STDIN = None

try:
    STDOUT = sys.stdin.fileno()
except ValueError:
    STDOUT = None


ANSI_SEQUENCES = dict(
    up='\x1b[A',
    down='\x1b[B',
    right='\x1b[C',
    left='\x1b[D',
    home='\x1bOH',
    end='\x1bOF',
    insert='\x1b[2~',
    delete='\x1b[3~',
    pageUp='\x1b[5~',
    pageDown='\x1b[6~',
    ctrlLeft='\x1b[1;5C',
    ctrlRight='\x1b[1;5D',
    ctrlUp='\x1b[1;5A',
    ctrlDown='\x1b[1;5B',
    ctrlSlash='\x1f',

    F1='\x1bOP',
    F2='\x1bOQ',
    F3='\x1bOR',
    F4='\x1bOS',
    F5='\x1b[15~',
    F6='\x1b[17~',
    F7='\x1b[18~',
    F8='\x1b[19~',
    F9='\x1b[20~',
    F10='\x1b[21~',
    F11='\x1b[23~',
    F12='\x1b[24~',

    ctrlF2='\x1bO1;5Q',
    ctrlF3='\x1bO1;5R',
    ctrlF4='\x1bO1;5S',
    ctrlF5='\x1b[15;5~',
    ctrlF6='\x1b[17;5~',
    ctrlF7='\x1b[18;5~',
    ctrlF8='\x1b[19;5~',
    ctrlF9='\x1b[20;5~',
    ctrlF10='\x1b[21;5~',
    ctrlF11='\x1b[23;5~',
    ctrlF12='\x1b[24;5~',

    shiftF1='\x1bO1;2P',
    shiftF2='\x1bO1;2Q',
    shiftF3='\x1bO1;2R',
    shiftF4='\x1bO1;2S',
    shiftF5='\x1b[15;2~',
    shiftF6='\x1b[17;2~',
    shiftF7='\x1b[18;2~',
    shiftF8='\x1b[19;2~',
    shiftF9='\x1b[20;2~',
    shiftF11='\x1b[23;2~',
    shiftF12='\x1b[24;2~',
)


try:
    for line in open(os.path.expanduser("~/.termenu/ansi_mapping")):
        if not line or line.startswith("#"):
            continue
        name, sep, sequence = line.replace(" ", "").replace("\t", "").strip().partition(":")
        if not sep:
            continue
        if not sequence:
            continue
        ANSI_SEQUENCES[name] = sequence = eval("'%s'" % sequence)
except FileNotFoundError:
    pass


for c in string.ascii_lowercase:
    ANSI_SEQUENCES['ctrl_%s' % c] = chr(ord(c) - ord('a')+1)

KEY_NAMES = {v:k for k,v in list(ANSI_SEQUENCES.items())}
KEY_NAMES.update({
    '\x1b' : 'esc',
    '\n' : 'enter',
    ' ' : 'space',
    '\x7f' : 'backspace',
})


class RawTerminal:
    def __init__(self, blocking=True):
        self._blocking = blocking
        self._opened = 0

    def open(self):
        self._opened += 1
        if self._opened > 1:
            return

        # Set raw mode
        self._oldterm = termios.tcgetattr(STDIN)
        newattr = termios.tcgetattr(STDIN)
        newattr[3] = newattr[3] & ~termios.ICANON & ~termios.ECHO
        termios.tcsetattr(STDIN, termios.TCSANOW, newattr)

        # Set non-blocking IO on stdin
        self._old_in = fcntl.fcntl(STDIN, fcntl.F_GETFL)
        self._old_out = fcntl.fcntl(STDOUT, fcntl.F_GETFL)

        if not self._blocking:
            fcntl.fcntl(STDIN, fcntl.F_SETFL, self._old_in | os.O_NONBLOCK)
            fcntl.fcntl(STDOUT, fcntl.F_SETFL, self._old_out | os.O_NONBLOCK)

    def close(self):
        self._opened -= 1
        if self._opened > 0:
            return
        # Restore previous terminal mode
        termios.tcsetattr(STDIN, termios.TCSAFLUSH, self._oldterm)
        fcntl.fcntl(STDIN, fcntl.F_SETFL, self._old_in)
        fcntl.fcntl(STDOUT, fcntl.F_SETFL, self._old_out)

    def get(self):
        ret = sys.stdin.read(1)
        if not ret:
            raise EOFError()
        return ret

    def wait(self):
        select.select([STDIN], [], [])

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def closed(self):
        self.close()
        try:
            yield
        finally:
            self.open()

    def listen(self, **kw):
        return keyboard_listener(terminal=self, **kw)


def keyboard_listener(heartbeat=None, terminal=None):
    if not terminal:
        terminal = RawTerminal(blocking=False)
    with terminal:
        # return keys
        sequence = ""
        while True:
            # wait for keys to become available
            ret, _, __ = select.select([STDIN], [], [], heartbeat)
            if not ret:
                yield "heartbeat"
                continue

            # read all available keys
            while True:
                try:
                    sequence += terminal.get()
                except EOFError:
                    break
                except OSError as e:
                    if e.errno == errno.EAGAIN:
                        break

            # handle ANSI key sequences
            while sequence:
                for seq in list(ANSI_SEQUENCES.values()):
                    if sequence[:len(seq)] == seq:
                        yield KEY_NAMES[seq]
                        sequence = sequence[len(seq):]
                        break
                # handle normal keys
                else:
                    for key in sequence:
                        yield KEY_NAMES.get(key, key)
                    sequence = ""


if __name__ == "__main__":
    for key in keyboard_listener(0.5):
        print(repr(key))
