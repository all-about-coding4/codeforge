# ⚡ CodeForge
```text
Multi-User Terminal + Files IDE

CodeForge is a self-hosted, browser-based development environment that brings a real terminal, file manager, Monaco editor, and real-time collaboration together in one workspace.

Built with Python + Lynkio, CodeForge is designed to feel like a lightweight desktop IDE while remaining accessible from any modern browser — including mobile devices.
```

# ✨ Features
```text
## Feature	## Description

🖥️ Real Terminal	Interactive shell with proper controlling TTY support on Unix systems. Ctrl+C, Ctrl+Z, job control, and other terminal signals work as expected.
🧩 Multiple Terminals	Open multiple independent terminal sessions and switch between them using tabs.
📝 Monaco Editor	Full-featured code editor with syntax highlighting and familiar keyboard shortcuts.
📁 File Explorer	Create, rename, delete, upload, download, and navigate files and folders directly from the browser.
👥 Multi-User Collaboration	Multiple users can work in the same workspace and see live editing activity.
🔄 Live File Updates	Changes made by another user can be synchronized in real time.
🔐 Workspace Isolation	File operations are restricted to the configured workspace/ directory.
📱 Mobile Friendly	Responsive interface with an on-screen terminal keyboard for touch devices.
🔁 Session Restart	Restarting a terminal session cleans up the existing process tree before starting a new shell.
🆔 User Identity	Each user can configure a username that appears in the terminal and collaboration UI.
```

# 🖼️ What You Get
```text
CodeForge combines the essential parts of a development environment into a single browser interface:

┌─────────────────────────────────────────────────────────────┐
│  CodeForge                              👤 alex         │
├───────────────┬─────────────────────────────────────────────┤
│               │                                             │
│ 📁 workspace  │              Monaco Editor                  │
│               │                                             │
│  📄 main.py   │   def hello():                             │
│  📄 server.py │       print("Hello, CodeForge!")           │
│  📁 src/      │                                             │
│               │                                             │
├───────────────┴─────────────────────────────────────────────┤
│ Terminal 1 │ Terminal 2 │ +                                │
├─────────────────────────────────────────────────────────────┤
│ alex$ python main.py                                            │
│ Hello, CodeForge!                                           │
│ alex$                                                          │
└─────────────────────────────────────────────────────────────┘

The exact interface may vary depending on the version and configuration.
```

# 🚀 Quick Start
```text
Requirements

* Python 3.8+
* Lynkio
* Linux or macOS recommended for full terminal functionality
* A modern web browser

Windows: CodeForge Pro can run using a subprocess-based terminal, but Unix systems provide more complete PTY and job-control behavior.
```

# 📦 Installation
```text
Clone the project or copy codeforge.py into a new directory.

Install the framework:

pip install lynkio

Optional: Create a virtual environment

python -m venv .venv

Linux/macOS:

source .venv/bin/activate

Windows:

.venv\Scripts\activate

Then install Lynkio inside the environment:

pip install lynkio
```

# ▶️ Running CodeForge
```text
Start the server:

python codeforge.py

You should see output similar to:

============================================================
  CodeForge Pro — Multi-Terminal
============================================================
  🌐  http://localhost:5000
  📂  Workspace: /path/to/workspace
  🐍  Python: ...
  📦  Venv: ...
  🐚  Shell: /bin/bash
============================================================

Open:

http://localhost:5000

The server binds to:

0.0.0.0:5000

This means the application can also be accessed by other devices on the same network, which is useful for collaborative development and testing.

For example:

http://192.168.1.100:5000
```

# 📁 File Explorer
```text
The file explorer provides a browser-based interface for managing the workspace.

Navigation

* Click a folder to enter it.
* Use the breadcrumb navigation to move back through directories.
* Click a file to open it in the editor.
* Right-click on desktop or long-press on supported touch devices to open the context menu.

File Operations

The explorer supports:

* Create file
* Create folder
* Open file
* Rename
* Delete
* Upload
* Download
* Run files in the terminal

Example:

workspace/
├── main.py
├── README.md
├── requirements.txt
└── src/
    ├── app.py
    └── utils.py

```

# 📝 Monaco Editor
```text
CodeForge uses Monaco Editor, the same editor technology that powers VS Code.

Features include:

* Syntax highlighting
* Multiple programming languages
* Automatic saving
* Familiar editor shortcuts
* Current-file collaboration indicators
* Live updates from other users

Automatic Saving

Files are automatically saved approximately 900 ms after you stop typing.

You can also force a save with:

Ctrl + S

or on macOS:

Cmd + S
```

# 🖥️ Terminal
```text
CodeForge provides interactive terminal sessions directly inside the browser.

Opening the Terminal

Click the Terminal button or use:

Ctrl/Cmd + `

Creating a Terminal

Click:

+

or press:

Ctrl/Cmd + T

Each terminal tab represents an independent session.

Terminal Restart

The Restart button terminates the existing terminal session and starts a fresh shell.

On Unix systems, CodeForge Pro attempts to clean up the entire process tree rather than terminating only the shell process.

This helps prevent child processes from remaining behind after a restart.
```

# 📱 Mobile Support
```text
CodeForge is designed to work on smaller screens and touch devices.

The terminal includes an on-screen control bar containing commonly required keys such as:

Esc   Tab   ↑   ↓   ←   →   Enter   ^C   ^D   ^L etc.

This makes it possible to interact with command-line applications even when the mobile keyboard does not provide convenient access to terminal control keys.
```

# 👥 Multi-User Collaboration

```text
CodeForge supports multiple users working in the same environment.

Users can configure their own username, which is used throughout the interface.

Collaboration features include:

* Persistent user identity
* Usernames in the terminal prompt
* Active-user indicators
* File editing indicators
* Real-time file updates
* Shared workspace sessions

For example, when several users are editing the same file, the interface can indicate that other users are currently working on it.
```

# 🆔 User Identity
```text
Click the user icon or terminal title to configure your username.

The username is used for:

Terminal prompt
     ↓
alex@codeforge:~/workspace$

and for collaboration indicators inside the editor.

User information is stored inside the workspace configuration.
```

# ⬇️ Downloads
```text
Files and directories can be downloaded directly from the browser.

Download a file

Select the file and use the Download action.

Download a directory

Use the download option from the current directory or breadcrumb.

Directories can be packaged as a ZIP archive before being downloaded.
```

# 📂 Workspace

```text
CodeForge keeps project files inside:

workspace/

The directory is located next to the application script.

Example:

codeforge/
├── app.py
|–– requirements.txt
├── workspace/
│   ├── main.py
│   ├── README.md
│   └── src/
└── ...

Workspace Isolation

File operations are intended to remain within the configured workspace directory.

This prevents normal explorer operations from navigating outside the project workspace.

Hidden Files

Files beginning with . are hidden from the file tree.

For example:

.git/
.env
.config

are not displayed in the normal explorer tree.

User information is stored in:

workspace/.users.json
```

# ⌨️ Keyboard Shortcuts

```text
Shortcut	Action
`Ctrl/Cmd + ``	Toggle terminal
Ctrl/Cmd + T	Open a new terminal
Ctrl/Cmd + S	Save current file
Ctrl/Cmd + D	Download current item

Browser and operating-system shortcuts can take precedence over application shortcuts in some environments.
```

# 🔧 Technical Overview

```text
CodeForge is built around a Python backend with Lynkio providing the application/runtime communication layer.

Unix Terminal

On Unix-like systems, the terminal uses a real PTY:

pty.fork()

This provides proper terminal semantics and allows signals such as:

Ctrl+C
Ctrl+Z
Ctrl+D

to behave much more like a native terminal.

Windows

Windows uses a subprocess-based terminal implementation.

Because Windows does not provide the same Unix PTY model, some terminal features and job-control behavior may differ.
```

# 🧱 Frontend Components

```text
The embedded frontend uses:

* Monaco Editor — code editing
* xterm.js — terminal rendering
* xterm.js Fit Addon — terminal sizing
* Font Awesome — interface icons
* Lynkio Client — browser/server communication

The Lynkio client is served by the application at:

/lynkio/client.js
```

# ⚙️ Configuration

```text
The default server address is:

http://localhost:5000

and the server listens on:

0.0.0.0:5000

The workspace is created relative to the application directory:

./workspace/

The application may also create or use a Python virtual environment for project execution depending on the configuration.
```

# 📊 Current Limits

```text
Resource	Default
Server port	5000
Bind address	0.0.0.0
Maximum upload size	500 MB
Workspace	./workspace/
Editor	Monaco
Terminal	xterm.js
Backend	Python + Lynkio

These values may change as CodeForge evolves.
```

# 🛡️ Security Considerations
```text
CodeForge provides a powerful browser-accessible terminal, so it should not be exposed directly to the public Internet without appropriate authentication and network security controls.

In particular, anyone who can access an unrestricted terminal session may potentially execute commands with the permissions of the server process.

For development or trusted local networks, running it on:

localhost

or behind a properly configured private network is recommended or wait for next update with security fixes.
```

# 🛑 Stopping the Server

```text
To stop CodeForge, return to the terminal where it is running and press:

Ctrl+C

## 🗺️ Project Structure

A typical installation can look like:

codeforge/
│
├── app.py
|–– requirements.txt
│
├── workspace/
│   ├── .users.json
│   ├── main.py
│   └── ...
│
└── ...

Your workspace contents remain separate from the application itself, making it easy to back up or move projects.
```

# 🤝 Contributing

```text
Contributions, bug reports, ideas, and improvements are welcome.

If you find an issue:

1. Reproduce the problem.
2. Include your Python version and operating system.
3. Include relevant terminal/browser output.
4. Describe the expected and actual behavior.

Pull requests are welcome for improvements that keep CodeForge lightweight, reliable, and easy to self-host.

⭐ Support the Project

If CodeForge is useful to you, consider giving the project a ⭐ Star on GitHub.

It helps the project gain visibility and lets others discover it.
```

# 📜 License

```text
MIT License
```

# 🚀 CodeForge

```text
A lightweight browser IDE with a real terminal, files, editing, and collaboration — all in one workspace.

Built with Python + Lynkio.
```