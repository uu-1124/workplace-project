#!/usr/bin/env python3
import paramiko

def enhance_sales_ai():
    """Add quality check and retry logic to sales_ai.py"""

    # Code to insert after the imports
    quality_check_code = '''

# ==================== Quality Check Functions (Added) ====================

def _check_content_quality(content: str, min_length: int = 20, field_name: str = "content") -> tuple[bool, str | None]:
    """
    检查生成内容的质量

    Returns:
        (is_valid, error_message) 如果有效返回(True, None)，否则返回(False, 错误信息)
    """
    if not content or not isinstance(content, str):
        return False, f"{field_name}: 内容为空"

    content = content.strip()

    # 检查长度
    if len(content) < min_length:
        return False, f"{field_name}: 内容过短（少于{min_length}字符）"

    # 检查是否是空话（重复词汇过多）
    words = re.findall(r'\\w+', content)
    if len(words) > 5:
        unique_words = set(words)
        if len(unique_words) / len(words) < 0.3:
            return False, f"{field_name}: 内容重复度过高"

    # 检查是否包含常见的占位符
    placeholders = ["TODO", "待补充", "暂无", "N/A", "null", "undefined", "..."]
    if any(ph in content for ph in placeholders):
        return False, f"{field_name}: 包含占位符内容"

    return True, None


def _validate_generated_data(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    验证生成数据的质量

    Returns:
        (is_valid, error_messages) 如果有效返回(True, [])，否则返回(False, [错误信息列表])
    """
    errors = []

    # 检查 leads
    leads = data.get("leads", [])
    if not leads or len(leads) == 0:
        errors.append("leads: 未生成任何线索")
    else:
        for i, lead in enumerate(leads[:3]):
            if not lead.get("name"):
                errors.append(f"leads[{i}]: 缺少客户名称")

            is_valid, err = _check_content_quality(lead.get("need", ""), 15, f"leads[{i}].need")
            if not is_valid:
                errors.append(err)

            is_valid, err = _check_content_quality(lead.get("next_step", ""), 10, f"leads[{i}].next_step")
            if not is_valid:
                errors.append(err)

    # 检查 playbooks
    playbooks = data.get("playbooks", [])
    if not playbooks or len(playbooks) == 0:
        errors.append("playbooks: 未生成任何销售剧本")
    else:
        for i, playbook in enumerate(playbooks[:2]):
            if not playbook.get("scenario"):
                errors.append(f"playbooks[{i}]: 缺少场景名称")

            is_valid, err = _check_content_quality(playbook.get("opener", ""), 20, f"playbooks[{i}].opener")
            if not is_valid:
                errors.append(err)

            questions = playbook.get("diagnosis_questions", [])
            if not questions or len(questions) < 2:
                errors.append(f"playbooks[{i}]: 诊断问题数量不足")

    # 检查 follow_ups
    follow_ups = data.get("follow_ups", [])
    if not follow_ups or len(follow_ups) == 0:
        errors.append("follow_ups: 未生成任何跟进计划")

    return len(errors) == 0, errors

'''

    # Code to replace the generate_sales_draft function with retry logic
    enhanced_generate = '''
async def generate_sales_draft(
    conn,
    team_id: str,
    work_item: dict[str, Any],
    member: dict[str, Any],
    use_model: bool = True,
    max_retries: int = 3,
) -> dict[str, Any]:
    """生成销售草稿，带重试和质量检查机制"""
    command = _command_for_work_item(conn, team_id, work_item.get("command_id"))
    sales_brief = _sales_brief(conn, team_id, work_item["department_id"])
    sales_launch_package = _sales_launch_package(conn, team_id, work_item.get("command_id"))
    fallback = _fallback_sales_draft(work_item, command, sales_brief, sales_launch_package, member)
    warnings: list[str] = []

    if not use_model:
        return {
            **fallback,
            "generated_by": "fallback",
            "warnings": ["未启用模型，使用系统预设的销售白皮模板生成。"],
        }

    # 重试循环
    for attempt in range(max_retries):
        try:
            customer_model = require_team_model_config(conn, team_id)
            response = await chat_completion(
                {
                    "model": "agent:sales-consultant",
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是商业销售成交顾问。只返回 JSON，不要 Markdown。"
                                "JSON 字段必须有： title, summary, leads, playbooks, follow_ups。"
                                "leads 至少 3 个，字段：id,name,source,stage,score,need,next_step,objection,value。"
                                "playbooks 至少 3 个，字段：id,scenario,opener,diagnosis_questions,value_proof,objection_handling,close_action。"
                                "follow_ups 至少 3 个，字段：id,lead_id,lead_name,playbook_id,channel,timing,message,status。"
                                "内容要具体可执行，不能空泛。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "work_item": work_item,
                                    "command": command,
                                    "sales_brief": sales_brief,
                                    "sales_launch_package": sales_launch_package,
                                    "operator": {
                                        "id": member.get("id"),
                                        "display_name": member.get("display_name"),
                                        "title": member.get("title"),
                                    },
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                },
                customer_model,
            )
            draft = _normalise_model_draft(_json_from_model_response(response), work_item, member)

            # 质量检查
            is_valid, errors = _validate_generated_data(draft)

            if is_valid:
                return {**draft, "generated_by": "model", "warnings": warnings, "attempts": attempt + 1}
            else:
                error_msg = f"第{attempt + 1}次生成质量不达标: {'; '.join(errors)}"
                warnings.append(error_msg)

                if attempt == max_retries - 1:
                    warnings.append("已达到最大重试次数，使用模板兜底")
                    return {**fallback, "generated_by": "fallback", "warnings": warnings, "attempts": attempt + 1}

        except (HTTPException, UpstreamModelError, ValueError, json.JSONDecodeError, TypeError) as exc:
            detail = getattr(exc, "detail", str(exc))
            error_msg = f"第{attempt + 1}次生成出错: {detail}"
            warnings.append(error_msg)

            if attempt == max_retries - 1:
                warnings.append("模型生成多次失败，使用预设模板兜底")
                return {**fallback, "generated_by": "fallback", "warnings": warnings, "attempts": attempt + 1}

    warnings.append("未知错误，使用模板兜底")
    return {**fallback, "generated_by": "fallback", "warnings": warnings, "attempts": max_retries}
'''

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname='124.71.229.151',
            username='root',
            password='634305853aA',
            timeout=10
        )

        # Read original file
        stdin, stdout, stderr = client.exec_command("cat /opt/workplace-ai-platform/api/app/services/sales_ai.py")
        original_content = stdout.read().decode('utf-8')

        # Insert quality check functions after imports (before _compact function)
        insert_position = original_content.find('def _compact(')
        if insert_position == -1:
            print("Error: Could not find insertion point")
            return False

        new_content = original_content[:insert_position] + quality_check_code + "\n\n" + original_content[insert_position:]

        # Replace generate_sales_draft function
        start_marker = "async def generate_sales_draft("
        end_marker = '        return {**fallback, "generated_by": "fallback", "warnings": warnings}'

        start_pos = new_content.find(start_marker)
        if start_pos == -1:
            print("Error: Could not find generate_sales_draft function")
            return False

        end_pos = new_content.find(end_marker, start_pos)
        if end_pos == -1:
            print("Error: Could not find end of generate_sales_draft function")
            return False

        end_pos = end_pos + len(end_marker)

        new_content = new_content[:start_pos] + enhanced_generate + new_content[end_pos:]

        # Write to temp file
        temp_file = "/tmp/sales_ai_enhanced.py"
        cmd = f"> {temp_file}"
        client.exec_command(cmd)

        # Write in chunks
        chunk_size = 8000
        for i in range(0, len(new_content), chunk_size):
            chunk = new_content[i:i+chunk_size]
            chunk = chunk.replace('\\', '\\\\').replace("'", "'\\''")
            cmd = f"printf '%s' '{chunk}' >> {temp_file}"
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                print(f"Error writing chunk: {stderr.read().decode()}")
                return False

        # Backup and replace
        stdin, stdout, stderr = client.exec_command(
            f"cp /opt/workplace-ai-platform/api/app/services/sales_ai.py "
            f"/opt/workplace-ai-platform/api/app/services/sales_ai.py.backup2.$(date +%Y%m%d_%H%M%S) && "
            f"mv {temp_file} /opt/workplace-ai-platform/api/app/services/sales_ai.py"
        )
        exit_code = stdout.channel.recv_exit_status()

        if exit_code == 0:
            print("Successfully enhanced sales_ai.py with quality checks and retry logic")
            return True
        else:
            print(f"Error replacing file: {stderr.read().decode()}")
            return False

    except Exception as e:
        print(f"Failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        client.close()

if __name__ == "__main__":
    enhance_sales_ai()
