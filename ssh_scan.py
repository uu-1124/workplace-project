#!/usr/bin/env python3
import paramiko
import sys

def ssh_command(host, username, password, command):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=username, password=password, timeout=30)

        stdin, stdout, stderr = client.exec_command(command)
        output = stdout.read().decode('utf-8')
        error = stderr.read().decode('utf-8')

        if output:
            print(output)
        if error:
            print(error, file=sys.stderr)

        client.close()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    host = "124.71.229.151"
    username = "root"
    password = "634305853aA"

    if len(sys.argv) > 1:
        command = sys.argv[1]
    else:
        command = "ls -la /opt/workplace-ai-platform"

    exit(ssh_command(host, username, password, command))
