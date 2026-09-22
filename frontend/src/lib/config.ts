/**
 * 全局客户端配置
 *
 * 现阶段用户登录尚未实现，user_id 由前端 hardcode 传递，服务端直接
 * 依据该 user_id 返回其名下会话。下一阶段接入登录后，只需把
 * CURRENT_USER_ID 换成从登录态/Token 解析出的真实用户 ID，
 * 其余代码无需改动（所有请求都从这里取值）。
 */

/** FastAPI 后端地址 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

/**
 * 当前用户 ID（临时 hardcode）
 *
 * 与后端种子脚本 scripts/seed_demo_data.py 的 DEMO_USER_ID 保持一致，
 * 这样启动即可看到演示会话数据。
 */
export const CURRENT_USER_ID = 'userid_1';

/** 默认使用的 agent key，对应后端注册表中的 market_researcher */
export const DEFAULT_AGENT_KEY = 'market_researcher';

/** 会话列表每页条数 */
export const SESSION_PAGE_SIZE = 30;
