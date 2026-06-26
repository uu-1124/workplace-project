#!/usr/bin/env python3
import paramiko
import sys
import os

def upload_file(local_path, remote_path):
    """Upload file to remote server"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        # Connect to server
        client.connect(
            hostname='124.71.229.151',
            username='root',
            password='634305853aA',
            timeout=10
        )

        # Read local file
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Create backup first
        backup_cmd = f"cp {remote_path} {remote_path}.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true"
        stdin, stdout, stderr = client.exec_command(backup_cmd)
        stdout.read()

        # Write content using echo and cat
        # Split content into chunks to avoid command line length issues
        temp_file = f"/tmp/upload_{os.path.basename(remote_path)}"

        # Clear temp file
        client.exec_command(f"> {temp_file}")

        # Write in chunks
        chunk_size = 8000
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i+chunk_size]
            # Escape single quotes and backslashes
            chunk = chunk.replace('\\', '\\\\').replace("'", "'\\''")
            cmd = f"printf '%s' '{chunk}' >> {temp_file}"
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                print(f"Error writing chunk: {stderr.read().decode()}")
                return False

        # Move temp file to target (use absolute path)
        cmd = f"mv {temp_file} '{remote_path}'"
        print(f"Executing: {cmd}")
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()

        if exit_code == 0:
            print(f"Successfully uploaded {local_path} to {remote_path}")
            return True
        else:
            print(f"Error moving file: {stderr.read().decode()}")
            return False

    except Exception as e:
        print(f"Upload failed: {e}")
        return False
    finally:
        client.close()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python upload_files.py <local_path> <remote_path>")
        sys.exit(1)

    local_path = sys.argv[1]
    remote_path = sys.argv[2]

    # Fix Git Bash path conversion issue
    # Git Bash converts /opt/ to D:/Git/opt/, so we need to prevent this
    print(f"Local: {local_path}")
    print(f"Remote: {remote_path}")

    success = upload_file(local_path, remote_path)
    sys.exit(0 if success else 1)
