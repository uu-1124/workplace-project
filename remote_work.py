#!/usr/bin/env python3
import paramiko
import sys
import os

def ssh_connect():
    """Connect to remote server"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname='124.71.229.151',
            username='root',
            password='634305853aA',
            timeout=10
        )
        return client
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

def read_file(client, remote_path):
    """Read file from remote server"""
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_path, 'r') as f:
            content = f.read().decode('utf-8')
        return content
    finally:
        sftp.close()

def write_file(client, remote_path, content):
    """Write file to remote server"""
    sftp = client.open_sftp()
    try:
        with sftp.file(remote_path, 'w') as f:
            f.write(content.encode('utf-8'))
    finally:
        sftp.close()

def execute_command(client, command):
    """Execute command on remote server"""
    stdin, stdout, stderr = client.exec_command(command)
    return stdout.read().decode('utf-8'), stderr.read().decode('utf-8')

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python remote_work.py <command> [args]")
        sys.exit(1)

    cmd = sys.argv[1]
    client = ssh_connect()

    try:
        if cmd == "read":
            if len(sys.argv) < 3:
                print("Usage: python remote_work.py read <remote_path>")
                sys.exit(1)
            content = read_file(client, sys.argv[2])
            print(content)

        elif cmd == "write":
            if len(sys.argv) < 4:
                print("Usage: python remote_work.py write <remote_path> <local_file>")
                sys.exit(1)
            with open(sys.argv[3], 'r', encoding='utf-8') as f:
                content = f.read()
            write_file(client, sys.argv[2], content)
            print(f"File written to {sys.argv[2]}")

        elif cmd == "exec":
            if len(sys.argv) < 3:
                print("Usage: python remote_work.py exec <command>")
                sys.exit(1)
            stdout, stderr = execute_command(client, sys.argv[2])
            if stdout:
                print(stdout)
            if stderr:
                print(stderr, file=sys.stderr)

    finally:
        client.close()
