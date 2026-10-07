#!/usr/bin/env python3
"""Hold the CX-15 serial port open. Run through uv; never infer power state."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
DEFAULT_SOCKET = ROOT / 'tmp/hardware/projector-serial.sock'
# Fixed, documented frames from micro_projector_serial.py; no derived checksum.
FRAMES = {
    'toggle': bytes.fromhex('ff 07 99 00 00 00 00 a0'),
    'flip-both': bytes.fromhex('ff 07 37 00 00 00 00 3e'),
    'flip-v': bytes.fromhex('ff 07 37 01 00 00 00 3f'),
    'flip-h': bytes.fromhex('ff 07 37 02 00 00 00 40'),
    'flip-none': bytes.fromhex('ff 07 37 03 00 00 00 41'),
}


def write_all(fd, payload):
    offset = 0
    deadline = time.monotonic() + 3
    while offset < len(payload):
        if time.monotonic() >= deadline:
            raise TimeoutError(f'Serial write incomplete: {offset}/{len(payload)}; do not blindly retry a toggle')
        try:
            written = os.write(fd, payload[offset:])
        except BlockingIOError:
            select.select([], [fd], [], 0.1)
            continue
        if written <= 0:
            raise OSError(f'Serial write stopped at {offset}/{len(payload)}')
        offset += written
    return offset


def configure_serial(fd):
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[4] = attrs[5] = termios.B9600
    attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    # HUPCL and all flow control are disabled by the cflag/iflag above.


def new_state(port):
    return {'port': str(port), 'pid': os.getpid(), 'baud': 9600, 'serial_held': True,
            'device_state': 'unknown', 'ack_supported': False,
            'received_bytes': 0, 'received_tail_hex': '', 'last_write': None}


def handle_request(request, fd, state):
    action = request.get('action')
    if action == 'status':
        return dict(state), False
    if action == 'send':
        command = request.get('command')
        if not isinstance(command, str) or command not in FRAMES:
            raise ValueError('Command must be one of: ' + ', '.join(FRAMES))
        written = write_all(fd, FRAMES[command])
        termios.tcdrain(fd)
        result = {'command': command, 'written': written,
                  'payload_hex': FRAMES[command].hex(' '), 'device_state': 'unknown',
                  'ack_supported': False, 'time': time.strftime('%Y-%m-%d %H:%M:%S')}
        state['last_write'] = result
        return result, False
    if action == 'stop':
        if request.get('confirm_standby') is not True:
            raise ValueError('Observe standby first, then use stop --confirm-standby')
        # Operator has observed standby. Keep the physical descriptor open for
        # another 15 seconds before returning to the server cleanup path.
        time.sleep(15)
        return {'standby_confirmed_by': 'operator', 'additional_hold_seconds': 15}, True
    raise ValueError('Unknown action')


def dispatch(request, fd, state):
    try:
        result, stop = handle_request(request, fd, state)
        return {'ok': True, 'result': result}, stop
    except (OSError, ValueError, termios.error) as error:
        return {'ok': False, 'error': str(error), 'device_state': 'unknown'}, False


def read_message(connection):
    raw = bytearray()
    while b'\n' not in raw:
        chunk = connection.recv(1024)
        if not chunk:
            raise ValueError('Incomplete request')
        raw.extend(chunk)
        if len(raw) > 4096:
            raise ValueError('Request too large')
    value = json.loads(bytes(raw).split(b'\n', 1)[0])
    if not isinstance(value, dict):
        raise ValueError('Request must be an object')
    return value


def serve(port, socket_path):
    if len(os.fsencode(socket_path)) > 103:
        raise ValueError('Unix socket path is too long; use a shorter path inside the project tmp directory')
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    # One holder per project; never unlink a socket belonging to a live session.
    lock_fd = os.open(str(socket_path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fd = None
    socket_owned = False
    old_handlers = {}
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if socket_path.exists():
            if not socket_path.is_socket():
                raise ValueError('Refusing to replace a non-socket control path')
            socket_path.unlink()  # lock proves the old project session ended
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        if hasattr(termios, 'TIOCEXCL'):
            fcntl.ioctl(fd, termios.TIOCEXCL)
        configure_serial(fd)
        state = new_state(port)
        def keep_open(signum, _frame):
            try:
                print(f'Signal {signum}: port remains open. Observe standby, then use stop --confirm-standby.', flush=True)
            except OSError:
                pass  # An SSH disconnect must not close the physical port.
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[signum] = signal.signal(signum, keep_open)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(socket_path))
            socket_owned = True
            socket_path.chmod(0o600)
            server.listen(4)
            print(json.dumps({'socket': str(socket_path), **state}), flush=True)
            stop = False
            while not stop:
                ready, _, _ = select.select([server, fd], [], [])
                if fd in ready:
                    chunk = os.read(fd, 256)
                    if not chunk:
                        raise OSError('Serial device disconnected; physical projector state is unknown')
                    state['received_bytes'] += len(chunk)
                    tail = bytes.fromhex(state['received_tail_hex']) + chunk
                    state['received_tail_hex'] = tail[-64:].hex(' ')
                if server in ready:
                    connection, _ = server.accept()
                    with connection:
                        connection.settimeout(3)
                        try:
                            reply, stop = dispatch(read_message(connection), fd, state)
                        except (OSError, ValueError, termios.error) as error:
                            reply = {'ok': False, 'error': str(error), 'device_state': 'unknown'}
                        # A disconnected client must not tear down the holder.
                        try:
                            connection.sendall(json.dumps(reply).encode() + b'\n')
                        except OSError:
                            pass
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        if fd is not None:
            os.close(fd)
        if socket_owned:
            socket_path.unlink(missing_ok=True)
        os.close(lock_fd)


def client(socket_path, request):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(25)
        connection.connect(str(socket_path))
        connection.sendall(json.dumps(request).encode() + b'\n')
        result = read_message(connection)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok') else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, default=DEFAULT_SOCKET)
    commands = parser.add_subparsers(dest='action', required=True)
    holder = commands.add_parser('serve', help='Hold serial open; sends no startup commands')
    holder.add_argument('--port', default=DEFAULT_PORT)
    commands.add_parser('status', help='Host session status; does not query projector power')
    sender = commands.add_parser('send')
    sender.add_argument('command', choices=FRAMES)
    stopper = commands.add_parser('stop')
    stopper.add_argument('--confirm-standby', action='store_true', required=True)
    args = parser.parse_args(argv)
    try:
        project_tmp = (ROOT / 'tmp').resolve()
        socket_path = args.socket.resolve()
        if project_tmp not in socket_path.parents:
            raise ValueError('--socket must be inside this project tmp directory')
        if args.action == 'serve':
            serve(args.port, socket_path)
            return 0
        return client(socket_path, {'action': args.action,
                       'command': getattr(args, 'command', None),
                       'confirm_standby': getattr(args, 'confirm_standby', False)})
    except (OSError, ValueError, termios.error) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
