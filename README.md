# CodeForge Pro is a multi-user, browser-based coding environment with a real terminal, file explorer, Monaco editor, and live collaboration.

## CodeForge Pro
Multi-User Terminal + Files IDE
A self-contained web IDE that runs in the browser. It provides:
	•	A real interactive terminal (with proper controlling TTY so Ctrl+C actually stops jobs)
	•	Multiple terminal tabs
	•	Monaco code editor
	•	File explorer with create / rename / delete / upload / download
	•	Real-time multi-user collaboration (see who is editing a file and receive live updates)
	•	Mobile-friendly UI with an on-screen keyboard bar

## Features
Feature
Description
Proper PTY
Uses pty.fork() so the shell has a real controlling terminal. Ctrl+C, Ctrl+Z, job control, etc. work correctly.
Multi-terminal
Open several independent terminals and switch between them with tabs (Ctrl+T or the + button).
Robust restart
Restart walks /proc (Linux) or ps and kills the entire session, not just the shell.
Multi-user
Users get a persistent ID + username. Username appears in the shell prompt.
Live collaboration
When multiple people open the same file you see their avatars and receive real-time content updates.
Workspace isolation
All file operations are confined to the workspace/ directory.
Mobile support
Responsive layout + large on-screen key bar for common control keys.

## Requirements
	•	Python 3.8+
	•	The lynkio package (the framework this app is built on)
	•	Linux or macOS recommended for full terminal features (Windows has a simplified subprocess-based terminal)

# Installation
# Clone or copy the script
# Install the framework
pip install lynkio

# (Optional) Create a virtual environment – the app will also try to create one automatically
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Running
python codeforge.py
You should see something like:
============================================================
  CodeForge Pro — Multi-Terminal (Controlling-TTY Edition)
============================================================
  🌐  http://localhost:5000
  📂  Workspace: /path/to/workspace
  🐍  Python: ...
  📦  Venv: ...
  🐚  Shell: /bin/bash
  ...
============================================================
Open http://localhost:5000 in your browser.
The server listens on 0.0.0.0:5000, so it is reachable from other machines on the network (useful for multi-user sessions).

How to Use
File Explorer (left sidebar)
	•	Click folders to navigate (breadcrumb updates).
	•	Click a file to open it in the editor.
	•	Right-click (or long-press on mobile) for context menu:
	◦	Open / New File / New Folder
	◦	Run in Terminal (runs python )
	◦	Download / Rename / Delete
	•	Toolbar buttons: New file, Upload, Download current folder/file.
Editor
	•	Monaco editor with syntax highlighting for common languages.
	•	Auto-saves ~900 ms after you stop typing.
	•	Live collaboration: other users’ avatars appear on the tab and in the status bar.
	•	Ctrl/Cmd + S forces a save.
Terminal
	•	Click the Term button (or `Ctrl + ``) to open/close the terminal pane.
	•	+ or Ctrl + T → new terminal tab.
	•	Restart button cleanly kills the whole session and starts a fresh shell.
	•	The prompt shows your username and the current folder (synced with the explorer).
	•	On mobile/touch devices a large key bar appears with arrows, Tab, Esc, Enter, ^C, ^D, ^L, etc.
User identity
	•	Click the user icon (or the terminal title) to set a username.
	•	Username is stored and appears in the shell prompt and in collaboration indicators.
Download
	•	Download button or the breadcrumb “Download …” link downloads the current file or the whole workspace as a zip.

Workspace
All files live under a workspace/ directory next to the script. Hidden files (names starting with .) are ignored by the tree. A .users.json file inside the workspace stores user names.

Keyboard Shortcuts
Shortcut
Action
`Ctrl/Cmd + ``
Toggle terminal
Ctrl/Cmd + T
New terminal
Ctrl/Cmd + S
Save current file
Ctrl/Cmd + D
Download current item

Technical Notes
	•	Unix: real PTY via pty.fork() → proper signal delivery.
	•	Windows: uses asyncio.create_subprocess_shell (no full PTY semantics).
	•	Max upload size: 500 MB.
	•	The embedded frontend uses:
	◦	xterm.js + fit addon
	◦	Monaco Editor
	◦	Font Awesome
	◦	LynkIO client (/lynkio/client.js)

Stopping the Server
Press Ctrl+C in the terminal where the server is running.

Enjoy coding with CodeForge Pro!
