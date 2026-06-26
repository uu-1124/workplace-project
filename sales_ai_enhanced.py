from __future__ import annotations

import json
import re
import uuid
import logging
from typing import Any

from fastapi import HTTPException

from app.services.llm import UpstreamModelError, chat_completion
from app.services.model_config import require_team_model_config

logger = logging.getLogger(__name__)


def _compact(value: Any, limit: int = 1200) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(_compact(part, 240) for part in parts if _compact(part, 240))
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, raw).hex[:14]}"


def _json_from_model_response(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("model response has no choices")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or item.get("content") or "") for item in content if isinstance(item, dict))
    content = str(content).strip()
    if not content:
        raise ValueError("model response content is empty")
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        content = content[start : end + 1]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("model response is not a JSON object")
    return parsed


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _check_content_quality(content: str, min_length: int = 20, field_name: str = "content") -> tuple[bool, str | None]:
    """
    检查生成内容的质量

    Args:
        content: 待检查的内容
        min_length: 最小长度要求
        field_name: 字段名称（用于错误提示）

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
    words = re.findall(r'\w+', content)
    if len(words) > 5:
        unique_words = set(words)
        if len(unique_words) / len(words) < 0.3:  # 独特词汇比例低于30%
            return False, f"{field_name}: 内容重复度过高"

    # 检查是否包含常见的占位符或错误提示
    placeholders = ["TODO", "待补充", "暂无", "N/A", "null", "undefined", "..."]
    if any(ph in content for ph in placeholders):
        return False, f"{field_name}: 包含占位符内容"

    return True, None


def _validate_generated_data(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """
    验证生成数据的质量

    Args:
        data: 生成的数据字典

    Returns:
        (is_valid, error_messages) 如果有效返回(True, [])，否则返回(False, [错误信息列表])
    """
    errors = []

    # 检查 leads
    leads = data.get("leads", [])
    if not leads or len(leads) == 0:
        errors.append("leads: 未生成任何线索")
    else:
        for i, lead in enumerate(leads[:3]):  # 只检查前3个
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
        for i, playbook in enumerate(playbooks[:2]):  # 只检查前2个
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


def _normalise_model_draft(
    raw: dict[str, Any],
    work_item: dict[str, Any],
    member: dict[str, Any],
) -> dict[str, Any]:
    seed = work_item["id"]
    owner_label = member.get("display_name") or "销售负责人"

    leads: list[dict[str, Any]] = []
    for index, item in enumerate(_list_of_dicts(raw.get("leads"))[:5]):
        name = _compact(item.get("name"), 80) or f"潜在客户 {index + 1}"
        leads.append(
            {
                "id": _compact(item.get("id"), 80) or _stable_id("lead", seed, name, str(index)),
                "name": name,
                "source": _compact(item.get("source"), 80) or "经营指标",
                "stage": item.get("stage") if item.get("stage") in {"cold", "warm", "hot", "customer"} else "warm",
                "score": max(0, min(100, int(item.get("score") or 70))),
                "need": _compact(item.get("need"), 1200),
                "next_step": _compact(item.get("next_step"), 1200),
                "objection": _compact(item.get("objection"), 1200),
                "owner": _compact(item.get("owner"), 80) or owner_label,
                "owner_member_id": member.get("id"),
                "value": max(0, float(item.get("value") or item.get("estimated_value") or 0)),
            }
        )

    playbooks: list[dict[str, Any]] = []
    for index, item in enumerate(_list_of_dicts(raw.get("playbooks"))[:5]):
        scenario = _compact(item.get("scenario"), 80) or f"销售场景 {index + 1}"
        questions = item.get("diagnosis_questions") or []
        if not isinstance(questions, list):
            questions = []
        playbooks.append(
            {
                "id": _compact(item.get("id"), 80) or _stable_id("playbook", seed, scenario, str(index)),
                "scenario": scenario,
                "opener": _compact(item.get("opener"), 1600),
                "diagnosis_questions": [_compact(question, 240) for question in questions[:6] if _compact(question, 240)],
                "value_proof": _compact(item.get("value_proof"), 1600),
                "objection_handling": _compact(item.get("objection_handling"), 1600),
                "close_action": _compact(item.get("close_action"), 1600),
            }
        )

    follow_ups: list[dict[str, Any]] = []
    for index, item in enumerate(_list_of_dicts(raw.get("follow_ups"))[:5]):
        follow_ups.append(
            {
                "id": _compact(item.get("id"), 80) or _stable_id("followup", seed, str(index)),
                "lead_id": _compact(item.get("lead_id"), 80),
                "lead_name": _compact(item.get("lead_name"), 80),
                "playbook_id": _compact(item.get("playbook_id"), 80),
                "channel": _compact(item.get("channel"), 80),
                "timing": _compact(item.get("timing"), 200),
                "message": _compact(item.get("message"), 1600),
                "status": item.get("status") if item.get("status") in {"planned", "sent", "converted", "blocked"} else "planned",
            }
        )

    return {
        "leads": leads,
        "playbooks": playbooks,
        "follow_ups": follow_ups,
    }


def _generate_fallback_draft(
    work_item: dict[str, Any],
    member: dict[str, Any],
    sales_brief: dict[str, Any],
    sales_launch_package: dict[str, Any],
) -> dict[str, Any]:
    """生成模板兜底数据"""
    seed = work_item["id"]
    owner_label = member.get("display_name") or "销售负责人"

    # 从 sales_brief 提取信息
    target_customer = sales_brief.get("target_customer", "目标客户")
    pain_points = sales_brief.get("pain_points", "客户痛点")
    solution_value = sales_brief.get("solution_value", "解决方案价值")

    # 生成默认线索
    leads = [
        {
            "id": _stable_id("lead", seed, "lead_1"),
            "name": f"{target_customer} - 潜在客户A",
            "source": "市场营销活动",
            "stage": "warm",
            "score": 65,
            "need": f"该客户面临{pain_points}的挑战，正在寻找合适的解决方案",
            "next_step": "安排产品演示，重点展示如何解决其核心痛点",
            "objection": "可能会关注价格和实施周期",
            "owner": owner_label,
            "owner_member_id": member.get("id"),
            "value": 50000,
        },
        {
            "id": _stable_id("lead", seed, "lead_2"),
            "name": f"{target_customer} - 潜在客户B",
            "source": "客户转介绍",
            "stage": "hot",
            "score": 85,
            "need": f"客户明确表示需要解决{pain_points}问题，已经评估过竞品",
            "next_step": "准备商务报价和实施方案，安排高层会议",
            "objection": "需要与现有系统集成",
            "owner": owner_label,
            "owner_member_id": member.get("id"),
            "value": 120000,
        },
    ]

    # 生成默认销售剧本
    playbooks = [
        {
            "id": _stable_id("playbook", seed, "playbook_1"),
            "scenario": "初次接触场景",
            "opener": f"您好，了解到贵公司在{target_customer}领域的业务，我们专注于{solution_value}，已经帮助多家类似企业解决了{pain_points}的问题。",
            "diagnosis_questions": [
                f"目前在{pain_points}方面遇到的主要挑战是什么？",
                "现有解决方案的效果如何？有什么不满意的地方？",
                "如果能解决这个问题，对业务会有什么改善？",
                "决策流程是怎样的？谁参与决策？",
            ],
            "value_proof": f"我们的解决方案可以{solution_value}，已经帮助XX公司提升了30%的效率",
            "objection_handling": "如果客户提出价格异议，可以强调投资回报周期和长期价值；如果担心实施风险，可以提供分阶段实施方案",
            "close_action": "如果您感兴趣，我们可以安排一次产品演示，让您亲眼看到效果",
        },
        {
            "id": _stable_id("playbook", seed, "playbook_2"),
            "scenario": "商务谈判场景",
            "opener": "感谢您对我们解决方案的认可，今天我们来讨论具体的合作细节和商务条款",
            "diagnosis_questions": [
                "预算范围是多少？有明确的预算时间窗口吗？",
                "除了价格，还有哪些因素会影响决策？",
                "期望的实施时间表是怎样的？",
                "需要哪些定制化开发？",
            ],
            "value_proof": f"根据您的需求，我们的方案可以在6个月内实现{solution_value}，投资回报期约12个月",
            "objection_handling": "针对价格异议，可以提供分期付款方案；针对实施风险，可以设置里程碑付款",
            "close_action": "我们可以在本周内提供正式报价和实施计划，下周安排技术团队与您对接",
        },
    ]

    # 生成默认跟进计划
    follow_ups = [
        {
            "id": _stable_id("followup", seed, "followup_1"),
            "lead_id": leads[0]["id"],
            "lead_name": leads[0]["name"],
            "playbook_id": playbooks[0]["id"],
            "channel": "电话",
            "timing": "首次接触后3天",
            "message": "您好，上次沟通后，我整理了一份针对您需求的解决方案，方便的话可以约个时间详细讨论",
            "status": "planned",
        },
        {
            "id": _stable_id("followup", seed, "followup_2"),
            "lead_id": leads[0]["id"],
            "lead_name": leads[0]["name"],
            "playbook_id": playbooks[0]["id"],
            "channel": "邮件",
            "timing": "首次接触后7天",
            "message": "分享一个相关案例供您参考，这家企业与您的情况很类似，实施后取得了显著效果",
            "status": "planned",
        },
        {
            "id": _stable_id("followup", seed, "followup_3"),
            "lead_id": leads[1]["id"],
            "lead_name": leads[1]["name"],
            "playbook_id": playbooks[1]["id"],
            "channel": "会议",
            "timing": "商务谈判后1天",
            "message": "发送正式报价和合同条款，安排法务和财务对接",
            "status": "planned",
        },
    ]

    return {
        "leads": leads,
        "playbooks": playbooks,
        "follow_ups": follow_ups,
    }


def generate_sales_launch_package_with_retry(
    conn,
    team_id: str,
    command: dict[str, Any],
    work_item: dict[str, Any],
    member: dict[str, Any],
    sales_brief: dict[str, Any],
    sales_launch_package: dict[str, Any],
    max_retries: int = 3,
) -> dict[str, Any]:
    """
    生成销售启动包，带重试和质量检查机制

    Args:
        conn: 数据库连接
        team_id: 团队ID
        command: 命令参数
        work_item: 工作项
        member: 成员信息
        sales_brief: 销售简报
        sales_launch_package: 销售启动包
        max_retries: 最大重试次数

    Returns:
        生成的销售启动包数据
    """
    warnings: list[str] = []
    fallback = _generate_fallback_draft(work_item, member, sales_brief, sales_launch_package)

    # 重试循环
    for attempt in range(max_retries):
        try:
            logger.info(f"Sales AI generation attempt {attempt + 1}/{max_retries}")

            # 调用模型生成
            customer_model = require_team_model_config(conn, team_id)
            response = chat_completion(
                {
                    "model": customer_model["model"],
                    "temperature": customer_model.get("temperature", 0.7),
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "你是一位资深销售专家，擅长制定销售策略。请根据用户提供的销售简报和命令，生成详细的销售启动包。"
                                "必须返回 JSON 格式，包含 leads（线索）、playbooks（销售剧本）、follow_ups（跟进计划）三个数组。"
                                "leads 包含 3-5 个，字段：id,name,source,stage,score,need,next_step,objection,value。"
                                "playbooks 包含 3-5 个，字段：id,scenario,opener,diagnosis_questions,value_proof,objection_handling,close_action。"
                                "follow_ups 包含 3-5 个，字段：id,lead_id,lead_name,playbook_id,channel,timing,message,status。"
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

            # 解析响应
            raw_data = _json_from_model_response(response)
            draft = _normalise_model_draft(raw_data, work_item, member)

            # 质量检查
            is_valid, errors = _validate_generated_data(draft)

            if is_valid:
                logger.info(f"Sales AI generation succeeded on attempt {attempt + 1}")
                return {**draft, "generated_by": "model", "warnings": warnings, "attempts": attempt + 1}
            else:
                error_msg = f"第{attempt + 1}次生成质量不达标: {'; '.join(errors)}"
                logger.warning(error_msg)
                warnings.append(error_msg)

                # 如果是最后一次尝试，使用模板兜底
                if attempt == max_retries - 1:
                    warnings.append("已达到最大重试次数，使用模板兜底")
                    logger.warning("Max retries reached, using fallback template")
                    return {**fallback, "generated_by": "fallback", "warnings": warnings, "attempts": attempt + 1}

        except (HTTPException, UpstreamModelError, ValueError, json.JSONDecodeError, TypeError) as exc:
            detail = getattr(exc, "detail", str(exc))
            error_msg = f"第{attempt + 1}次生成出错: {detail}"
            logger.error(error_msg, exc_info=True)
            warnings.append(error_msg)

            # 如果是最后一次尝试，使用模板兜底
            if attempt == max_retries - 1:
                warnings.append("模型生成多次失败，使用预设模板兜底")
                logger.warning("Max retries reached after errors, using fallback template")
                return {**fallback, "generated_by": "fallback", "warnings": warnings, "attempts": attempt + 1}

    # 理论上不会到这里，但为了保险还是返回兜底
    warnings.append("未知错误，使用模板兜底")
    return {**fallback, "generated_by": "fallback", "warnings": warnings, "attempts": max_retries}


# 保持向后兼容的原函数名
def generate_sales_launch_package(
    conn,
    team_id: str,
    command: dict[str, Any],
    work_item: dict[str, Any],
    member: dict[str, Any],
    sales_brief: dict[str, Any],
    sales_launch_package: dict[str, Any],
) -> dict[str, Any]:
    """
    生成销售启动包（兼容旧代码）
    """
    return generate_sales_launch_package_with_retry(
        conn, team_id, command, work_item, member, sales_brief, sales_launch_package
    )
