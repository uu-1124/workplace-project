#!/bin/bash
# 测试SSH连接
export SSHPASS='634305853aA'
sshpass -e ssh -o StrictHostKeyChecking=no root@124.71.229.151 "echo 'Connected successfully'"
