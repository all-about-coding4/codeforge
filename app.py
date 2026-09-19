#!/usr/bin/env python3
"""
CodeForge Pro — Multi-User Terminal + Files
• Proper controlling-terminal pty (Ctrl+C actually stops foreground jobs)
• Multiple terminals with tab switching
• Robust restart (walks /proc to kill session members only)
• Multi-user, prompt shows current folder, real-time file collaboration
"""

import os
import sys
import json
import shutil
import asyncio
import subprocess
import zipfile
import io
import tempfile
import platform
import struct
import fcntl
import termios
import pty
import base64
import re
import time
import uuid
import signal
from pathlib import Path
from lynkio import Lynk, send_file, abort, Connection, Request, json_response

# ============================================================
# CONFIG
# ============================================================
WORKSPACE = os.path.realpath(os.path.abspath("workspace"))
os.makedirs(WORKSPACE, exist_ok=True)

MAX_BODY_SIZE = 500 * 1024 * 1024
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"
DEFAULT_SHELL = os.environ.get("SHELL") or ("cmd.exe" if IS_WINDOWS else "/bin/bash")

# ============================================================
# ENV DETECTION
# ============================================================
def can_symlink(path):
    try:
        test_dir = os.path.join(path, ".symtest")
        os.makedirs(test_dir, exist_ok=True)
        src = os.path.join(test_dir, "src"); dst = os.path.join(test_dir, "dst")
        with open(src, "w") as f: f.write("x")
        os.symlink(src, dst)
        os.unlink(dst); os.unlink(src); os.rmdir(test_dir)
        return True
    except Exception:
        return False

def setup_environment():
    if sys.prefix != sys.base_prefix:
        print(f"✅ Using existing virtualenv: {sys.prefix}")
        return sys.executable, os.path.dirname(sys.executable), sys.prefix, True
    home = os.environ.get("HOME") or os.path.expanduser("~")
    for venv_root in [os.path.join(home, ".codeforge-venv"), os.path.join(WORKSPACE, ".venv")]:
        if not can_symlink(os.path.dirname(venv_root)):
            continue
        if os.path.exists(venv_root):
            py = os.path.join(venv_root, "Scripts" if IS_WINDOWS else "bin", "python")
            if os.path.exists(py):
                print(f"✅ Reusing existing venv: {venv_root}")
                return py, os.path.dirname(py), venv_root, True
        try:
            print(f"⚙️  Creating virtualenv at {venv_root} ...")
            subprocess.run([sys.executable, "-m", "venv", venv_root], check=True, capture_output=True, timeout=120)
            py = os.path.join(venv_root, "Scripts" if IS_WINDOWS else "bin", "python")
            pip = os.path.join(venv_root, "Scripts" if IS_WINDOWS else "bin", "pip")
            subprocess.run([pip, "install", "--upgrade", "pip", "--quiet"], check=False, capture_output=True, timeout=120)
            print(f"✅ Virtualenv ready: {venv_root}")
            return py, os.path.dirname(py), venv_root, True
        except Exception as e:
            print(f"⚠️  Could not create venv at {venv_root}: {e}")
    print("⚠️  Falling back to system Python")
    return sys.executable, os.path.dirname(sys.executable), None, False

PYTHON_PATH, VENV_BIN, VENV_ROOT, IN_VENV = setup_environment()

TERM_ENV = os.environ.copy()
TERM_ENV["TERM"] = "xterm-256color"
TERM_ENV["PYTHONUNBUFFERED"] = "1"
TERM_ENV["HOME"] = os.environ.get("HOME") or WORKSPACE
TERM_ENV["LANG"] = "en_US.UTF-8"
TERM_ENV["LC_ALL"] = "en_US.UTF-8"
TERM_ENV["PS1"] = r"\[\e[1;32m\]user\[\e[0m\]\[\e[0;33m\]$\[\e[0m\]\[\e[1;36m\]workspace\[\e[0m\]\[\e[0;34m\]$\[\e[0m\] "
TERM_ENV["PROMPT_COMMAND"] = ""
if IN_VENV and VENV_BIN:
    TERM_ENV["PATH"] = VENV_BIN + os.pathsep + TERM_ENV.get("PATH", "")
    TERM_ENV["VIRTUAL_ENV"] = VENV_ROOT
PYTHON_DIR = os.path.dirname(PYTHON_PATH)
if PYTHON_DIR not in TERM_ENV.get("PATH", ""):
    TERM_ENV["PATH"] = PYTHON_DIR + os.pathsep + TERM_ENV.get("PATH", "")

# ============================================================
# PATH SAFETY
# ============================================================
def normalize_path(rel_path):
    if rel_path is None: return ""
    if not isinstance(rel_path, str): return ""
    rel_path = rel_path.replace("\\", "/").strip()
    rel_path = "".join(c for c in rel_path if ord(c) >= 32 or c == "\t")
    rel_path = rel_path.lstrip("/")
    parts = []
    for part in rel_path.split("/"):
        if part in ("", "."): continue
        if part == "..":
            if parts: parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)

def safe_join(rel_path):
    rel = normalize_path(rel_path)
    full = os.path.realpath(os.path.abspath(os.path.join(WORKSPACE, rel)))
    try:
        common = os.path.commonpath([WORKSPACE, full])
    except ValueError:
        abort(403, "Invalid path")
    if common != WORKSPACE:
        abort(403, "Path outside workspace")
    return full

def safe_rel(rel_path):
    return normalize_path(rel_path)

def shell_quote(s):
    if s is None: return "''"
    return "'" + str(s).replace("'", "'\\''") + "'"

# ============================================================
# USER STORE
# ============================================================
USERS_FILE = os.path.join(WORKSPACE, ".users.json")
USERS_LOCK = asyncio.Lock()

def _load_users_sync():
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️  Could not load users: {e}")
    return {}

def _save_users_sync(users):
    try:
        tmp = USERS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2)
        os.replace(tmp, USERS_FILE)
    except Exception as e:
        print(f"⚠️  Could not save users: {e}")

USERS = _load_users_sync()

def sanitize_username(name):
    name = (name or "").strip()[:32]
    name = re.sub(r"[^\w\-\. ]", "", name, flags=re.UNICODE)
    return name or "user"

def get_username(user_id):
    if not user_id: return "user"
    u = USERS.get(user_id, {})
    return u.get("username", "user")

async def set_username(user_id, username):
    if not user_id: return
    username = sanitize_username(username)
    async with USERS_LOCK:
        if user_id not in USERS: USERS[user_id] = {}
        USERS[user_id]["username"] = username
        USERS[user_id]["last_seen"] = time.time()
        await asyncio.to_thread(_save_users_sync, USERS)

def touch_user(user_id):
    if not user_id: return
    if user_id not in USERS: USERS[user_id] = {"username": "user"}
    USERS[user_id]["last_seen"] = time.time()

# ============================================================
# LYNkIO APP
# ============================================================
app = Lynk(
    host="0.0.0.0",
    port=5000,
    debug=False,
    max_body_size=MAX_BODY_SIZE,
    max_payload_size=MAX_BODY_SIZE,
    serve_client=True,
)

# ============================================================
# UTIL
# ============================================================
def get_unique_path(base_path):
    if not os.path.exists(base_path):
        return base_path
    dirname = os.path.dirname(base_path)
    basename = os.path.basename(base_path)
    name, ext = os.path.splitext(basename)
    counter = 1
    while True:
        new_path = os.path.join(dirname, f"{name}({counter}){ext}")
        if not os.path.exists(new_path):
            return new_path
        counter += 1

def atomic_write_text(path, content):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception: pass
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp): os.unlink(tmp)
        except Exception: pass
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            return False

# ============================================================
# SESSION / PROCESS-GROUP KILLING
# ============================================================
def _find_session_pids(sid):
    """Find all pids in a POSIX session (best-effort, Linux via /proc)."""
    pids = []
    if sid is None or sid <= 0: return pids
    if IS_LINUX:
        try:
            for entry in os.listdir('/proc'):
                if not entry.isdigit(): continue
                try:
                    with open(f'/proc/{entry}/stat', 'rb') as f:
                        data = f.read().decode('utf-8', 'ignore')
                    rparen = data.rfind(')')
                    if rparen == -1: continue
                    fields = data[rparen+2:].split()
                    # fields: state ppid pgrp session tty_nr ...
                    if len(fields) < 4: continue
                    if int(fields[3]) == sid:
                        pids.append(int(entry))
                except Exception:
                    continue
        except Exception:
            pass
        return pids
    # Fallback (macOS): use ps
    try:
        out = subprocess.check_output(['ps', '-o', 'pid=,sess='], timeout=3).decode()
        for line in out.strip().splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) == sid:
                try: pids.append(int(parts[0]))
                except Exception: pass
    except Exception:
        pass
    return pids

def _kill_session(sid, hard=False, exclude_pids=None):
    """Kill every process in the given session.
    Never touches the current server process or its ancestors.
    """
    exclude = set(exclude_pids or [])
    exclude.add(os.getpid())
    # Also exclude our parent chain
    try:
        ppid = os.getppid()
        while ppid and ppid > 1:
            exclude.add(ppid)
            try:
                with open(f'/proc/{ppid}/stat') as f:
                    data = f.read()
                rp = data.rfind(')')
                if rp == -1: break
                f2 = data[rp+2:].split()
                ppid = int(f2[1]) if len(f2) > 1 else 0
            except Exception:
                break
    except Exception:
        pass

    sig = signal.SIGKILL if hard else signal.SIGTERM
    pids = _find_session_pids(sid)
    for pid in pids:
        if pid in exclude: continue
        try: os.kill(pid, sig)
        except ProcessLookupError: pass
        except Exception: pass

    # If /proc walk failed, fall back to pgid kill
    if not pids:
        try: os.killpg(sid, sig)
        except Exception:
            try: os.kill(sid, sig)
            except Exception: pass

# ============================================================
# TERMINAL — Multi-session with proper controlling terminal
# ============================================================
term_sessions = {}

def _get_client_term_sessions(client_id):
    return term_sessions.setdefault(client_id, {})

def set_winsize(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception: pass

async def _kill_terminal_session(client_id, session_id):
    sessions = term_sessions.get(client_id)
    if not sessions: return
    session = sessions.pop(session_id, None)
    if not session: return

    # Remove reader FIRST (before fd closes)
    if not IS_WINDOWS and session.get("master_fd") is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.remove_reader(session["master_fd"])
        except Exception: pass

    # Cancel tasks
    for key in ("reader_task", "monitor_task"):
        t = session.get(key)
        if t:
            try: t.cancel()
            except Exception: pass

    if IS_WINDOWS:
        proc = session.get("proc")
        if proc and proc.returncode is None:
            try: proc.terminate()
            except Exception: pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                try: proc.kill()
                except Exception: pass
            except Exception: pass
    else:
        # Kill the entire session (bash + children)
        sid = session.get("sid")
        if sid:
            _kill_session(sid, hard=False)
            # Give it time to die
            for _ in range(20):
                if not _find_session_pids(sid):
                    break
                await asyncio.sleep(0.05)
            # Force kill if still alive
            if _find_session_pids(sid):
                _kill_session(sid, hard=True)

        # Reap the shell's pid
        pid = session.get("pid")
        if pid:
            try:
                os.waitpid(pid, os.WNOHANG)
            except Exception: pass

        # Close master fd
        if session.get("master_fd") is not None:
            try: os.close(session["master_fd"])
            except Exception: pass

async def _kill_all_client_sessions(client_id):
    sessions = term_sessions.pop(client_id, None)
    if not sessions: return
    term_sessions[client_id] = sessions
    for sid in list(sessions.keys()):
        await _kill_terminal_session(client_id, sid)
    term_sessions.pop(client_id, None)

async def start_terminal(client: Connection, session_id: str, rows=30, cols=120):
    """Start (or restart) a terminal session.
    On Unix uses pty.fork() so the pty becomes the controlling terminal —
    this is what makes Ctrl+C actually deliver SIGINT to foreground jobs.
    """
    await _kill_terminal_session(client.id, session_id)

    loop = asyncio.get_running_loop()
    sessions = _get_client_term_sessions(client.id)

    if IS_WINDOWS:
        proc = await asyncio.create_subprocess_shell(
            DEFAULT_SHELL,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=WORKSPACE, env=TERM_ENV,
        )
        session = {"proc": proc, "master_fd": None, "user_id": client.session.get("user_id")}
        sessions[session_id] = session

        async def reader_win(sess=session, sid=session_id):
            try:
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk: break
                    try:
                        await client.send(json.dumps({
                            "event": "term:output",
                            "data": {"session_id": sid, "data": base64.b64encode(chunk).decode()}
                        }))
                    except Exception: break
            except Exception: pass

        async def monitor_win(sess=session, sid=session_id):
            try:
                await proc.wait()
                if sessions.get(sid, {}).get("proc") is proc:
                    sessions.pop(sid, None)
                    try:
                        await client.send(json.dumps({
                            "event": "term:exit",
                            "data": {"session_id": sid, "code": proc.returncode}
                        }))
                    except Exception: pass
            except Exception: pass

        session["reader_task"] = asyncio.create_task(reader_win())
        session["monitor_task"] = asyncio.create_task(monitor_win())
        await client.send(json.dumps({
            "event": "term:ready",
            "data": {"session_id": session_id, "shell": DEFAULT_SHELL, "cwd": WORKSPACE}
        }))
        return

    # ---------- Unix: pty.fork() gives us a proper controlling terminal ----------
    try:
        pid, master_fd = pty.fork()
    except Exception as e:
        await client.send(json.dumps({
            "event": "term:error",
            "data": {"session_id": session_id, "error": f"pty.fork failed: {e}"}
        }))
        return

    if pid == 0:
        # ---- Child process ----
        try:
            os.chdir(WORKSPACE)
            # Ensure standard fds point at the slave side
            # (pty.fork already dup'd them for us)
            # exec bash -i
            os.execvpe(DEFAULT_SHELL, [DEFAULT_SHELL, "-i"], TERM_ENV)
        except Exception:
            try: os._exit(127)
            except Exception: pass

    # ---- Parent ----
    try:
        os.set_blocking(master_fd, False)
    except Exception: pass

    set_winsize(master_fd, rows, cols)

    # sid == pid for the session leader (bash)
    session = {
        "pid": pid,
        "sid": pid,
        "master_fd": master_fd,
        "user_id": client.session.get("user_id"),
    }
    sessions[session_id] = session

    read_queue = asyncio.Queue()

    def on_readable(fd=master_fd):
        try:
            data = os.read(fd, 4096)
            if not data:
                try: loop.remove_reader(fd)
                except Exception: pass
                return
            read_queue.put_nowait(data)
        except BlockingIOError:
            pass
        except OSError:
            try: loop.remove_reader(fd)
            except Exception: pass

    loop.add_reader(master_fd, on_readable)

    async def pump(sid=session_id):
        try:
            while True:
                chunk = await read_queue.get()
                try:
                    await client.send(json.dumps({
                        "event": "term:output",
                        "data": {"session_id": sid, "data": base64.b64encode(chunk).decode()}
                    }))
                except Exception:
                    break
        except asyncio.CancelledError:
            pass

    async def monitor_unix(sid=session_id, p=pid, fd=master_fd):
        try:
            while True:
                try:
                    wpid, status = os.waitpid(p, os.WNOHANG)
                    if wpid == p:
                        break
                except ChildProcessError:
                    break
                except Exception:
                    break
                await asyncio.sleep(0.4)
            try: loop.remove_reader(fd)
            except Exception: pass
            if sessions.get(sid, {}).get("pid") == p:
                sessions.pop(sid, None)
                # Clean up any leftover session processes
                try: _kill_session(p, hard=True)
                except Exception: pass
                try:
                    await client.send(json.dumps({
                        "event": "term:exit",
                        "data": {"session_id": sid, "code": 0}
                    }))
                except Exception: pass
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    session["reader_task"] = asyncio.create_task(pump())
    session["monitor_task"] = asyncio.create_task(monitor_unix())

    await client.send(json.dumps({
        "event": "term:ready",
        "data": {"session_id": session_id, "shell": DEFAULT_SHELL, "cwd": WORKSPACE}
    }))

@app.on("term:start")
async def ws_term_start(client, data):
    try:
        sid = (data or {}).get("session_id") or "t1"
        rows = int((data or {}).get("rows", 30))
        cols = int((data or {}).get("cols", 120))
        await start_terminal(client, sid, rows, cols)
    except Exception as e:
        try: await client.send(json.dumps({"event": "term:error", "data": {"error": str(e)}}))
        except Exception: pass

@app.on("term:restart")
async def ws_term_restart(client, data):
    try:
        sid = (data or {}).get("session_id") or "t1"
        rows = int((data or {}).get("rows", 30))
        cols = int((data or {}).get("cols", 120))
        await start_terminal(client, sid, rows, cols)
    except Exception as e:
        try: await client.send(json.dumps({"event": "term:error", "data": {"error": str(e)}}))
        except Exception: pass

@app.on("term:close")
async def ws_term_close(client, data):
    try:
        sid = (data or {}).get("session_id")
        if sid:
            await _kill_terminal_session(client.id, sid)
    except Exception: pass

@app.on("term:input")
async def ws_term_input(client, data):
    try:
        sid = (data or {}).get("session_id")
        text = (data or {}).get("text", "")
        if not sid or not text: return
        sessions = term_sessions.get(client.id)
        if not sessions: return
        session = sessions.get(sid)
        if not session: return

        if IS_WINDOWS:
            proc = session.get("proc")
            if proc and proc.returncode is None:
                proc.stdin.write(text.encode()); await proc.stdin.drain()
        else:
            fd = session.get("master_fd")
            if fd is not None:
                os.write(fd, text.encode())
    except Exception: pass

@app.on("term:resize")
async def ws_term_resize(client, data):
    try:
        sid = (data or {}).get("session_id")
        if not sid or IS_WINDOWS: return
        sessions = term_sessions.get(client.id)
        if not sessions: return
        session = sessions.get(sid)
        if not session: return
        rows = int((data or {}).get("rows", 30))
        cols = int((data or {}).get("cols", 120))
        fd = session.get("master_fd")
        if fd is not None:
            set_winsize(fd, rows, cols)
    except Exception: pass

@app.on("term:set_folder")
async def ws_term_set_folder(client, data):
    try:
        raw = (data or {}).get("folder", "") or ""
        folder = normalize_path(raw)
        if folder:
            try:
                check_full = safe_join(folder)
                if not os.path.isdir(check_full):
                    folder = ""
            except Exception:
                folder = ""
        target_sid = (data or {}).get("session_id")
        username = client.session.get("username") or get_username(client.session.get("user_id")) or "user"
        username = re.sub(r"[^\w\-\.]", "", username) or "user"
        display_path = folder if folder else "workspace"

        ps1 = (
            "\\[\\e[1;32m\\]" + username + "\\[\\e[0m\\]"
            "\\[\\e[0;33m\\]$\\[\\e[0m\\]"
            "\\[\\e[1;36m\\]" + display_path + "\\[\\e[0m\\]"
            "\\[\\e[0;34m\\]$\\[\\e[0m\\] "
        )
        cmd = "PS1='" + ps1.replace("'", "'\\''") + "'\n"
        refresh = "\x0c"

        sessions = term_sessions.get(client.id)
        if not sessions: return
        targets = [target_sid] if target_sid and target_sid in sessions else list(sessions.keys())

        for sid in targets:
            session = sessions.get(sid)
            if not session: continue
            try:
                if IS_WINDOWS:
                    proc = session.get("proc")
                    if proc and proc.returncode is None:
                        proc.stdin.write(cmd.encode()); await proc.stdin.drain()
                        proc.stdin.write(refresh.encode()); await proc.stdin.drain()
                else:
                    fd = session.get("master_fd")
                    if fd is not None:
                        os.write(fd, cmd.encode())
                        os.write(fd, refresh.encode())
            except Exception: pass
    except Exception: pass

@app.on("term:whoami")
async def ws_term_whoami(client, data):
    uid = client.session.get("user_id") or client.id
    username = get_username(uid)
    try:
        await client.send(json.dumps({"event": "user:info", "data": {"user_id": uid, "username": username}}))
    except Exception: pass

# ============================================================
# MULTI-USER + FILE SYNC WS
# ============================================================
@app.on("user:init")
async def ws_user_init(client, data):
    try:
        uid = ((data or {}).get("user_id") or "").strip() or client.id
        client.session["user_id"] = uid
        touch_user(uid)
        username = get_username(uid)
        client.session["username"] = username
        await client.send(json.dumps({"event": "user:info", "data": {"user_id": uid, "username": username}}))
    except Exception as e:
        print(f"⚠️  user:init error: {e}")

@app.on("user:rename")
async def ws_user_rename(client, data):
    try:
        uid = client.session.get("user_id") or client.id
        name = sanitize_username((data or {}).get("username", ""))
        await set_username(uid, name)
        client.session["username"] = name
        await client.send(json.dumps({"event": "user:info", "data": {"user_id": uid, "username": name}}))
        for room in list(client.rooms):
            if room.startswith("file:"):
                await broadcast_viewers(room[5:])
    except Exception as e:
        print(f"⚠️  user:rename error: {e}")

@app.on("file:open")
async def ws_file_open(client, data):
    try:
        path = safe_rel((data or {}).get("path", ""))
        if not path: return
        open_files = client.session.setdefault("open_files", set())
        open_files.add(path)
        room = f"file:{path}"
        app.join_room(client.id, room)
        await broadcast_viewers(path)
    except Exception as e:
        print(f"⚠️  file:open error: {e}")

@app.on("file:close")
async def ws_file_close(client, data):
    try:
        path = safe_rel((data or {}).get("path", ""))
        if not path: return
        open_files = client.session.get("open_files")
        if open_files is not None: open_files.discard(path)
        room = f"file:{path}"
        app.leave_room(client.id, room)
        await broadcast_viewers(path)
    except Exception as e:
        print(f"⚠️  file:close error: {e}")

@app.on("file:save")
async def ws_file_save(client, data):
    try:
        path = safe_rel((data or {}).get("path", ""))
        content = (data or {}).get("content", "")
        if not path: return
        try:
            full = safe_join(path)
        except Exception:
            return
        if not os.path.isfile(full): return
        ok = await asyncio.to_thread(atomic_write_text, full, content)
        if not ok: return
        uid = client.session.get("user_id") or client.id
        username = client.session.get("username") or get_username(uid)
        room = f"file:{path}"
        await app.emit_to_room(room, "file:updated", {
            "path": path,
            "content": content,
            "from_user": uid,
            "from_client": client.id,
            "username": username,
            "timestamp": time.time(),
        }, exclude=client.id)
    except Exception as e:
        print(f"⚠️  file:save error: {e}")

async def broadcast_viewers(path):
    try:
        room = f"file:{path}"
        cids = list(app.get_room_clients(room))
        if not cids: return
        viewers = []
        for cid in cids:
            c = app._clients_all.get(cid)
            if not c or c.closed: continue
            uid = c.session.get("user_id") or cid
            uname = c.session.get("username") or get_username(uid)
            viewers.append({"user_id": uid, "username": uname})
        payload = {"path": path, "viewers": viewers}
        for cid in cids:
            c = app._clients_all.get(cid)
            if not c or c.closed: continue
            try:
                await c.send(json.dumps({"event": "file:viewers", "data": payload}))
            except Exception: pass
    except Exception as e:
        print(f"⚠️  broadcast_viewers error: {e}")

@app.on_internal("disconnect")
async def on_ws_disconnect(client, data=None):
    try:
        await _kill_all_client_sessions(client.id)
    except Exception: pass
    try:
        open_files = list(client.session.get("open_files", set()))
        for path in open_files:
            await broadcast_viewers(path)
    except Exception: pass

@app.on_internal("connect")
async def on_ws_connect(client, data=None):
    pass

# ============================================================
# HTTP — USER
# ============================================================
@app.get("/api/user")
async def api_get_user(req):
    uid = (req.headers.get("x-user-id") or "").strip()
    if not uid: uid = str(uuid.uuid4())
    touch_user(uid)
    username = get_username(uid)
    return json_response({"user_id": uid, "username": username})

@app.post("/api/user")
async def api_set_user(req):
    data = await req.json()
    uid = (data.get("user_id") or "").strip()
    username = (data.get("username") or "").strip()
    if not uid or not username:
        abort(400, "Missing user_id or username")
    username = sanitize_username(username)
    await set_username(uid, username)
    return json_response({"success": True, "user_id": uid, "username": username})

# ============================================================
# HTTP — FILES
# ============================================================
@app.get("/")
async def index(req):
    return HTML_PAGE, "text/html"

@app.get("/files")
async def list_files(req):
    def walk(dirpath, rel=""):
        items = []
        try:
            for name in sorted(os.listdir(dirpath)):
                if name.startswith("."): continue
                full = os.path.join(dirpath, name)
                r = f"{rel}/{name}" if rel else name
                if os.path.isdir(full):
                    items.append({"name": name, "type": "folder", "path": r, "children": walk(full, r)})
                else:
                    items.append({"name": name, "type": "file", "path": r, "size": os.path.getsize(full)})
        except PermissionError: pass
        return items
    return json_response({"tree": walk(WORKSPACE)})

@app.get("/file/content")
async def file_content(req):
    path = req.query_params.get("path")
    if not path: abort(400, "Missing path")
    full = safe_join(path)
    if not os.path.isfile(full): abort(404, "Not found")
    try:
        with open(full, "r", encoding="utf-8") as f:
            return json_response({"content": f.read(), "isBinary": False})
    except UnicodeDecodeError:
        return json_response({"content": "", "isBinary": True})
    except Exception as e:
        abort(500, str(e))

@app.post("/file/save")
async def file_save(req):
    data = await req.json()
    path = data.get("path")
    content = data.get("content", "")
    if not path: abort(400, "Missing path")
    full = safe_join(path)
    ok = await asyncio.to_thread(atomic_write_text, full, content)
    if not ok: abort(500, "Write failed")
    return json_response({"success": True})

@app.post("/file/create")
async def file_create(req):
    data = await req.json()
    path = data.get("path")
    if not path: abort(400, "Missing path")
    full = safe_join(path)
    rel = safe_rel(path)
    if os.path.exists(full):
        full = get_unique_path(full)
        rel = os.path.relpath(full, WORKSPACE).replace("\\", "/")
    def _touch():
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f: f.write("")
    await asyncio.to_thread(_touch)
    return json_response({"success": True, "path": rel})

@app.post("/folder/create")
async def folder_create(req):
    data = await req.json()
    path = data.get("path")
    if not path: abort(400, "Missing path")
    full = safe_join(path)
    rel = safe_rel(path)
    if os.path.exists(full):
        full = get_unique_path(full)
        rel = os.path.relpath(full, WORKSPACE).replace("\\", "/")
    os.makedirs(full, exist_ok=True)
    return json_response({"success": True, "path": rel})

@app.post("/file/delete")
async def file_delete(req):
    data = await req.json()
    path = data.get("path")
    if not path: abort(400, "Missing path")
    full = safe_join(path)
    if not os.path.exists(full): abort(404, "Not found")
    if os.path.realpath(full) == WORKSPACE: abort(403, "Cannot delete root workspace")
    if os.path.isdir(full): shutil.rmtree(full)
    else: os.remove(full)
    return json_response({"success": True})

@app.post("/file/rename")
async def file_rename(req):
    data = await req.json()
    old = data.get("old"); new = data.get("new")
    if not old or not new: abort(400, "Missing old/new")
    old_full = safe_join(old)
    new_full = safe_join(new)
    if not os.path.exists(old_full): abort(404, "Source not found")
    if os.path.realpath(old_full) == WORKSPACE: abort(403, "Cannot rename workspace root")
    rel_new = safe_rel(new)
    if os.path.exists(new_full):
        new_full = get_unique_path(new_full)
        rel_new = os.path.relpath(new_full, WORKSPACE).replace("\\", "/")
    os.makedirs(os.path.dirname(new_full), exist_ok=True)
    os.rename(old_full, new_full)
    return json_response({"success": True, "path": rel_new})

def parse_multipart(data, boundary):
    parts = data.split(b"--" + boundary.encode())
    fields, files = {}, {}
    for part in parts:
        if not part or part in (b"--\r\n", b"--"): continue
        part = part.lstrip(b"\r\n")
        idx = part.find(b"\r\n\r\n")
        if idx == -1: continue
        headers = part[:idx].decode("utf-8", "ignore")
        body = part[idx+4:]
        if body.endswith(b"\r\n"): body = body[:-2]
        if "Content-Disposition:" not in headers: continue
        m_name = re.search(r'name="([^"]*)"', headers)
        m_file = re.search(r'filename="([^"]*)"', headers)
        if not m_name: continue
        name = m_name.group(1)
        if m_file: files[name] = (m_file.group(1), body)
        else: fields[name] = body.decode("utf-8", "ignore").strip()
    return fields, files

@app.post("/upload")
async def upload(req):
    ct = req.headers.get("content-type", "")
    if not ct.startswith("multipart/form-data"):
        abort(400, "Expected multipart")
    boundary = ct.split("boundary=")[1].strip()
    fields, files = parse_multipart(req.body, boundary)
    if "file" not in files: abort(400, "No file")
    filename, data = files["file"]
    filename = os.path.basename(filename.replace("\\", "/")).strip()
    if not filename or filename in (".", ".."):
        abort(400, "Invalid filename")
    target = safe_rel(fields.get("target", ""))
    rel = f"{target}/{filename}" if target else filename
    rel = safe_rel(rel)
    if not rel: abort(400, "Invalid upload path")
    full = safe_join(rel)
    if os.path.exists(full):
        full = get_unique_path(full)
        rel = os.path.relpath(full, WORKSPACE).replace("\\", "/")
    def _write():
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f: f.write(data)
    await asyncio.to_thread(_write)
    return json_response({"success": True, "path": rel})

@app.get("/download")
async def download(req):
    path = req.query_params.get("path", "")
    if not path:
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(WORKSPACE):
                if ".venv" in root: continue
                for f in files:
                    fp = os.path.join(root, f)
                    zf.write(fp, os.path.relpath(fp, WORKSPACE))
        return (mem.getvalue(), "application/zip")
    full = safe_join(path)
    if not os.path.exists(full): abort(404, "Not found")
    if os.path.isdir(full):
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(full):
                for f in files:
                    fp = os.path.join(root, f)
                    zf.write(fp, os.path.relpath(fp, WORKSPACE))
        return (mem.getvalue(), "application/zip")
    return send_file(full, base_dir="", as_attachment=True)

# ============================================================
# HTML PAGE (unchanged from previous — kept identical)
# ============================================================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover" />
<meta name="theme-color" content="#0b0d12" />
<title>CodeForge • Multi-User Terminal</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css" />
<link rel="stylesheet" data-name="vs/editor/editor.main" href="https://cdn.jsdelivr.net/npm/monaco-editor@0.39.0/min/vs/editor/editor.main.min.css" />
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg-app:#0b0d12;--bg-surface:#14161e;--bg-sidebar:#101219;--bg-toolbar:#14161e;
  --bg-tab:#1a1d27;--bg-tab-active:#242836;--bg-hover:#1f2330;--bg-active:#2a2f42;
  --bg-input:#1a1d27;--border:#242838;--text:#e6e8f0;--text-2:#9aa3bf;
  --text-3:#5f6884;--accent:#e95420;--accent-2:#ff7043;--accent-glow:rgba(233,84,32,0.3);
  --danger:#ff5c78;--success:#4ade80;--warning:#fbbf24;
  --ubuntu-2:#2c001e;
  --f-sans:'Inter',-apple-system,sans-serif;
  --f-mono:'Ubuntu Mono','JetBrains Mono','Fira Code',monospace;
  --sidebar:260px;--tab-h:36px;--status-h:22px;--toolbar-h:44px;--crumb-h:30px;
  --safe-top:env(safe-area-inset-top,0px);
  --safe-bot:env(safe-area-inset-bottom,0px);
}
html,body{height:100%;font-family:var(--f-sans);background:var(--bg-app);color:var(--text);overflow:hidden;font-size:13px}
body{height:100dvh;padding-top:var(--safe-top);padding-bottom:var(--safe-bot)}
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
#app{display:flex;flex-direction:column;height:100%}
#toolbar{display:flex;align-items:center;gap:4px;padding:0 8px;height:var(--toolbar-h);background:var(--bg-toolbar);border-bottom:1px solid var(--border);flex-shrink:0;z-index:10}
.brand{display:flex;align-items:center;gap:6px;font-weight:600;font-size:13px;margin-right:4px}
.brand i{color:var(--accent);font-size:16px}
.brand span{background:linear-gradient(135deg,var(--accent),#ff9a76);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.brand small{font-weight:400;font-size:10px;color:var(--text-3);-webkit-text-fill-color:var(--text-3)}
.tbtn{background:transparent;border:none;color:var(--text-2);padding:6px 10px;border-radius:6px;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:5px;height:36px;min-width:36px;justify-content:center;position:relative}
.tbtn:hover,.tbtn:active{background:var(--bg-hover);color:var(--text)}
.tbtn.primary{background:var(--accent);color:#fff;font-weight:500}
.tbtn i{font-size:13px}
.tbtn .badge{position:absolute;top:2px;right:2px;background:var(--success);color:#0b0d12;font-size:9px;font-weight:700;padding:1px 5px;border-radius:8px;min-width:16px;text-align:center}
.tsplit{flex:1}
#mobile-toggle{display:none;background:transparent;border:none;color:var(--text-2);font-size:22px;padding:6px 10px;min-width:40px;height:40px}
#main{display:flex;flex:1;overflow:hidden;position:relative}
#sidebar{width:var(--sidebar);min-width:var(--sidebar);background:var(--bg-sidebar);border-right:1px solid var(--border);display:flex;flex-direction:column;flex-shrink:0;transition:transform .25s;z-index:30}
#sb-head{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border);height:40px}
#sb-head span{font-size:10px;font-weight:600;color:var(--text-3);text-transform:uppercase;letter-spacing:.8px}
#sb-head .acts{display:flex;gap:2px}
#sb-head button{background:transparent;border:none;color:var(--text-3);padding:8px;border-radius:6px;font-size:13px;min-width:36px;min-height:36px;cursor:pointer}
#sb-head button:hover,#sb-head button:active{background:var(--bg-hover);color:var(--text)}
#file-tree{flex:1;overflow:auto;-webkit-overflow-scrolling:touch;padding:4px 0}
.ft-item{display:flex;align-items:center;padding:6px 8px 6px 4px;cursor:pointer;font-size:13px;color:var(--text-2);white-space:nowrap;min-height:34px;gap:6px;border-radius:4px;margin:0 4px}
.ft-item:hover,.ft-item:active{background:var(--bg-hover);color:var(--text)}
.ft-item.active{background:var(--bg-active);color:#fff;box-shadow:inset 2px 0 0 var(--accent)}
.ft-item.current{background:rgba(233,84,32,.12);color:#fff}
.ft-item.current > .lbl{font-weight:600}
.ft-item .chev{width:20px;text-align:center;font-size:9px;color:var(--text-3);flex-shrink:0}
.ft-item .chev.open{transform:rotate(90deg)}
.ft-item .ic{width:18px;text-align:center;font-size:13px;flex-shrink:0}
.ft-item .ic.folder{color:#e8b87a}
.ft-item .lbl{flex:1;overflow:hidden;text-overflow:ellipsis}
.ft-item .mini-av{display:flex;gap:2px;flex-shrink:0}
.ft-item .mini-av .av{width:14px;height:14px;border-radius:50%;background:var(--accent);color:#fff;font-size:8px;font-weight:700;display:flex;align-items:center;justify-content:center;text-transform:uppercase}
.ft-kids{padding-left:12px}
.ft-kids.collapsed{display:none}
#sb-backdrop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:25;opacity:0;transition:opacity .25s}
#sb-backdrop.show{display:block;opacity:1}
#editor-area{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
#breadcrumb{display:flex;align-items:center;gap:4px;padding:0 10px;height:var(--crumb-h);background:var(--bg-surface);border-bottom:1px solid var(--border);flex-shrink:0;overflow-x:auto;white-space:nowrap;scrollbar-width:none;font-size:12px}
#breadcrumb::-webkit-scrollbar{display:none}
#breadcrumb .crumb-root{display:flex;align-items:center;gap:6px;color:var(--accent);cursor:pointer;padding:3px 8px;border-radius:4px;flex-shrink:0}
#breadcrumb .crumb-root:hover{background:var(--bg-hover)}
#breadcrumb .crumb-root i{font-size:11px}
#breadcrumb .sep{color:var(--text-3);flex-shrink:0;font-size:10px}
#breadcrumb .crumb{color:var(--text-2);cursor:pointer;padding:3px 8px;border-radius:4px;flex-shrink:0;display:flex;align-items:center;gap:4px}
#breadcrumb .crumb:hover{background:var(--bg-hover);color:var(--text)}
#breadcrumb .crumb.current{color:#fff;background:rgba(233,84,32,.15);font-weight:500}
#breadcrumb .crumb i{font-size:10px;color:#e8b87a}
#breadcrumb .dl-current{margin-left:auto;color:var(--text-3);cursor:pointer;padding:3px 8px;border-radius:4px;flex-shrink:0;display:flex;align-items:center;gap:4px;font-size:11px}
#breadcrumb .dl-current:hover{background:var(--bg-hover);color:var(--text)}
#tabs{display:flex;align-items:center;background:var(--bg-surface);border-bottom:1px solid var(--border);height:var(--tab-h);flex-shrink:0;overflow-x:auto;padding:0 4px;gap:1px}
.tab{display:flex;align-items:center;gap:6px;padding:0 10px;height:100%;font-size:12px;color:var(--text-2);background:var(--bg-tab);border-right:1px solid var(--border);cursor:pointer;white-space:nowrap;flex-shrink:0}
.tab.active{background:var(--bg-tab-active);color:#fff;border-bottom:2px solid var(--accent)}
.tab .close{font-size:11px;opacity:.6;padding:4px 6px;min-width:22px;text-align:center;cursor:pointer}
.tab .ic{font-size:11px;color:var(--text-3)}
.split-wrap{flex:1;display:flex;flex-direction:column;overflow:hidden;position:relative}
#editor-pane{flex:1;overflow:hidden;position:relative;min-height:80px}
#editor-container{position:absolute;inset:0}
#empty-state{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;color:var(--text-3);padding:24px;text-align:center;background:var(--bg-app);z-index:2}
#empty-state .big{font-size:44px;color:var(--border)}
#empty-state h2{font-weight:400;font-size:16px;color:var(--text-2)}
#empty-state .btn{margin-top:6px;background:var(--accent);color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:13px;font-weight:500;min-height:42px;cursor:pointer}
#term-pane{display:flex;flex-direction:column;background:var(--ubuntu-2);border-top:1px solid var(--border);overflow:hidden;transition:height .25s;flex-shrink:0;height:0;position:relative}
#term-pane.open{height:52%}
#term-head{display:flex;align-items:center;padding:0 4px;background:linear-gradient(180deg,#3c0e2c,#2c001e);border-bottom:1px solid #4a2040;height:40px;flex-shrink:0;gap:2px}
#term-head .title{display:flex;align-items:center;gap:6px;font-family:var(--f-mono);font-size:11.5px;color:#ffcec1;cursor:pointer;padding:4px 8px;border-radius:4px;flex-shrink:0}
#term-head .title:hover{background:rgba(255,255,255,.08)}
#term-head .title i.term-ico{color:#e95420}
#term-head .title .dots{display:flex;gap:4px;margin-right:4px}
#term-head .title .dots span{width:10px;height:10px;border-radius:50%}
.dot-close{background:#ff5f56}
.dot-min{background:#ffbd2e}
.dot-max{background:#27c93f}
#term-tabs{flex:1;display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;padding:0 4px;min-width:0}
#term-tabs::-webkit-scrollbar{display:none}
.term-tab{display:flex;align-items:center;gap:6px;padding:4px 8px;background:rgba(255,255,255,.06);border:1px solid transparent;color:#d9a4bf;border-radius:6px;font-family:var(--f-mono);font-size:11.5px;cursor:pointer;white-space:nowrap;flex-shrink:0;transition:.15s}
.term-tab:hover{background:rgba(255,255,255,.12);color:#fff}
.term-tab.active{background:rgba(233,84,32,.35);color:#fff;border-color:#e95420}
.term-tab i.ti{color:#e95420;font-size:11px}
.term-tab.active i.ti{color:#fff}
.term-tab .term-tab-close{font-size:10px;opacity:.6;padding:2px 4px;border-radius:3px;margin-left:2px}
.term-tab .term-tab-close:hover{background:rgba(0,0,0,.3);opacity:1;color:#fff}
#term-head .acts{display:flex;gap:2px;flex-shrink:0}
#term-head .acts button{background:transparent;border:none;color:#d9a4bf;padding:8px;border-radius:4px;font-size:12px;min-width:34px;min-height:34px;cursor:pointer}
#term-head .acts button:hover,#term-head .acts button:active{background:rgba(255,255,255,.1);color:#fff}
#term-body{flex:1;display:flex;flex-direction:column;overflow:hidden;position:relative;min-height:0}
#term-stack-wrap{flex:1;position:relative;min-height:0;background:#2c001e}
#terminal-stack{position:absolute;inset:0}
.term-wrapper{position:absolute;inset:0;display:none}
.term-wrapper.active{display:block}
.term-wrapper .xterm{height:100%;width:100%;padding:4px 2px 2px 6px}
.term-wrapper .xterm-viewport{background:transparent !important;-webkit-overflow-scrolling:touch}
#tap-to-type{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(44,0,30,.9);z-index:50;cursor:pointer}
#tap-to-type.show{display:flex}
#tap-to-type .inner{text-align:center;color:#ffcec1;font-family:var(--f-sans);pointer-events:none}
#tap-to-type .inner i{font-size:48px;margin-bottom:14px;display:block;color:#e95420;animation:pulse 1.6s ease-in-out infinite}
#tap-to-type .inner h3{font-size:16px;font-weight:500;margin-bottom:6px}
#tap-to-type .inner p{font-size:12px;color:#d9a4bf}
@keyframes pulse{0%,100%{transform:scale(1);opacity:.85}50%{transform:scale(1.08);opacity:1}}
#key-bar-wrap{display:none;background:#3c0e2c;border-top:1px solid #4a2040;flex-shrink:0;position:relative;z-index:20}
#key-bar-wrap.open{display:block}
#key-bar-scroller{position:relative;display:flex;flex-direction:row;align-items:stretch;overflow:hidden}
#key-bar{flex:1 1 auto;display:grid;grid-template-columns:repeat(4, minmax(0, 1fr));grid-auto-rows:42px;gap:6px;padding:6px 8px;overflow-y:auto;overflow-x:hidden;max-height:180px;scroll-behavior:smooth;-webkit-overflow-scrolling:touch;scrollbar-width:thin;scrollbar-color:#7a3a5f transparent;overscroll-behavior:contain;touch-action:pan-y}
#key-bar::-webkit-scrollbar{width:4px}
#key-bar::-webkit-scrollbar-thumb{background:#7a3a5f;border-radius:2px}
#key-bar::-webkit-scrollbar-track{background:transparent}
.kk{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);color:#ffcec1;padding:0;border-radius:6px;font-family:var(--f-mono);font-size:12px;height:42px;min-width:0;display:flex;align-items:center;justify-content:center;user-select:none;-webkit-user-select:none;cursor:pointer;white-space:nowrap;touch-action:manipulation}
.kk:active,.kk:hover{background:rgba(233,84,32,.45);border-color:#e95420;color:#fff}
.kk i{font-size:12px}
.kb-nav{flex:0 0 auto;display:flex;flex-direction:column;gap:4px;padding:6px 6px;background:#3c0e2c;border-left:1px solid #4a2040;width:38px}
.kb-nav button{flex:1;min-height:24px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.12);color:#ffcec1;border-radius:5px;cursor:pointer;font-size:11px;display:flex;align-items:center;justify-content:center;padding:0;transition:background .12s, opacity .12s}
.kb-nav button:hover:not(:disabled),.kb-nav button:active:not(:disabled){background:rgba(233,84,32,.35);border-color:#e95420;color:#fff}
.kb-nav button:disabled,.kb-nav button.disabled{opacity:.28;cursor:default;background:rgba(255,255,255,.04)}
#status{display:flex;align-items:center;gap:12px;padding:0 10px;height:var(--status-h);background:var(--bg-surface);border-top:1px solid var(--border);font-size:10.5px;color:var(--text-3);flex-shrink:0;font-family:var(--f-mono);overflow:hidden}
#status .it{display:flex;align-items:center;gap:5px;flex-shrink:0}
#status .d{width:7px;height:7px;border-radius:50%}
#status .d.saved{background:var(--success)}
#status .d.dirty{background:var(--warning)}
#status .d.term{background:#4ade80}
#status .s{flex:1}
#ctx{position:fixed;background:var(--bg-surface);border:1px solid var(--border);border-radius:10px;padding:5px 0;min-width:200px;box-shadow:0 12px 40px rgba(0,0,0,.7);z-index:9999;display:none}
#ctx .mi{display:flex;align-items:center;gap:12px;padding:12px 16px;font-size:13.5px;color:var(--text-2);min-height:44px;cursor:pointer}
#ctx .mi:hover,#ctx .mi:active{background:var(--bg-hover);color:var(--text)}
#ctx .mi i{width:18px;font-size:13px;color:var(--text-3);text-align:center}
#ctx .mi.danger{color:var(--danger)}
#ctx .div{height:1px;background:var(--border);margin:4px 10px}
#modal{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;justify-content:center;z-index:10000;padding:16px}
#modal.show{display:flex}
#mbox{background:var(--bg-surface);border:1px solid var(--border);border-radius:12px;padding:20px 22px;max-width:420px;width:100%}
#mbox h3{font-size:15px;margin-bottom:4px}
#mbox p{font-size:12.5px;color:var(--text-2);margin-bottom:14px}
#mbox input{width:100%;padding:12px 14px;background:var(--bg-input);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:16px;min-height:44px;outline:none}
#mbox input:focus{border-color:var(--accent)}
#mbox .acts{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}
#mbox .acts button{padding:10px 18px;border-radius:8px;border:none;font-size:13.5px;min-height:42px;min-width:80px;cursor:pointer}
.btn-c{background:transparent;color:var(--text-2)}
.btn-k{background:var(--accent);color:#fff;font-weight:500}
#toast{position:fixed;bottom:calc(60px + var(--safe-bot));left:50%;transform:translateX(-50%) translateY(12px);background:var(--bg-surface);border:1px solid var(--border);border-radius:10px;padding:10px 18px;font-size:13px;z-index:99999;opacity:0;pointer-events:none;transition:.25s;max-width:calc(100vw - 32px)}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#toast.success{border-color:var(--success)}
#toast.error{border-color:var(--danger)}
#toast.info{border-color:var(--accent)}
#loading{position:fixed;inset:0;background:rgba(11,13,18,.9);display:none;align-items:center;justify-content:center;z-index:99998;flex-direction:column;gap:14px}
#loading.show{display:flex}
.spinner{width:44px;height:44px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#loading .lbl{font-size:14px;color:var(--text-2)}
@media (max-width:820px){
  .brand small{display:none}
  .tbtn span{display:none}
  #term-pane.open{height:58%}
}
@media (max-width:600px){
  :root{--toolbar-h:46px}
  #sidebar{position:absolute;left:0;top:0;bottom:0;width:280px;min-width:280px;transform:translateX(-100%);box-shadow:6px 0 30px rgba(0,0,0,.6)}
  #sidebar.open{transform:translateX(0)}
  #mobile-toggle{display:inline-flex !important}
  #term-pane.open{height:66%}
}
@media (max-width:400px){
  .brand span{display:none}
  .brand{font-size:0}
  .brand i{font-size:20px}
  #term-pane.open{height:72%}
}
</style>
</head>
<body>
<div id="app">
  <div id="toolbar">
    <button id="mobile-toggle"><i class="fas fa-bars"></i></button>
    <div class="brand"><i class="fas fa-terminal"></i><span>CodeForge</span><small>· multi-user</small></div>
    <button class="tbtn primary" id="btn-open"><i class="fas fa-folder-open"></i><span>Files</span></button>
    <button class="tbtn" id="btn-term"><i class="fas fa-terminal"></i><span>Term</span></button>
    <div class="tsplit"></div>
    <button class="tbtn" id="btn-new-file"><i class="fas fa-file-circle-plus"></i></button>
    <button class="tbtn" id="btn-upload"><i class="fas fa-upload"></i></button>
    <button class="tbtn" id="btn-download" title="Download current"><i class="fas fa-download"></i></button>
    <button class="tbtn" id="btn-user" title="Your identity"><i class="fas fa-user-circle"></i><span class="badge" id="online-badge">1</span></button>
  </div>
  <div id="main">
    <div id="sb-backdrop"></div>
    <div id="sidebar">
      <div id="sb-head">
        <span><i class="fas fa-folder-tree"></i> Explorer</span>
        <div class="acts">
          <button id="sb-new-file"><i class="fas fa-file"></i></button>
          <button id="sb-new-folder"><i class="fas fa-folder-plus"></i></button>
          <button id="sb-refresh"><i class="fas fa-rotate"></i></button>
        </div>
      </div>
      <div id="file-tree"></div>
    </div>
    <div id="editor-area">
      <div id="breadcrumb">
        <span class="crumb-root" data-path=""><i class="fas fa-folder-tree"></i> workspace</span>
        <span class="dl-current" id="dl-current"><i class="fas fa-download"></i> <span id="dl-current-label">Download</span></span>
      </div>
      <div id="tabs"></div>
      <div class="split-wrap">
        <div id="editor-pane">
          <div id="editor-container"></div>
          <div id="empty-state">
            <div class="big"><i class="fas fa-folder-open"></i></div>
            <h2>No file open</h2>
            <p>Pick a file from the explorer or open the terminal.</p>
            <button class="btn" id="empty-open"><i class="fas fa-terminal"></i> Open Terminal</button>
          </div>
        </div>
        <div id="term-pane">
          <div id="term-head">
            <div class="title" id="term-title-btn" title="Click to change username">
              <div class="dots">
                <span class="dot-close"></span><span class="dot-min"></span><span class="dot-max"></span>
              </div>
              <i class="fas fa-terminal term-ico"></i>
              <span id="term-title">bash</span>
            </div>
            <div id="term-tabs"></div>
            <div class="acts">
              <button id="term-new" title="New terminal"><i class="fas fa-plus"></i></button>
              <button id="term-user" title="Change username"><i class="fas fa-user-pen"></i></button>
              <button id="term-keys" title="Keys"><i class="fas fa-keyboard"></i></button>
              <button id="term-clear" title="Clear"><i class="fas fa-eraser"></i></button>
              <button id="term-restart" title="Restart"><i class="fas fa-rotate-right"></i></button>
              <button id="term-close" title="Close"><i class="fas fa-times"></i></button>
            </div>
          </div>
          <div id="term-body">
            <div id="term-stack-wrap">
              <div id="terminal-stack"></div>
              <div id="tap-to-type">
                <div class="inner">
                  <i class="fas fa-keyboard"></i>
                  <h3>Tap to start typing</h3>
                  <p>Opens the on-screen keyboard</p>
                </div>
              </div>
            </div>
            <div id="key-bar-wrap">
              <div id="key-bar-scroller">
                <div id="key-bar">
                  <div class="kk" data-k="\u001b[A"><i class="fas fa-arrow-up"></i></div>
                  <div class="kk" data-k="\u001b[B"><i class="fas fa-arrow-down"></i></div>
                  <div class="kk" data-k="\u001b[D"><i class="fas fa-arrow-left"></i></div>
                  <div class="kk" data-k="\u001b[C"><i class="fas fa-arrow-right"></i></div>
                  <div class="kk" data-k="\t">Tab</div>
                  <div class="kk" data-k="\u001b">Esc</div>
                  <div class="kk" data-k="\r">Enter</div>
                  <div class="kk" data-k="\u007f">Bksp</div>
                  <div class="kk" data-k="\u0003">^C</div>
                  <div class="kk" data-k="\u0004">^D</div>
                  <div class="kk" data-k="\u000c">^L</div>
                  <div class="kk" data-k="\u001a">^Z</div>
                  <div class="kk" data-k="\u0017">^W</div>
                  <div class="kk" data-k="\u000f">^O</div>
                  <div class="kk" data-k="\u0018">^X</div>
                  <div class="kk" data-k="\u0013">^S</div>
                  <div class="kk" data-k="\u0001">^A</div>
                  <div class="kk" data-k="\u0005">^E</div>
                  <div class="kk" data-k="\u000b">^K</div>
                  <div class="kk" data-k="\u0015">^U</div>
                  <div class="kk" data-k="\u0012">^R</div>
                  <div class="kk" data-k="\u0019">^Y</div>
                  <div class="kk wide" data-k="\u001b[1;5C">Ctrl→</div>
                  <div class="kk wide" data-k="\u001b[1;5D">Ctrl←</div>
                  <div class="kk" data-k="|">|</div>
                  <div class="kk" data-k="~">~</div>
                  <div class="kk" data-k="/">/</div>
                  <div class="kk" data-k="-">-</div>
                  <div class="kk" data-k="_">_</div>
                  <div class="kk" data-k=".">.</div>
                  <div class="kk" data-k=";">;</div>
                  <div class="kk" data-k=":">:</div>
                  <div class="kk" data-k="&">&amp;</div>
                  <div class="kk" data-k="*">*</div>
                  <div class="kk" data-k="(">(</div>
                  <div class="kk" data-k=")">)</div>
                  <div class="kk" data-k="[">[</div>
                  <div class="kk" data-k="]">]</div>
                  <div class="kk" data-k="{">{</div>
                  <div class="kk" data-k="}">}</div>
                  <div class="kk" data-k="&quot;">"</div>
                  <div class="kk" data-k="'">'</div>
                  <div class="kk" data-k="$">$</div>
                  <div class="kk" data-k="#">#</div>
                  <div class="kk" data-k="!">!</div>
                  <div class="kk" data-k="=">=</div>
                  <div class="kk" data-k="+">+</div>
                  <div class="kk" data-k="@">@</div>
                  <div class="kk" data-k="%">%</div>
                  <div class="kk" data-k="^">^</div>
                  <div class="kk" data-k=",">,</div>
                  <div class="kk" data-k="&lt;">&lt;</div>
                  <div class="kk" data-k="&gt;">&gt;</div>
                  <div class="kk" data-k="?">?</div>
                </div>
                <div class="kb-nav">
                  <button id="kb-up" title="Scroll up"><i class="fas fa-chevron-up"></i></button>
                  <button id="kb-down" title="Scroll down"><i class="fas fa-chevron-down"></i></button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div id="status">
        <span class="it"><span class="d saved" id="st-dot"></span><span id="st-label">Ready</span></span>
        <span class="it" id="st-term" style="display:none"><span class="d term"></span>terminal</span>
        <span class="s"></span>
        <span class="it" id="st-viewers"></span>
        <span class="it" id="st-path">workspace</span>
        <span class="it" id="st-lang">plaintext</span>
        <span class="it" id="st-cursor">Ln 1, Col 1</span>
      </div>
    </div>
  </div>
</div>
<div id="ctx">
  <div class="mi" data-a="open"><i class="fas fa-eye"></i> Open</div>
  <div class="mi" data-a="new-file"><i class="fas fa-file"></i> New File</div>
  <div class="mi" data-a="new-folder"><i class="fas fa-folder"></i> New Folder</div>
  <div class="mi" data-a="run"><i class="fas fa-play"></i> Run in Terminal</div>
  <div class="mi" data-a="download"><i class="fas fa-download"></i> Download</div>
  <div class="div"></div>
  <div class="mi" data-a="rename"><i class="fas fa-pen"></i> Rename</div>
  <div class="mi danger" data-a="delete"><i class="fas fa-trash"></i> Delete</div>
</div>
<div id="modal">
  <div id="mbox">
    <h3 id="m-title"><i class="fas fa-pen"></i> Input</h3>
    <p id="m-desc">Enter value</p>
    <input id="m-input" type="text" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" />
    <div class="acts">
      <button class="btn-c" id="m-cancel">Cancel</button>
      <button class="btn-k" id="m-ok">OK</button>
    </div>
  </div>
</div>
<div id="toast"></div>
<div id="loading"><div class="spinner"></div><div class="lbl" id="ld-lbl">Loading…</div></div>
<input type="file" id="file-in" multiple style="display:none" />
<input type="file" id="folder-in" webkitdirectory multiple style="display:none" />

<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
<script>
  var require = { paths: { 'vs': 'https://cdn.jsdelivr.net/npm/monaco-editor@0.39.0/min/vs' } };
</script>
<script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.39.0/min/vs/loader.js"></script>
<script src="/lynkio/client.js"></script>

<script>
function setVH(){ document.documentElement.style.setProperty('--vh', (window.innerHeight*0.01)+'px'); }
setVH();
window.addEventListener('resize', setVH);
window.addEventListener('orientationchange', ()=>setTimeout(setVH, 100));
if(window.visualViewport){ window.visualViewport.addEventListener('resize', setVH); }

function genUserId(){
  return 'u_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
}
const USER_ID = (function(){
  let uid = localStorage.getItem('cf_user_id');
  if(!uid){ uid = genUserId(); localStorage.setItem('cf_user_id', uid); }
  return uid;
})();

const S = {
  tree: [], map: new Map(), files: [], active: null,
  ctx: null, modalRes: null, termOpen: false, keyBarOpen: false,
  currentFolder: '',
  username: (localStorage.getItem('cf_username') || 'user'),
  user_id: USER_ID,
  viewers: {},
  terms: new Map(),
  activeTermSid: null,
  termCounter: 0,
};
let monaco, editor, editorModel;
let mainClient = null;
let termFocused = false;
let suppressOverlayUntil = 0;
const $ = (id) => document.getElementById(id);
const isMobile = () => window.matchMedia('(max-width: 820px)').matches;
const isTouch = () => 'ontouchstart' in window || navigator.maxTouchPoints > 0;

function normalizePath(p){
  if(p == null) return "";
  if(typeof p !== "string") return "";
  p = p.replace(/\\/g, "/").trim();
  p = p.replace(/[\x00-\x08\x0a-\x1f\x7f]/g, "");
  p = p.replace(/^\/+/, "");
  const parts = [];
  for(const seg of p.split("/")){
    if(seg === "" || seg === ".") continue;
    if(seg === ".."){ if(parts.length) parts.pop(); continue; }
    parts.push(seg);
  }
  return parts.join("/");
}
const ext = (n) => { const i = n.lastIndexOf('.'); return i > 0 ? n.slice(i+1).toLowerCase() : ''; };
const base = (p) => p.split('/').pop();
const parent = (p) => { const i = p.lastIndexOf('/'); return i > 0 ? p.slice(0, i) : ''; };
const join = (...a) => a.filter(Boolean).join('/');
const initial = (name) => (name || '?').trim().charAt(0).toUpperCase() || '?';

function langOf(e){
  return ({js:'javascript',ts:'typescript',py:'python',rb:'ruby',go:'go',rs:'rust',
    c:'c',cpp:'cpp',java:'java',cs:'csharp',php:'php',html:'html',css:'css',scss:'scss',
    json:'json',yaml:'yaml',yml:'yaml',md:'markdown',sh:'shell',bash:'shell',sql:'sql',
    txt:'plaintext',log:'plaintext'})[e] || 'plaintext';
}
function iconOf(name, isDir){
  if(isDir) return 'fa-folder';
  const e = ext(name);
  return ({js:'fa-brands fa-js',ts:'fa-brands fa-js',py:'fa-brands fa-python',
    html:'fa-brands fa-html5',css:'fa-brands fa-css3-alt',json:'fa-code',
    md:'fa-file-alt',sh:'fa-terminal',sql:'fa-database'})[e] || 'fa-file';
}
function toast(msg, type='info'){
  const t = $('toast');
  const ic = type==='success'?'fa-check-circle':type==='error'?'fa-exclamation-circle':'fa-info-circle';
  t.innerHTML = '<i class="fas '+ic+'"></i> '+msg;
  t.className = 'show ' + type;
  clearTimeout(t._t);
  t._t = setTimeout(()=>t.className='', 2600);
}
function loading(lbl){ $('ld-lbl').textContent = lbl||'Loading…'; $('loading').classList.add('show'); }
function unloading(){ $('loading').classList.remove('show'); }
function modal(title, desc, val){
  return new Promise(r=>{
    $('m-title').innerHTML = '<i class="fas fa-pen"></i> ' + title;
    $('m-desc').textContent = desc;
    $('m-input').value = val || '';
    $('modal').classList.add('show');
    setTimeout(()=>{ $('m-input').focus(); $('m-input').select(); }, 100);
    S.modalRes = r;
  });
}
$('m-cancel').onclick = ()=>{ $('modal').classList.remove('show'); if(S.modalRes){S.modalRes(null);S.modalRes=null;} };
$('m-ok').onclick = ()=>{ const v=$('m-input').value.trim(); $('modal').classList.remove('show'); if(S.modalRes){S.modalRes(v||null);S.modalRes=null;} };
$('m-input').onkeydown = e=>{ if(e.key==='Enter') $('m-ok').click(); if(e.key==='Escape') $('m-cancel').click(); };

async function api(method, url, body){
  const opts = { method, headers: { 'X-User-Id': S.user_id } };
  if(body instanceof FormData){ opts.body = body; }
  else if(body){ opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  if(!r.ok){
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.message || msg; } catch(e){}
    throw new Error(msg);
  }
  return r.json();
}
const fsTree = () => api('GET','/files');
const fsRead = (path) => api('GET','/file/content?path='+encodeURIComponent(path));
const fsSave = (path,content) => api('POST','/file/save',{path,content});
const fsNewFile = (path) => api('POST','/file/create',{path});
const fsNewFolder = (path) => api('POST','/folder/create',{path});
const fsDelete = (path) => api('POST','/file/delete',{path});
const fsRename = (old,nw) => api('POST','/file/rename',{old,new:nw});

async function syncUser(){
  try{
    const r = await fetch('/api/user', { headers: { 'X-User-Id': S.user_id } });
    const d = await r.json();
    if(d.user_id && d.user_id !== S.user_id){
      S.user_id = d.user_id;
      localStorage.setItem('cf_user_id', d.user_id);
    }
    if(d.username){
      S.username = d.username;
      localStorage.setItem('cf_username', d.username);
    }
    updateTermTitle();
  }catch(e){}
}
async function pushUsername(name){
  try{
    const r = await fetch('/api/user', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-User-Id': S.user_id },
      body: JSON.stringify({ user_id: S.user_id, username: name })
    });
    const d = await r.json();
    if(d.success){
      S.username = d.username;
      localStorage.setItem('cf_username', d.username);
      updateTermTitle();
      if(mainClient){ mainClient.emit('user:rename', { username: d.username }); }
      updatePrompt();
    }
  }catch(e){ toast('Could not save username','error'); }
}
function updateTermTitle(){
  const t = $('term-title');
  if(!t) return;
  const shellName = t.textContent.split(' — ')[0] || 'bash';
  t.textContent = shellName + ' — ' + S.username;
}

function updatePrompt(){
  if(!mainClient || !mainClient.ws || mainClient.ws.readyState !== WebSocket.OPEN) return;
  mainClient.emit('term:set_folder', { folder: S.currentFolder || "" });
  updateTermTitle();
}

function navigateTo(rawPath){
  const norm = normalizePath(rawPath);
  if(!norm){ S.currentFolder = ""; }
  else {
    const node = S.map.get(norm);
    if(node && node.type === "folder"){ S.currentFolder = norm; }
    else { S.currentFolder = ""; }
  }
  renderTree();
  renderBreadcrumb();
}

function validateCurrentFolder(){
  if(!S.currentFolder) return;
  const norm = normalizePath(S.currentFolder);
  if(norm !== S.currentFolder){ S.currentFolder = norm; }
  if(!S.currentFolder) return;
  const node = S.map.get(S.currentFolder);
  if(node && node.type === "folder") return;
  let p = S.currentFolder;
  while(p){
    p = p.substring(0, p.lastIndexOf("/"));
    if(!p){ S.currentFolder = ""; return; }
    const n = S.map.get(p);
    if(n && n.type === "folder"){ S.currentFolder = p; return; }
  }
  S.currentFolder = "";
}

async function loadTree(){
  try{
    const d = await fsTree();
    S.tree = d.tree;
    S.map.clear();
    (function walk(nodes){
      for(const n of nodes){
        S.map.set(n.path, n);
        if(n.children) walk(n.children);
      }
    })(S.tree);
    validateCurrentFolder();
    renderTree();
    renderBreadcrumb();
  }catch(e){ toast('Tree: '+e.message,'error'); }
}
function viewersForPath(path){
  const v = S.viewers[path] || [];
  return v.filter(x => x.user_id !== S.user_id);
}
function renderTree(){
  const root = $('file-tree');
  const roots = S.tree.filter(n=>!n.parent);
  if(!roots.length){
    root.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-3);font-size:12px"><i class="fas fa-folder-open" style="font-size:22px;display:block;margin-bottom:8px"></i>No files</div>';
    return;
  }
  root.innerHTML = roots.map(n=>renderNode(n,0)).join('');
  bindTree();
}
function renderNode(node, depth){
  const dir = node.type === 'folder';
  const icon = iconOf(node.name, dir);
  const chev = dir ? '<span class="chev"><i class="fas fa-chevron-right"></i></span>' : '<span class="chev"></span>';
  const active = S.active === node.path ? 'active' : '';
  const current = (dir && S.currentFolder === node.path) ? 'current' : '';
  const others = viewersForPath(node.path);
  const avatars = others.length
    ? '<span class="mini-av">' + others.slice(0,3).map(v =>
        '<span class="av" title="'+v.username+'">'+initial(v.username)+'</span>').join('') + '</span>'
    : '';
  const kids = dir && node.children && node.children.length ?
    '<div class="ft-kids">'+node.children.map(c=>renderNode(c,depth+1)).join('')+'</div>' : '';
  return '<div class="ft-item '+active+' '+current+'" data-p="'+node.path+'" data-t="'+node.type+'" style="padding-left:'+(6+depth*12)+'px">'+chev+'<span class="ic '+(dir?'folder':'file')+'"><i class="fas '+icon+'"></i></span><span class="lbl">'+node.name+'</span>'+avatars+'</div>'+kids;
}
function bindTree(){
  const root = $('file-tree');
  root.querySelectorAll('.ft-item').forEach(el=>{
    el.onclick = e=>{
      if(e.target.closest('.chev')){ e.stopPropagation(); toggleNode(el.dataset.p, el); return; }
      const node = S.map.get(el.dataset.p);
      if(!node) return;
      if(node.type==='folder'){
        navigateTo(node.path);
        toggleNode(el.dataset.p, el);
      } else {
        openFile(el.dataset.p);
        if(isMobile()) closeSidebar();
      }
    };
    el.oncontextmenu = e=>{
      e.preventDefault();
      const node = S.map.get(el.dataset.p);
      if(!node) return;
      showCtx(e.clientX, e.clientY, node);
    };
    let tmr = null, moved = false;
    el.addEventListener('touchstart', (e)=>{
      moved = false;
      tmr = setTimeout(()=>{
        if(moved) return;
        const touch = e.touches[0] || e.changedTouches[0];
        const node = S.map.get(el.dataset.p);
        if(node){
          if(navigator.vibrate) navigator.vibrate(15);
          showCtx(touch.clientX, touch.clientY, node);
        }
        tmr = null;
      }, 500);
    }, {passive:true});
    el.addEventListener('touchmove', ()=>{ moved = true; if(tmr){ clearTimeout(tmr); tmr = null; } }, {passive:true});
    el.addEventListener('touchend', ()=>{ if(tmr){ clearTimeout(tmr); tmr = null; } }, {passive:true});
    el.addEventListener('touchcancel', ()=>{ if(tmr){ clearTimeout(tmr); tmr = null; } }, {passive:true});
  });
}
function toggleNode(path, el){
  const node = S.map.get(path);
  if(!node || node.type!=='folder') return;
  const chev = el.querySelector('.chev');
  const kids = el.nextElementSibling;
  if(kids && kids.classList.contains('ft-kids')){
    kids.classList.toggle('collapsed');
    if(chev) chev.classList.toggle('open');
  } else {
    if(chev) chev.classList.toggle('open');
  }
}
function openSidebar(){ $('sidebar').classList.add('open'); $('sb-backdrop').classList.add('show'); }
function closeSidebar(){ $('sidebar').classList.remove('open'); $('sb-backdrop').classList.remove('show'); }

function renderBreadcrumb(){
  const bc = $('breadcrumb');
  const parts = S.currentFolder ? S.currentFolder.split('/') : [];
  let html = '<span class="crumb-root" data-path=""><i class="fas fa-folder-tree"></i> workspace</span>';
  let acc = '';
  for(let i=0; i<parts.length; i++){
    acc = acc ? acc + '/' + parts[i] : parts[i];
    const isLast = i === parts.length - 1;
    html += '<span class="sep">/</span>';
    html += '<span class="crumb '+(isLast?'current':'')+'" data-path="'+acc+'"><i class="fas fa-folder"></i>'+parts[i]+'</span>';
  }
  const label = parts.length ? parts[parts.length-1] : 'workspace';
  html += '<span class="dl-current" id="dl-current"><i class="fas fa-download"></i> <span>Download '+label+'</span></span>';
  bc.innerHTML = html;
  bc.querySelectorAll('.crumb-root, .crumb').forEach(el=>{
    el.onclick = ()=>{ navigateTo(el.dataset.path || ''); };
  });
  const dl = bc.querySelector('#dl-current');
  if(dl) dl.onclick = downloadCurrent;
  $('st-path').textContent = S.currentFolder || 'workspace';
  updatePrompt();
}

function downloadCurrent(){
  let path = '';
  if (S.active) { const n = S.map.get(S.active); path = n ? S.active : ''; }
  else if (S.currentFolder) { path = S.currentFolder; }
  path = normalizePath(path);
  const url = path ? '/download?path=' + encodeURIComponent(path) : '/download';
  window.location.href = url;
  toast('Downloading ' + (path ? base(path) : 'workspace') + ' …', 'info');
}
function download(path){
  path = normalizePath(path);
  const url = path ? '/download?path=' + encodeURIComponent(path) : '/download';
  window.location.href = url;
  toast('Downloading ' + (path ? base(path) : 'workspace') + ' …', 'info');
}

function showCtx(x, y, node){
  S.ctx = node;
  const m = $('ctx');
  m.style.display = 'block';
  const w = m.offsetWidth || 200, h = m.offsetHeight || 320;
  m.style.left = Math.max(8, Math.min(x, window.innerWidth - w - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(y, window.innerHeight - h - 8)) + 'px';
}
document.addEventListener('click', ()=>$('ctx').style.display='none');
document.addEventListener('contextmenu', (e)=>{ if(!e.target.closest('.ft-item')) $('ctx').style.display='none'; });
$('ctx').querySelectorAll('.mi').forEach(el=>{
  el.onclick = async ()=>{
    const a = el.dataset.a;
    const node = S.ctx;
    $('ctx').style.display = 'none';
    if(!node) return;
    const isDir = node.type === 'folder';
    const parentDir = isDir ? node.path : parent(node.path);
    switch(a){
      case 'open': if(!isDir) openFile(node.path); else { navigateTo(node.path); } break;
      case 'new-file': await doNewFile(parentDir); break;
      case 'new-folder': await doNewFolder(parentDir); break;
      case 'run': await runInTerminal(node.path); break;
      case 'download': download(node.path); break;
      case 'rename': await doRename(node); break;
      case 'delete': await doDelete(node); break;
    }
  };
});

async function doNewFile(parentDir){
  const name = await modal('New File','Enter file name:','file.py');
  if(!name) return;
  const safeName = name.replace(/[/\\]/g, "").trim();
  if(!safeName) return;
  const path = parentDir ? join(parentDir, safeName) : safeName;
  try{
    const r = await fsNewFile(path);
    await loadTree();
    await openFile(r.path);
    toast('Created '+safeName,'success');
  }catch(e){ toast(e.message,'error'); }
}
async function doNewFolder(parentDir){
  const name = await modal('New Folder','Enter folder name:','new-folder');
  if(!name) return;
  const safeName = name.replace(/[/\\]/g, "").trim();
  if(!safeName) return;
  const path = parentDir ? join(parentDir, safeName) : safeName;
  try{
    await fsNewFolder(path);
    await loadTree();
    toast('Created '+safeName,'success');
  }catch(e){ toast(e.message,'error'); }
}
async function doRename(node){
  const nn = await modal('Rename','Rename "'+node.name+'":', node.name);
  if(!nn || nn === node.name) return;
  const safeName = nn.replace(/[/\\]/g, "").trim();
  if(!safeName) return;
  const parentDir = parent(node.path);
  const np = parentDir ? join(parentDir, safeName) : safeName;
  try{
    const r = await fsRename(node.path, np);
    S.files.forEach(f=>{
      if(f.path === node.path) f.path = r.path;
      else if(f.path.startsWith(node.path + '/')) f.path = r.path + f.path.slice(node.path.length);
    });
    if(S.active === node.path) S.active = r.path;
    if(S.currentFolder === node.path) S.currentFolder = r.path;
    await loadTree(); renderTabs();
    toast('Renamed to '+safeName,'success');
  }catch(e){ toast(e.message,'error'); }
}
async function doDelete(node){
  if(!confirm('Delete "'+node.name+'"'+(node.type==='folder'?' and all contents':'')+'?')) return;
  try{
    await fsDelete(node.path);
    S.files = S.files.filter(f=>{
      const inside = f.path === node.path || f.path.startsWith(node.path + '/');
      if(inside && f._model) f._model.dispose();
      return !inside;
    });
    if(S.currentFolder === node.path || S.currentFolder.startsWith(node.path + '/')){
      S.currentFolder = parent(node.path);
    }
    if(S.active && (S.active === node.path || S.active.startsWith(node.path+'/'))){
      S.active = S.files.length ? S.files[S.files.length-1].path : null;
      if(!S.active){ if(editor) editor.setModel(null); $('empty-state').style.display='flex'; }
    }
    await loadTree(); renderTabs(); updateStatus();
    toast('Deleted '+node.name,'success');
  }catch(e){ toast(e.message,'error'); }
}

async function openFile(path){
  path = normalizePath(path);
  if(!path) return;
  const existing = S.files.find(f=>f.path===path);
  if(existing){ S.active = path; focusEditor(path); renderTabs(); updateStatus(); attachRoom(path); return; }
  loading('Opening…');
  try{
    const r = await fsRead(path);
    unloading();
    if(r.isBinary){ toast('Binary file cannot be opened','error'); return; }
    const f = { path, content: r.content||'', dirty:false, lang: langOf(ext(path)), _model:null, _lastLocalEdit: 0 };
    S.files.push(f);
    S.active = path;
    $('empty-state').style.display = 'none';
    focusEditor(path);
    renderTabs();
    updateStatus();
    attachRoom(path);
  }catch(e){ unloading(); toast(e.message,'error'); }
}
function attachRoom(path){
  if(mainClient && mainClient.ws && mainClient.ws.readyState === WebSocket.OPEN){
    mainClient.emit('file:open', { path });
  }
}
function focusEditor(path){
  if(!monaco || !editor) return;
  const f = S.files.find(x=>x.path===path);
  if(!f) return;
  if(!f._model){
    const uri = monaco.Uri.file(path);
    f._model = monaco.editor.createModel(f.content, f.lang, uri);
  }
  editor.setModel(f._model);
  editorModel = f._model;
  if(f._model.getLanguageId() !== f.lang) monaco.editor.setModelLanguage(f._model, f.lang);
  updateStatus();
}
function renderTabs(){
  const c = $('tabs');
  if(!S.files.length){ c.innerHTML=''; return; }
  c.innerHTML = S.files.map(f=>{
    const name = base(f.path);
    const ico = iconOf(name, false);
    const dirty = f.dirty ? '<i class="fas fa-circle" style="font-size:5px;color:var(--accent)"></i>' : '';
    const others = viewersForPath(f.path);
    const avs = others.length
      ? '<span class="viewers">' + others.slice(0,3).map(v =>
          '<span class="av" title="'+v.username+'">'+initial(v.username)+'</span>').join('') + '</span>'
      : '';
    return '<div class="tab '+(S.active===f.path?'active':'')+'" data-p="'+f.path+'"><i class="fas '+ico+' ic"></i><span>'+name+'</span>'+dirty+avs+'<span class="close" data-p="'+f.path+'"><i class="fas fa-times"></i></span></div>';
  }).join('');
  c.querySelectorAll('.tab').forEach(el=>{
    el.onclick = e=>{
      if(e.target.closest('.close')) return;
      S.active = el.dataset.p;
      focusEditor(S.active); renderTabs(); updateStatus();
      attachRoom(S.active);
    };
  });
  c.querySelectorAll('.close').forEach(el=>{
    el.onclick = e=>{
      e.stopPropagation();
      const p = el.dataset.p;
      const i = S.files.findIndex(f=>f.path===p);
      if(i<0) return;
      const f = S.files[i];
      if(f.dirty && !confirm('"'+base(f.path)+'" has unsaved changes. Close anyway?')) return;
      if(mainClient && mainClient.ws && mainClient.ws.readyState === WebSocket.OPEN){
        mainClient.emit('file:close', { path: p });
      }
      if(f._model) f._model.dispose();
      S.files.splice(i,1);
      if(S.active===p){
        S.active = S.files.length ? S.files[S.files.length-1].path : null;
        if(S.active) focusEditor(S.active);
        else { if(editor) editor.setModel(null); $('empty-state').style.display='flex'; }
      }
      renderTabs(); updateStatus();
    };
  });
}
function updateStatus(){
  const dot = $('st-dot'), lbl = $('st-label'), lang = $('st-lang'), viewersEl = $('st-viewers');
  const f = S.files.find(x=>x.path===S.active);
  if(f){
    dot.className = 'd ' + (f.dirty ? 'dirty' : 'saved');
    lbl.textContent = f.dirty ? 'Unsaved' : 'Saved';
    lang.textContent = f.lang || 'plaintext';
    const others = viewersForPath(f.path);
    if(others.length){
      viewersEl.className = 'it viewers-status';
      viewersEl.innerHTML = '<i class="fas fa-users"></i> ' + others.slice(0,4).map(v =>
        '<span class="av" title="'+v.username+'">'+initial(v.username)+'</span>').join('') +
        ' <span style="margin-left:4px">' + others.length + ' editing</span>';
    } else {
      viewersEl.className = '';
      viewersEl.innerHTML = '';
    }
  } else {
    dot.className = 'd saved';
    lbl.textContent = 'Ready';
    lang.textContent = 'plaintext';
    viewersEl.className = '';
    viewersEl.innerHTML = '';
  }
}
async function saveFile(path){
  const f = S.files.find(x=>x.path===path);
  if(!f || !f.dirty) return;
  f.dirty = false;
  renderTabs(); updateStatus();
  if(mainClient && mainClient.ws && mainClient.ws.readyState === WebSocket.OPEN){
    mainClient.emit('file:save', { path: f.path, content: f.content });
  } else {
    try { await fsSave(f.path, f.content); }
    catch(e){ f.dirty = true; renderTabs(); updateStatus(); toast(e.message,'error'); }
  }
}
function initMonaco(){
  return new Promise(res=>{
    require(['vs/editor/editor.main'], m=>{
      monaco = m;
      const mobile = isMobile();
      editor = monaco.editor.create($('editor-container'), {
        value:'', language:'plaintext', theme:'vs-dark', automaticLayout:true,
        fontSize: mobile ? 13 : 13.5,
        fontFamily:'JetBrains Mono, Ubuntu Mono, monospace',
        minimap:{enabled:false},
        tabSize:2, insertSpaces:true,
        wordWrap: mobile ? 'on' : 'off',
        padding: { top: 8, bottom: 8 },
      });
      editor.onDidChangeModelContent(()=>{
        if(!editorModel) return;
        const f = S.files.find(x=>x.path===S.active);
        if(!f) return;
        const v = editorModel.getValue();
        if(f.content !== v){
          f.content = v;
          f.dirty = true;
          f._lastLocalEdit = Date.now();
          renderTabs(); updateStatus();
          clearTimeout(f._t);
          f._t = setTimeout(()=>saveFile(f.path), 900);
        }
      });
      editor.onDidChangeCursorPosition(e=>{
        $('st-cursor').textContent = 'Ln '+e.position.lineNumber+', Col '+e.position.column;
      });
      editor.addCommand(monaco.KeyMod.CtrlCmd|monaco.KeyCode.KeyS, ()=>saveFile(S.active));
      res();
    }, err=>{ res(); });
  });
}

function applyRemoteUpdate(path, content, fromUser, username){
  const f = S.files.find(x => x.path === path);
  if(!f) return;
  const now = Date.now();
  const recentEdit = (now - (f._lastLocalEdit || 0)) < 2000;
  if(recentEdit){ toast(username + ' is also editing ' + base(path), 'info'); return; }
  if(f._model){
    try{
      const cursor = editor && editor.getPosition ? editor.getPosition() : null;
      const scrollTop = editor ? editor.getScrollTop() : 0;
      f._model.setValue(content);
      if(cursor && editor){ try { editor.setPosition(cursor); } catch(e){} }
      if(editor){ editor.setScrollTop(scrollTop); }
    }catch(e){ try { f._model.setValue(content); } catch(e2){} }
  }
  f.content = content;
  f.dirty = false;
  renderTabs(); updateStatus();
  toast(username + ' updated ' + base(path), 'info');
}

/* ==================================================================
   TERMINAL — multi-session (client side)
================================================================== */
function emitTerm(event, data, sid){
  if(!mainClient || !mainClient.ws || mainClient.ws.readyState !== WebSocket.OPEN) return;
  const payload = Object.assign({}, data || {});
  const useSid = sid || S.activeTermSid;
  if(useSid) payload.session_id = useSid;
  mainClient.emit(event, payload);
}
function activeTerm(){
  if(!S.activeTermSid) return null;
  return S.terms.get(S.activeTermSid) || null;
}
function createTerminal(name, opts){
  opts = opts || {};
  const sid = opts.sid || ('t' + (++S.termCounter));
  const wrapper = document.createElement('div');
  wrapper.className = 'term-wrapper';
  wrapper.dataset.sid = sid;
  $('terminal-stack').appendChild(wrapper);

  const mobile = isMobile();
  const t = new Terminal({
    fontFamily: "'Ubuntu Mono', 'JetBrains Mono', monospace",
    fontSize: mobile ? 13 : 14,
    lineHeight: 1.25,
    cursorBlink: true,
    cursorStyle: 'block',
    theme: {
      background: '#2c001e', foreground: '#ffffff', cursor: '#ffffff',
      cursorAccent: '#2c001e', selection: 'rgba(255,255,255,0.25)',
      black: '#2c001e', red: '#cc0000', green: '#4e9a06', yellow: '#c4a000',
      blue: '#3465a4', magenta: '#75507b', cyan: '#06989a', white: '#d3d7cf',
      brightBlack: '#555753', brightRed: '#ef2929', brightGreen: '#8ae234',
      brightYellow: '#fce94f', brightBlue: '#729fcf', brightMagenta: '#ad7fa8',
      brightCyan: '#34e2e2', brightWhite: '#eeeeec',
    },
    scrollback: 5000,
    convertEol: false,
  });
  const fit = new FitAddon.FitAddon();
  t.loadAddon(fit);
  t.open(wrapper);

  const info = { sid, term: t, fit, wrapper, name: name || ('bash ' + sid), ready: false };
  S.terms.set(sid, info);

  t.onData(data=>{ if(info.ready) emitTerm('term:input', { text: data }, sid); });
  t.onResize(()=>{ if(info.ready) emitTerm('term:resize', { rows: t.rows, cols: t.cols }, sid); });

  if(t.textarea){
    t.textarea.addEventListener('focus', ()=>{
      termFocused = true;
      if(S.activeTermSid === sid) $('tap-to-type').classList.remove('show');
    });
    t.textarea.addEventListener('blur', ()=>{
      termFocused = false;
      if(isTouch() && S.activeTermSid === sid && Date.now() > suppressOverlayUntil){
        $('tap-to-type').classList.add('show');
      }
    });
  }

  setTimeout(()=>{
    try { fit.fit(); } catch(e){}
    emitTerm('term:start', { rows: t.rows, cols: t.cols }, sid);
  }, 30);

  if(!opts.noSwitch) switchTerminal(sid);
  renderTermTabs();
  return sid;
}
function switchTerminal(sid){
  const info = S.terms.get(sid);
  if(!info) return;
  S.activeTermSid = sid;
  document.querySelectorAll('.term-wrapper').forEach(w => {
    w.classList.toggle('active', w.dataset.sid === sid);
  });
  setTimeout(()=>{
    try { info.fit.fit(); } catch(e){}
    if(info.ready) emitTerm('term:resize', { rows: info.term.rows, cols: info.term.cols }, sid);
    if(!isTouch()) info.term.focus();
  }, 50);
  $('st-term').style.display = info.ready ? 'flex' : 'none';
  updateTermTitle();
  renderTermTabs();
}
function closeTerminal(sid){
  const info = S.terms.get(sid);
  if(!info) return;
  emitTerm('term:close', {}, sid);
  try { info.term.dispose(); } catch(e){}
  try { info.wrapper.remove(); } catch(e){}
  S.terms.delete(sid);
  if(S.activeTermSid === sid){
    const remaining = Array.from(S.terms.keys());
    if(remaining.length) switchTerminal(remaining[0]);
    else { S.activeTermSid = null; createTerminal('bash'); }
  } else renderTermTabs();
}
function restartTerminal(sid){
  const info = S.terms.get(sid);
  if(!info) return;
  info.ready = false;
  if(S.activeTermSid === sid) $('st-term').style.display = 'none';
  try { info.term.reset(); info.term.clear(); } catch(e){}
  try { info.term.write('\x1b[33m[restarting session…]\x1b[0m\r\n'); } catch(e){}
  if(mainClient && mainClient.ws && mainClient.ws.readyState === WebSocket.OPEN){
    emitTerm('term:restart', { rows: info.term.rows, cols: info.term.cols }, sid);
  }
}
function renderTermTabs(){
  const c = $('term-tabs');
  if(!c) return;
  const sids = Array.from(S.terms.keys());
  c.innerHTML = sids.map(sid => {
    const t = S.terms.get(sid);
    const active = sid === S.activeTermSid ? 'active' : '';
    return '<div class="term-tab ' + active + '" data-sid="' + sid + '">' +
      '<i class="fas fa-terminal ti"></i>' +
      '<span>' + (t.name || 'bash') + '</span>' +
      '<span class="term-tab-close" data-sid="' + sid + '"><i class="fas fa-times"></i></span>' +
      '</div>';
  }).join('');
  c.querySelectorAll('.term-tab').forEach(el => {
    el.onclick = (e) => { if(e.target.closest('.term-tab-close')) return; switchTerminal(el.dataset.sid); };
  });
  c.querySelectorAll('.term-tab-close').forEach(el => {
    el.onclick = (e) => { e.stopPropagation(); closeTerminal(el.dataset.sid); };
  });
}
function initTerminalStack(){ createTerminal('bash'); }

function toggleTerminal(force){
  const pane = $('term-pane');
  const open = force !== undefined ? force : !S.termOpen;
  S.termOpen = open;
  pane.classList.toggle('open', open);
  if(open){
    setTimeout(()=>{
      const info = activeTerm();
      if(info){ try { info.fit.fit(); } catch(e){} }
      if(info && info.ready) emitTerm('term:resize', { rows: info.term.rows, cols: info.term.cols }, info.sid);
      if(isTouch()){
        const a = activeTerm();
        if(a && (!a.ready || !termFocused)) $('tap-to-type').classList.add('show');
        $('key-bar-wrap').classList.add('open'); S.keyBarOpen = true;
        updateKeyBarArrows();
      } else {
        const a = activeTerm(); if(a) a.term.focus();
      }
    }, 260);
  }
}
function toggleKeyBar(force){
  const kb = $('key-bar-wrap');
  const open = force !== undefined ? force : !S.keyBarOpen;
  S.keyBarOpen = open;
  kb.classList.toggle('open', open);
  setTimeout(()=>{ const a = activeTerm(); if(a){ try { a.fit.fit(); }catch(e){} } updateKeyBarArrows(); }, 150);
}
function shellQuote(s){ return "'" + String(s || "").replace(/'/g, "'\\''") + "'"; }
async function runInTerminal(filePath){
  filePath = normalizePath(filePath);
  if(!filePath) return;
  const dir = parent(filePath);
  const name = base(filePath);
  const node = S.map.get(filePath);
  if(!node || node.type !== "file"){ toast('Cannot run: file no longer exists','error'); return; }
  toggleTerminal(true);
  const cmd = dir
    ? 'cd ' + shellQuote(dir) + ' && python ' + shellQuote(name)
    : 'python ' + shellQuote(name);
  if(!activeTerm() || !activeTerm().ready){
    if(!S.terms.size) createTerminal('bash');
  }
  setTimeout(()=>{
    emitTerm('term:input', { text: cmd + '\n' });
    const a = activeTerm(); if(a && !isTouch()) a.term.focus();
  }, 400);
}
async function changeUsername(){
  const nn = await modal('Change username', 'Enter your username (shown in the prompt):', S.username);
  if(!nn) return;
  await pushUsername(nn);
  toast('Username set to ' + S.username, 'success');
}

/* ==================================================================
   QUICK-KEY BAR
================================================================== */
function setupQuickKeys(){
  document.querySelectorAll('#key-bar .kk').forEach(k=>{
    let _startX = 0, _startY = 0, _moved = false, _active = false;
    const sendKey = () => {
      const a = activeTerm();
      if(!a || !a.ready) return;
      const raw = k.dataset.k;
      const text = raw.replace(/\\u([0-9a-fA-F]{4})/g, function(_, h){ return String.fromCharCode(parseInt(h, 16)); });
      const finalText = text.replace(/\\r/g, '\r').replace(/\\t/g, '\t').replace(/\\n/g, '\n');
      emitTerm('term:input', { text: finalText }, a.sid);
      suppressOverlayUntil = Date.now() + 800;
      $('tap-to-type').classList.remove('show');
      setTimeout(()=>{ try{ a.term.focus(); }catch(e){} }, 0);
    };
    k.addEventListener('pointerdown', (e)=>{
      if(e.pointerType === 'mouse' && e.button !== 0) return;
      _active = true; _moved = false;
      _startX = e.clientX; _startY = e.clientY;
      e.stopPropagation();
    }, {passive:true});
    k.addEventListener('pointermove', (e)=>{
      if(!_active) return;
      if(Math.abs(e.clientX - _startX) > 8 || Math.abs(e.clientY - _startY) > 8) _moved = true;
    }, {passive:true});
    k.addEventListener('pointerup', (e)=>{
      const wasActive = _active, moved = _moved;
      _active = false; _moved = false;
      if(!wasActive || moved) return;
      e.stopPropagation();
      sendKey();
    }, {passive:true});
    k.addEventListener('pointercancel', ()=>{ _active = false; _moved = false; }, {passive:true});
    ['touchstart','touchmove','touchend','touchcancel','mousedown','mouseup'].forEach(evt=>{
      k.addEventListener(evt, (e)=>{ e.stopPropagation(); }, {passive:false});
    });
    k.addEventListener('click', (e)=>{ e.preventDefault(); e.stopPropagation(); });
    k.addEventListener('contextmenu', (e)=>{ e.preventDefault(); e.stopPropagation(); });
  });
}
function setupKeyBarScroll(){
  const bar = $('key-bar'); const upBtn = $('kb-up'); const downBtn = $('kb-down');
  if(!bar) return;
  const step = 120;
  if(upBtn) upBtn.addEventListener('click', (e)=>{ e.preventDefault(); e.stopPropagation(); bar.scrollBy({ top: -step, behavior:'smooth' }); });
  if(downBtn) downBtn.addEventListener('click', (e)=>{ e.preventDefault(); e.stopPropagation(); bar.scrollBy({ top: step, behavior:'smooth' }); });
  bar.addEventListener('wheel', (e)=>{
    if(Math.abs(e.deltaY) > Math.abs(e.deltaX) && bar.scrollHeight > bar.clientHeight){
      e.preventDefault(); bar.scrollTop += e.deltaY;
    }
  }, { passive: false });
  bar.addEventListener('touchmove', (e)=>{ e.stopPropagation(); }, { passive:true });
  bar.addEventListener('pointerdown', (e)=>{ e.stopPropagation(); }, { passive:true });
  let raf = null;
  function updateArrows(){
    if(!upBtn || !downBtn) return;
    const atTop = bar.scrollTop <= 2;
    const atBottom = (bar.scrollTop + bar.clientHeight) >= (bar.scrollHeight - 2);
    upBtn.classList.toggle('disabled', atTop); upBtn.disabled = atTop;
    downBtn.classList.toggle('disabled', atBottom); downBtn.disabled = atBottom;
  }
  function throttled(){ if(raf) return; raf = requestAnimationFrame(()=>{ raf = null; updateArrows(); }); }
  bar.addEventListener('scroll', throttled, { passive:true });
  window.addEventListener('resize', throttled);
  if('ResizeObserver' in window){ try{ new ResizeObserver(throttled).observe(bar); }catch(e){} }
  setTimeout(updateArrows, 150); setTimeout(updateArrows, 500);
  window.updateKeyBarArrows = updateArrows;
}
function updateKeyBarArrows(){ if(typeof window.updateKeyBarArrows === 'function') window.updateKeyBarArrows(); }

function uploadFiles(){
  const inp = $('file-in'); inp.value = ''; inp.click();
  inp.onchange = async ()=>{
    if(!inp.files.length) return;
    const target = S.currentFolder || '';
    loading('Uploading…');
    for(const f of inp.files){
      const fd = new FormData();
      fd.append('file', f);
      fd.append('target', target);
      try{ await fetch('/upload', { method:'POST', body:fd, headers: { 'X-User-Id': S.user_id } }); }catch(e){}
    }
    unloading();
    await loadTree();
    toast('Uploaded '+inp.files.length+' file(s)','success');
  };
}

async function startWS(){
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = proto + '//' + location.host + '/';
  if(typeof LynkClient === 'undefined'){
    console.error('LynkClient not loaded — check /lynkio/client.js');
    toast('WebSocket library failed to load','error');
    return;
  }
  if(mainClient){ try{ mainClient.close(); }catch(e){} }
  mainClient = new LynkClient(url);

  mainClient.on('term:output', (payload)=>{
    if(!payload || !payload.data) return;
    const sid = payload.session_id;
    const info = S.terms.get(sid);
    if(!info) return;
    try{
      const bin = atob(payload.data);
      const arr = new Uint8Array(bin.length);
      for(let i=0;i<bin.length;i++) arr[i] = bin.charCodeAt(i);
      info.term.write(arr);
    }catch(e){ console.error('term:output', e); }
  });
  mainClient.on('term:ready', (payload)=>{
    const sid = payload && payload.session_id;
    const info = S.terms.get(sid);
    if(!info) return;
    info.ready = true;
    const shellName = (payload && payload.shell) ? payload.shell.split('/').pop() : 'bash';
    info.name = shellName + ' ' + sid.replace(/^t/, '#');
    if(sid === S.activeTermSid){
      $('st-term').style.display = 'flex';
      $('term-title').textContent = shellName + ' — ' + S.username;
    }
    renderTermTabs();
    if(isTouch() && sid === S.activeTermSid && !termFocused) $('tap-to-type').classList.add('show');
    if(isTouch()){ $('key-bar-wrap').classList.add('open'); S.keyBarOpen = true; }
    setTimeout(()=>{
      try{ info.fit.fit(); }catch(e){}
      emitTerm('term:resize', { rows: info.term.rows, cols: info.term.cols }, sid);
      emitTerm('term:set_folder', { folder: S.currentFolder || "" }, sid);
      updateKeyBarArrows();
    }, 100);
  });
  mainClient.on('term:error', (payload)=>{
    const sid = payload && payload.session_id;
    const info = S.terms.get(sid);
    if(info){ try{ info.term.write('\r\n\x1b[31m[error] ' + (payload && payload.error) + '\x1b[0m\r\n'); }catch(e){} }
  });
  mainClient.on('term:exit', (payload)=>{
    const sid = payload && payload.session_id;
    const info = S.terms.get(sid);
    if(!info) return;
    info.ready = false;
    if(sid === S.activeTermSid) $('st-term').style.display = 'none';
    try{ info.term.write('\r\n\x1b[33m[session ended — restarting…]\x1b[0m\r\n'); }catch(e){}
    setTimeout(()=>{
      if(mainClient && mainClient.ws && mainClient.ws.readyState === WebSocket.OPEN){
        emitTerm('term:restart', { rows: info.term.rows, cols: info.term.cols }, sid);
      }
    }, 500);
  });
  mainClient.on('user:info', (payload)=>{
    if(!payload) return;
    if(payload.username){
      S.username = payload.username;
      localStorage.setItem('cf_username', payload.username);
      updateTermTitle();
      updatePrompt();
    }
  });
  mainClient.on('file:updated', (payload)=>{
    if(!payload) return;
    const { path, content, from_user, username } = payload;
    if(from_user === S.user_id) return;
    applyRemoteUpdate(path, content, from_user, username || 'Someone');
  });
  mainClient.on('file:viewers', (payload)=>{
    if(!payload || !payload.path) return;
    S.viewers[payload.path] = payload.viewers || [];
    renderTree(); renderTabs(); updateStatus();
  });

  try{
    await mainClient.connect();
    mainClient.emit('user:init', { user_id: S.user_id });
    for(const sid of S.terms.keys()){
      const info = S.terms.get(sid);
      try { info.fit.fit(); } catch(e){}
      emitTerm('term:start', { rows: info.term.rows, cols: info.term.cols }, sid);
    }
    for(const f of S.files){ mainClient.emit('file:open', { path: f.path }); }
    setTimeout(updatePrompt, 300);
  }catch(e){ console.error('WS connect failed', e); }
}

document.addEventListener('DOMContentLoaded', async ()=>{
  $('btn-open').onclick = ()=>{ if(isMobile()) openSidebar(); else loadTree(); };
  $('empty-open').onclick = ()=>toggleTerminal(true);
  $('btn-upload').onclick = uploadFiles;
  $('btn-download').onclick = downloadCurrent;
  $('btn-new-file').onclick = ()=>{ doNewFile(S.currentFolder); };
  $('sb-new-file').onclick = ()=>doNewFile(S.currentFolder);
  $('sb-new-folder').onclick = ()=>doNewFolder(S.currentFolder);
  $('sb-refresh').onclick = async ()=>{ await loadTree(); toast('Refreshed','info'); };
  $('btn-term').onclick = ()=>toggleTerminal();
  $('btn-user').onclick = changeUsername;

  $('mobile-toggle').onclick = ()=>openSidebar();
  $('sb-backdrop').onclick = ()=>closeSidebar();

  $('term-close').onclick = ()=>toggleTerminal(false);
  $('term-new').onclick = ()=>{ createTerminal('bash ' + (S.termCounter + 1)); toggleTerminal(true); };
  $('term-clear').onclick = ()=>{ const a = activeTerm(); if(a){ try{ a.term.clear(); }catch(e){} if(termFocused) a.term.focus(); } };
  $('term-keys').onclick = ()=>toggleKeyBar();
  $('term-user').onclick = changeUsername;
  $('term-title-btn').onclick = changeUsername;

  $('term-restart').onclick = ()=>{
    let a = activeTerm();
    if(!a){ createTerminal('bash'); toggleTerminal(true); toast('New terminal created','info'); return; }
    restartTerminal(a.sid);
    toast('Restarting ' + a.name + ' …','info');
  };

  document.addEventListener('keydown', e=>{
    if((e.ctrlKey||e.metaKey) && e.key === '`'){ e.preventDefault(); toggleTerminal(); }
    if((e.ctrlKey||e.metaKey) && e.key === 's'){ e.preventDefault(); saveFile(S.active); }
    if((e.ctrlKey||e.metaKey) && e.key === 'd'){ e.preventDefault(); downloadCurrent(); }
    if((e.ctrlKey||e.metaKey) && e.key === 't'){ e.preventDefault(); createTerminal('bash ' + (S.termCounter + 1)); toggleTerminal(true); }
  });

  setupKeyBarScroll();
  setupQuickKeys();
  await syncUser();
  await initMonaco();
  await loadTree();
  initTerminalStack();

  window.addEventListener('resize', ()=>{ const a = activeTerm(); if(a){ try{ a.fit.fit(); }catch(e){} } });
  window.addEventListener('orientationchange', ()=>setTimeout(()=>{ const a = activeTerm(); if(a){ try{ a.fit.fit(); }catch(e){} } }, 300));
  if(window.visualViewport){
    window.visualViewport.addEventListener('resize', ()=>setTimeout(()=>{
      const a = activeTerm(); if(a){ try{ a.fit.fit(); }catch(e){} }
    }, 120));
  }

  await startWS();

  const tt = $('tap-to-type');
  const focusTerm = ()=>{
    const a = activeTerm();
    if(!a) return;
    try{ a.term.focus(); }catch(e){}
    $('tap-to-type').classList.remove('show');
    setTimeout(()=>{ try{ a.fit.fit(); }catch(e){} }, 200);
  };
  tt.addEventListener('click', focusTerm);
  tt.addEventListener('touchend', (e)=>{ e.preventDefault(); focusTerm(); }, {passive:false});
});
</script>
</body>
</html>
"""

# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  CodeForge Pro — Multi-Terminal (Controlling-TTY Edition)")
    print("=" * 60)
    print(f"  🌐  http://localhost:5000")
    print(f"  📂  Workspace: {WORKSPACE}")
    print(f"  🐍  Python: {PYTHON_PATH}")
    print(f"  📦  Venv: {VENV_ROOT or '(system)'}")
    print(f"  🐚  Shell: {DEFAULT_SHELL}")
    print(f"  ⌨️   ^C now sends SIGINT to foreground job (pty.fork + setsid + CT)")
    print(f"  🔄  Restart walks /proc to kill the whole session, not just bash")
    print(f"  🖥️   Multiple terminals with tabs (Ctrl+T / + button)")
    print(f"  ⚡  Ctrl+C to stop")
    print("=" * 60)
    app.run()