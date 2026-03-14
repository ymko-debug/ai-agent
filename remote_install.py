"""
remote_install.py
─────────────────
Run this from YOUR machine to remotely install the AI Assistant
on a Windows client PC via SSH.

Requirements on YOUR machine:
    pip install paramiko

Requirements on CLIENT's Windows PC:
    - OpenSSH must be enabled (Windows 10/11: Settings → Apps → Optional Features → OpenSSH Server)
    - Python 3.10+ installed (https://python.org)
    - Internet access

Usage:
    python remote_install.py
"""

import os
import time
import paramiko
from pathlib import Path

# ─────────────────────────────────────────
# ✏️  FILL THESE IN BEFORE RUNNING
# ─────────────────────────────────────────
CLIENT_HOST     = "192.168.0.113"       # Client's IP address (ask them to run: ipconfig)
CLIENT_USER     = "artf8"    # Their Windows username
CLIENT_PASSWORD = "ClientPassword"    # Their Windows password  (or use SSH key below)
CLIENT_SSH_KEY  = r"C:\Users\rusla\.ssh\client_key"                # Path to .pem key if using key auth, e.g. "C:/keys/client.pem"
                                      # Set to None if using password auth

INSTALL_DIR     = "C:\\Users\\artf8\\OneDrive\\Desktop\\ARIA 2"   # Where to install on client's PC

# API keys to pre-configure for the client
CLIENT_OPENAI_KEY      = ""     # Client's OpenAI API key — fill in before running
CLIENT_OPENROUTER_KEY  = ""           # Optional fallback
CLIENT_TAVILY_KEY      = ""           # Optional web search

# ─────────────────────────────────────────
# FILES TO DEPLOY (must exist next to this script)
# ─────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
FILES_TO_SEND = {
    "client_app.py":      SCRIPT_DIR / "client_app.py",
    "requirements.txt":   SCRIPT_DIR / "requirements_client.txt",
}

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def run(ssh: paramiko.SSHClient, cmd: str, desc: str = "", timeout: int = 120) -> str:
    """Run a command on the remote machine, print output, return stdout."""
    if desc:
        print(f"\n▶ {desc}")
    print(f"  CMD: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="ignore").strip()
    err = stderr.read().decode(errors="ignore").strip()
    if out:
        print(f"  OUT: {out[:500]}")
    if err:
        print(f"  ERR: {err[:500]}")
    return out


def transfer(sftp: paramiko.SFTPClient, local_path: Path, remote_path: str):
    """Upload a file to the remote machine."""
    print(f"\n📤 Uploading {local_path.name} → {remote_path}")
    sftp.put(str(local_path), remote_path)
    print(f"   ✅ Done")


# ─────────────────────────────────────────
# MAIN INSTALLER
# ─────────────────────────────────────────
def install():
    print("=" * 55)
    print("  AI Assistant — Remote Installer for Windows")
    print("=" * 55)
    print(f"\n🎯 Target: {CLIENT_USER}@{CLIENT_HOST}")
    print(f"📁 Install path: {INSTALL_DIR}\n")

    # ── Connect via SSH ──────────────────────────────────
    print("🔌 Connecting via SSH…")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if CLIENT_SSH_KEY:
            pkey = paramiko.RSAKey.from_private_key_file(CLIENT_SSH_KEY)
            ssh.connect(
                CLIENT_HOST,
                username=CLIENT_USER,
                pkey=pkey,
                look_for_keys=False,
                allow_agent=False,
                timeout=15
            )
        else:
            ssh.connect(
                CLIENT_HOST,
                username=CLIENT_USER,
                password=CLIENT_PASSWORD,
                look_for_keys=False,
                allow_agent=False,
                timeout=15
            )
        print("✅ Connected!\n")
    except Exception as e:
        print(f"❌ SSH connection failed: {e}")
        print("\nTroubleshooting:")
        print("  1. Ask client to run: ipconfig  (to get their IP)")
        print("  2. Ask client to enable OpenSSH:")
        print("     Settings → Apps → Optional Features → Add: OpenSSH Server")
        print("  3. Ask client to start SSH: Services → OpenSSH SSH Server → Start")
        return

    sftp = ssh.open_sftp()

    # ── Create install directory ─────────────────────────
    run(ssh, f'cmd /c "if not exist "{INSTALL_DIR}" mkdir "{INSTALL_DIR}""',
        desc="Creating install directory")

    # ── Check Python is installed ────────────────────────
    py_check = run(ssh, "python --version", desc="Checking Python version")
    if "Python 3" not in py_check:
        print("\n⚠️  Python 3 not found on client machine.")
        print("   Please ask your client to install Python from https://python.org")
        print("   Then re-run this installer.")
        ssh.close()
        return

    # ── Upload agent files ───────────────────────────────
    print("\n📦 Uploading agent files…")
    for remote_name, local_path in FILES_TO_SEND.items():
        if not local_path.exists():
            print(f"  ❌ Missing local file: {local_path}")
            print(f"     Make sure {remote_name} exists next to remote_install.py")
            ssh.close()
            return
        transfer(sftp, local_path, f"{INSTALL_DIR}\\{remote_name}")

    # ── Write .env file with client's API keys ───────────
    print("\n🔑 Writing .env configuration…")
    env_content = (
        f"OPENAI_API_KEY={CLIENT_OPENAI_KEY}\n"
        f"OPENROUTER_API_KEY={CLIENT_OPENROUTER_KEY}\n"
        f"TAVILY_API_KEY={CLIENT_TAVILY_KEY}\n"
    )
    # Write .env via echo commands (avoids needing to upload a file)
    run(ssh, f'cmd /c "echo OPENAI_API_KEY={CLIENT_OPENAI_KEY}> "{INSTALL_DIR}\\.env""',
        desc="Writing .env")
    if CLIENT_OPENROUTER_KEY:
        run(ssh, f'cmd /c "echo OPENROUTER_API_KEY={CLIENT_OPENROUTER_KEY}>> "{INSTALL_DIR}\\.env""')
    if CLIENT_TAVILY_KEY:
        run(ssh, f'cmd /c "echo TAVILY_API_KEY={CLIENT_TAVILY_KEY}>> "{INSTALL_DIR}\\.env""')
    print("   ✅ .env written")

    # ── Create virtual environment ───────────────────────
    run(ssh, f'cmd /c "cd /d "{INSTALL_DIR}" && python -m venv venv"',
        desc="Creating Python virtual environment", timeout=60)

    # ── Install dependencies ─────────────────────────────
    run(ssh,
        f'cmd /c "cd /d "{INSTALL_DIR}" && venv\\Scripts\\pip install -r requirements.txt --quiet"',
        desc="Installing Python packages (this may take a minute…)", timeout=300)

    # ── Create a startup batch file ──────────────────────
    print("\n📝 Creating start_assistant.bat…")
    bat_content = (
        "@echo off\n"
        f"cd /d \"{INSTALL_DIR}\"\n"
        "call venv\\Scripts\\activate\n"
        "streamlit run client_app.py\n"
        "pause\n"
    )
    bat_remote = f"{INSTALL_DIR}\\start_assistant.bat"

    # Write bat file line by line
    run(ssh, f'cmd /c "echo @echo off> "{bat_remote}""')
    run(ssh, f'cmd /c "echo cd /d \\"{INSTALL_DIR}\\">> "{bat_remote}""')
    run(ssh, f'cmd /c "echo call venv\\Scripts\\activate>> "{bat_remote}""')
    run(ssh, f'cmd /c "echo streamlit run client_app.py>> "{bat_remote}""')
    print("   ✅ start_assistant.bat created")

    # ── Verify installation ──────────────────────────────
    print("\n🔍 Verifying installation…")
    files = run(ssh, f'cmd /c "dir /b "{INSTALL_DIR}""', desc="Files in install directory")
    print(f"\n   Files installed:\n   {files.replace(chr(10), chr(10)+'   ')}")

    # ── Done! ────────────────────────────────────────────
    print("\n" + "=" * 55)
    print("  ✅  Installation complete!")
    print("=" * 55)
    print(f"""
📌 To launch the assistant on the client's PC:
   Option A — Double-click:  {INSTALL_DIR}\\start_assistant.bat
   Option B — Command line:
       cd /d "{INSTALL_DIR}"
       venv\\Scripts\\activate
       streamlit run client_app.py

🌐 The assistant will open in their browser at:
   http://localhost:8501
""")

    sftp.close()
    ssh.close()


if __name__ == "__main__":
    install()
