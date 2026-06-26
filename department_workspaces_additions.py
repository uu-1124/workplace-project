# ==================== 新增：销售流程重试接口 ====================
# 添加到 department_workspaces.py 文件末尾

from app.services import sales_ai

class RegenerateSalesItemRequest(BaseModel):
    """重新生成销售项目的请求模型"""
    regenerate_by_member_id: str | None = None
    item_type: Literal["lead", "playbook", "follow_up"] = "lead"
    force: bool = False  # 是否强制重新生成（即使之前成功）


@router.post("/{team_id}/{department_key}/sales/regenerate/{item_id}")
async def regenerate_sales_item(
    team_id: str,
    department_key: Literal["sales"],
    item_id: str,
    payload: RegenerateSalesItemRequest,
    authorization: str | None = Header(default=None),
):
    """
    重新生成销售流程中的某个项目（线索/剧本/跟进计划）

    Args:
        team_id: 团队ID
        department_key: 部门标识（必须是"sales"）
        item_id: 要重新生成的项目ID
        payload: 请求参数
        authorization: 认证令牌

    Returns:
        重新生成的结果
    """
    with db_conn() as conn:
        # 1. 认证和权限检查
        auth_member = auth_member_or_legacy(conn, team_id, authorization)
        department = _resolve_department(conn, team_id, department_key)
        _require_department_access(conn, team_id, department["id"], auth_member, "can_manage_tasks")
        actor_id = actor_member_id(
            conn, team_id, payload.regenerate_by_member_id, auth_member, "regenerate_by_member_id"
        )

        # 2. 获取成员信息
        member_row = conn.execute(
            "SELECT id, display_name, title FROM team_members WHERE team_id = ? AND id = ?",
            (team_id, actor_id),
        ).fetchone()
        if not member_row:
            raise HTTPException(status_code=404, detail="Member not found")

        member = {
            "id": member_row[0],
            "display_name": member_row[1],
            "title": member_row[2],
        }

        # 3. 查找原始项目数据
        # 从 sales pipeline 中查找
        pipeline = _sales_pipeline_payload(conn, team_id, department["id"])

        original_item = None
        item_type = payload.item_type

        if item_type == "lead":
            original_item = next((lead for lead in pipeline.get("leads", []) if lead.get("id") == item_id), None)
        elif item_type == "playbook":
            original_item = next((pb for pb in pipeline.get("playbooks", []) if pb.get("id") == item_id), None)
        elif item_type == "follow_up":
            original_item = next((fu for fu in pipeline.get("follow_ups", []) if fu.get("id") == item_id), None)

        if not original_item:
            raise HTTPException(
                status_code=404,
                detail=f"Sales {item_type} with id {item_id} not found in pipeline"
            )

        # 4. 获取销售简报和启动包上下文
        form_row = conn.execute(
            """
            SELECT payload
            FROM department_forms
            WHERE team_id = ? AND department_id = ? AND form_type = 'sales_brief'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (team_id, department["id"]),
        ).fetchone()

        sales_brief = json.loads(form_row[0]) if form_row else {}

        # 5. 创建一个临时工作项用于生成上下文
        work_item = {
            "id": f"regen_{item_id}_{uuid.uuid4().hex[:8]}",
            "department_id": department["id"],
            "regenerate_for": item_id,
            "regenerate_type": item_type,
        }

        # 6. 调用 sales_ai 重新生成
        command = {
            "action": "regenerate",
            "item_type": item_type,
            "original_item": original_item,
            "context": {
                "sales_brief": sales_brief,
                "existing_pipeline": pipeline,
            }
        }

        try:
            # 使用增强的生成函数
            draft = sales_ai.generate_sales_launch_package(
                conn=conn,
                team_id=team_id,
                command=command,
                work_item=work_item,
                member=member,
                sales_brief=sales_brief,
                sales_launch_package={
                    "leads": pipeline.get("leads", []),
                    "playbooks": pipeline.get("playbooks", []),
                    "follow_ups": pipeline.get("follow_ups", []),
                },
            )

            # 7. 从生成结果中提取对应类型的新项目
            new_item = None
            if item_type == "lead" and draft.get("leads"):
                new_item = draft["leads"][0]  # 取第一个生成的
            elif item_type == "playbook" and draft.get("playbooks"):
                new_item = draft["playbooks"][0]
            elif item_type == "follow_up" and draft.get("follow_ups"):
                new_item = draft["follow_ups"][0]

            if not new_item:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to generate new {item_type}"
                )

            # 8. 更新数据库中的 pipeline
            # 替换原有项目
            if item_type == "lead":
                updated_leads = [
                    new_item if lead.get("id") == item_id else lead
                    for lead in pipeline.get("leads", [])
                ]
                pipeline["leads"] = updated_leads
            elif item_type == "playbook":
                updated_playbooks = [
                    new_item if pb.get("id") == item_id else pb
                    for pb in pipeline.get("playbooks", [])
                ]
                pipeline["playbooks"] = updated_playbooks
            elif item_type == "follow_up":
                updated_follow_ups = [
                    new_item if fu.get("id") == item_id else fu
                    for fu in pipeline.get("follow_ups", [])
                ]
                pipeline["follow_ups"] = updated_follow_ups

            # 9. 保存更新后的 pipeline
            _replace_sales_pipeline(
                conn,
                team_id,
                department["id"],
                ReplaceSalesPipelineRequest(
                    updated_by_member_id=actor_id,
                    leads=[SalesLeadPayload(**lead) for lead in pipeline.get("leads", [])],
                    playbooks=[SalesPlaybookPayload(**pb) for pb in pipeline.get("playbooks", [])],
                    follow_ups=[SalesFollowUpPayload(**fu) for fu in pipeline.get("follow_ups", [])],
                )
            )

            conn.commit()

            return {
                "ok": True,
                "team_id": team_id,
                "department": department,
                "item_id": item_id,
                "item_type": item_type,
                "original_item": original_item,
                "new_item": new_item,
                "generated_by": draft.get("generated_by", "model"),
                "warnings": draft.get("warnings", []),
                "attempts": draft.get("attempts", 1),
            }

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to regenerate sales {item_type} {item_id}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to regenerate {item_type}: {str(e)}"
            )


@router.post("/{team_id}/{department_key}/sales/regenerate-all")
async def regenerate_all_sales_items(
    team_id: str,
    department_key: Literal["sales"],
    authorization: str | None = Header(default=None),
):
    """
    重新生成整个销售流程（所有线索、剧本、跟进计划）

    Args:
        team_id: 团队ID
        department_key: 部门标识（必须是"sales"）
        authorization: 认证令牌

    Returns:
        重新生成的完整销售启动包
    """
    with db_conn() as conn:
        # 1. 认证和权限检查
        auth_member = auth_member_or_legacy(conn, team_id, authorization)
        department = _resolve_department(conn, team_id, department_key)
        _require_department_access(conn, team_id, department["id"], auth_member, "can_manage_tasks")

        actor_id = auth_member["id"]

        # 2. 获取成员信息
        member_row = conn.execute(
            "SELECT id, display_name, title FROM team_members WHERE team_id = ? AND id = ?",
            (team_id, actor_id),
        ).fetchone()
        if not member_row:
            raise HTTPException(status_code=404, detail="Member not found")

        member = {
            "id": member_row[0],
            "display_name": member_row[1],
            "title": member_row[2],
        }

        # 3. 获取销售简报
        form_row = conn.execute(
            """
            SELECT payload
            FROM department_forms
            WHERE team_id = ? AND department_id = ? AND form_type = 'sales_brief'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (team_id, department["id"]),
        ).fetchone()

        sales_brief = json.loads(form_row[0]) if form_row else {}

        # 4. 创建工作项
        work_item = {
            "id": f"regen_all_{uuid.uuid4().hex[:8]}",
            "department_id": department["id"],
            "action": "regenerate_all",
        }

        # 5. 调用 sales_ai 重新生成完整的销售启动包
        command = {"action": "regenerate_all"}

        try:
            draft = sales_ai.generate_sales_launch_package(
                conn=conn,
                team_id=team_id,
                command=command,
                work_item=work_item,
                member=member,
                sales_brief=sales_brief,
                sales_launch_package={},
            )

            # 6. 保存新的 pipeline
            _replace_sales_pipeline(
                conn,
                team_id,
                department["id"],
                ReplaceSalesPipelineRequest(
                    updated_by_member_id=actor_id,
                    leads=[SalesLeadPayload(**lead) for lead in draft.get("leads", [])],
                    playbooks=[SalesPlaybookPayload(**pb) for pb in draft.get("playbooks", [])],
                    follow_ups=[SalesFollowUpPayload(**fu) for fu in draft.get("follow_ups", [])],
                )
            )

            conn.commit()

            return {
                "ok": True,
                "team_id": team_id,
                "department": department,
                "leads": draft.get("leads", []),
                "playbooks": draft.get("playbooks", []),
                "follow_ups": draft.get("follow_ups", []),
                "generated_by": draft.get("generated_by", "model"),
                "warnings": draft.get("warnings", []),
                "attempts": draft.get("attempts", 1),
            }

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to regenerate all sales items: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to regenerate sales items: {str(e)}"
            )
