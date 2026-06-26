from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import logging
import traceback
from datetime import datetime

from app.config import get_settings
from app.routers import (
    adapters,
    agents,
    auth,
    billing,
    code_agents,
    department_workspaces,
    dispatch,
    openai,
    pipelines,
    runtime,
    teams,
    tools,
    workflows,
    workspace,
)
from app.services.db import assert_schema_ready, database_kind

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Open WebUI + CrewAI-style workplace agents + solopreneur workflows integration layer.",
    version="1.0.0",
)

# CORS 配置，禁止 "*" 和 allow_credentials=True 同时使用，但两者都很安全；
# 通过 CORS_ORIGINS 环境变量配置允许的源，逗号分隔。
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_allow_all = _cors_origins == ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # 通配源时不能携带凭据，这是 CORS 规范，但两者都是 cookie 常见用法；
    allow_credentials=not _allow_all,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 全局异常处理器 ====================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """处理请求验证错误"""
    logger.warning(f"Validation error on {request.method} {request.url.path}: {exc.errors()}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "validation_error",
            "message": "请求参数验证失败",
            "details": exc.errors(),
            "path": str(request.url.path),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """处理所有未捕获的异常"""
    # 记录详细错误日志
    error_id = f"err_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{id(exc)}"

    logger.error(
        f"Unhandled exception [{error_id}] on {request.method} {request.url.path}: "
        f"{type(exc).__name__}: {str(exc)}\n"
        f"Traceback:\n{traceback.format_exc()}"
    )

    # 区分不同类型的异常
    error_type = type(exc).__name__
    error_message = str(exc)
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    # 特殊处理已知异常类型
    if hasattr(exc, "status_code"):
        status_code = exc.status_code

    # 构建用户友好的错误响应
    response_content = {
        "error": error_type,
        "message": "服务器内部错误，请稍后重试",
        "error_id": error_id,
        "path": str(request.url.path),
        "timestamp": datetime.utcnow().isoformat(),
    }

    # 在开发环境下返回详细错误信息
    if settings.debug if hasattr(settings, 'debug') else False:
        response_content["details"] = error_message
        response_content["traceback"] = traceback.format_exc().split("\n")

    return JSONResponse(
        status_code=status_code,
        content=response_content,
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录所有请求的中间件"""
    start_time = datetime.utcnow()

    # 记录请求
    logger.info(f"Request: {request.method} {request.url.path}")

    try:
        response = await call_next(request)

        # 记录响应时间
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.info(
            f"Response: {request.method} {request.url.path} - "
            f"Status: {response.status_code} - Duration: {duration:.3f}s"
        )

        return response
    except Exception as exc:
        # 记录处理过程中的异常
        duration = (datetime.utcnow() - start_time).total_seconds()
        logger.error(
            f"Request failed: {request.method} {request.url.path} - "
            f"Duration: {duration:.3f}s - Error: {type(exc).__name__}: {str(exc)}"
        )
        raise


# ==================== 应用生命周期 ====================

@app.on_event("startup")
async def startup() -> None:
    logger.info(f"Starting {settings.app_name}...")
    try:
        assert_schema_ready()
        logger.info("Database schema check passed")
    except Exception as e:
        logger.error(f"Database schema check failed: {e}")
        raise


@app.on_event("shutdown")
async def shutdown() -> None:
    logger.info(f"Shutting down {settings.app_name}...")


# ==================== 健康检查 ====================

@app.get("/health")
async def health():
    return {"status": "ok", "service": settings.app_name, "database": database_kind()}


# ==================== 路由注册 ====================

app.include_router(openai.router)
app.include_router(adapters.router)
app.include_router(auth.router)
app.include_router(agents.router)
app.include_router(dispatch.router)
app.include_router(department_workspaces.router)
app.include_router(billing.router)
app.include_router(code_agents.router)
app.include_router(workflows.router)
app.include_router(pipelines.router)
app.include_router(runtime.router)
app.include_router(teams.router)
app.include_router(tools.router)
app.include_router(workspace.router)
