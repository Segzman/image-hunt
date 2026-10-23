"""Start Image Hunt if it isn't running, then open it in the default browser.
Used by the app icon, the imagehunt:// bookmark link and shortcuts on macOS, Linux and Windows.
Run with the venv python:  python launch.py [imagehunt://open | imagehunt://open?s=SAVED_SLUG]"""
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('IMAGE_HUNT_PORT') or 8666)
BASE = f'http://127.0.0.1:{PORT}/'


def log_path():
    if sys.platform == 'darwin':
        d = os.path.expanduser('~/Library/Logs')
    elif os.name == 'nt':
        d = os.path.join(os.environ.get('LOCALAPPDATA') or ROOT, 'Image Hunt')
    else:
        d = os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'image-hunt')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'image-hunt.log')


def up():
    try:
        with urllib.request.urlopen(BASE + 'api/models', timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def target(arg):
    # imagehunt://open?s=slug -> http://127.0.0.1:8666/?s=slug ; anything else -> home
    q = urllib.parse.urlparse(arg or '').query
    s = urllib.parse.parse_qs(q).get('s', [''])[0]
    return BASE + ('?s=' + urllib.parse.quote(s) if s else '')


def env():
    # Icons/links start with a bare GUI PATH: add where ag, opencode and claude usually live.
    e = dict(os.environ, AG_AUTO_UPDATE='0', IMAGE_HUNT_PORT=str(PORT), PYTHONUNBUFFERED='1')
    if os.name != 'nt':
        home = os.path.expanduser('~')
        extra = [f'{home}/.local/bin', '/opt/homebrew/bin', '/usr/local/bin', f'{home}/.opencode/bin', f'{home}/bin']
        e['PATH'] = os.pathsep.join(extra + [e.get('PATH') or '/usr/bin:/bin'])
    return e


def start():
    try:
        import PIL  # noqa: F401  (installer puts it in the venv; repair if someone deleted it)
    except ImportError:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'pillow'], check=False)
    py = sys.executable
    kw = {}
    if os.name == 'nt':
        w = os.path.join(os.path.dirname(py), 'pythonw.exe')
        py = w if os.path.exists(w) else py
        kw['creationflags'] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    with open(log_path(), 'a') as log:
        subprocess.Popen([py, os.path.join(ROOT, 'server.py')], cwd=ROOT, env=env(),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **kw)
    for _ in range(40):
        if up():
            return True
        time.sleep(0.25)
    return False


def main():
    if not up() and not start():
        msg = f'Image Hunt did not start. Log: {log_path()}'
        print(msg, file=sys.stderr)
        if sys.platform == 'darwin':
            subprocess.run(['osascript', '-e', f'display alert "Image Hunt" message {msg!r}'], check=False)
        return 1
    webbrowser.open(target(sys.argv[1] if len(sys.argv) > 1 else ''))
    return 0


if __name__ == '__main__':
    assert target('imagehunt://open?s=skull') == BASE + '?s=skull' and target('') == BASE
    sys.exit(main())
