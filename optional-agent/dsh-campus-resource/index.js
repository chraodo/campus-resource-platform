import { defineTool } from '@deepseek-ai/dsh-tools';

export const name = 'campus-resource-agent';
export const inject = ['tools'];

const ACTION_TYPES = new Set([
  'reserve_seat',
  'submit_room_application',
  'report_fault',
  'cancel_reservation',
  'cancel_classroom_application',
  'cancel_work_order',
]);

function getEndpoint() {
  const raw = process.env.CAMPUS_API_BASE_URL;
  if (!raw) throw new Error('未设置 CAMPUS_API_BASE_URL');
  const url = new URL(raw);
  const local = ['localhost', '127.0.0.1', '::1'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(local && url.protocol === 'http:')) {
    throw new Error('校园平台 API 必须使用 HTTPS；本机回环地址可用 HTTP');
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error('CAMPUS_API_BASE_URL 不能包含账号、密码、查询参数或片段');
  }
  return url.toString().replace(/\/$/, '');
}

async function apiRequest(path, exec, { method = 'GET', body } = {}) {
  const token = process.env.CAMPUS_API_TOKEN;
  if (!token) throw new Error('未设置 CAMPUS_API_TOKEN');
  const response = await fetch(new URL(path, `${getEndpoint()}/`), {
    method,
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: exec.signal,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string' ? payload.detail : '';
    throw new Error(`校园平台返回 HTTP ${response.status}${detail ? `：${detail}` : ''}`);
  }
  return payload;
}

function addTool(ctx, definition) {
  ctx.tools.register(defineTool({
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => [{ type: 'text', text: JSON.stringify(value, null, 2) }],
    },
    ...definition,
  }));
}

export function apply(ctx) {
  addTool(ctx, {
    name: 'campus_search_seats',
    description: '查询指定日期和时段的座位、电源/无障碍筛选及 M1/M2/M3 占用来源。如果用户没有说清日期、开始时间或时长，先追问；不要猜。只查询，不会预约。',
    parameters: {
      date: { type: 'string', required: true, description: '学校本地日期，YYYY-MM-DD' },
      start_time: { type: 'string', required: true, description: '整点时间，HH:00' },
      duration_hours: { type: 'number', required: true, description: '1 到系统上限的整数小时' },
      area: { type: 'string', description: '区域名称，可省略' },
      has_power: { type: 'boolean', description: '只看有电源的座位' },
      accessible: { type: 'boolean', description: '只看无障碍座位' },
    },
    async execute(args, exec) {
      const query = new URLSearchParams({
        date: args.date,
        start_time: args.start_time,
        duration_hours: String(args.duration_hours),
      });
      for (const key of ['area', 'has_power', 'accessible']) {
        if (args[key] !== undefined) query.set(key, String(args[key]));
      }
      return apiRequest(`/api/v1/seats?${query}`, exec);
    },
  });

  addTool(ctx, {
    name: 'campus_my_reservations',
    description: '查看当前登录账号有权限查看的座位预约。不要查询他人信息。',
    parameters: {},
    async execute(_args, exec) {
      return { reservations: await apiRequest('/api/v1/reservations', exec) };
    },
  });

  addTool(ctx, {
    name: 'campus_my_classroom_applications',
    description: '查看当前登录账号有权限查看的整教室申请及审批状态。',
    parameters: {},
    async execute(_args, exec) {
      return { applications: await apiRequest('/api/v1/classroom-applications', exec) };
    },
  });

  addTool(ctx, {
    name: 'campus_my_work_orders',
    description: '查看当前登录账号有权限查看的物业报修工单及处理状态。',
    parameters: {},
    async execute(_args, exec) {
      return { work_orders: await apiRequest('/api/v1/work-orders', exec) };
    },
  });

  addTool(ctx, {
    name: 'campus_preview_write',
    description: '为预约座位、提交教室申请、报修或取消本人单据生成待确认摘要。若请求信息不全，先追问；不得自行补日期、时间、用途或取消对象。必须把摘要原样告诉用户，并等待用户明确同意后，才可调用 campus_confirm_write。',
    parameters: {
      action_type: {
        type: 'string',
        required: true,
        description: 'reserve_seat、submit_room_application、report_fault、cancel_reservation、cancel_classroom_application 或 cancel_work_order',
      },
      payload: {
        type: 'object',
        required: true,
        description: '对应业务所需字段；预约/教室申请/报修参数遵循公开 API 文档',
        additionalProperties: true,
      },
    },
    async execute(args, exec) {
      if (!ACTION_TYPES.has(args.action_type)) throw new Error('不支持的业务操作');
      return apiRequest('/api/v1/agent/actions/preview', exec, {
        method: 'POST',
        body: { action_type: args.action_type, payload: args.payload },
      });
    },
  });

  addTool(ctx, {
    name: 'campus_confirm_write',
    description: '确认并执行一个待处理操作。仅当用户在看到该操作的完整摘要后，明确表示同意这一个操作，才允许调用；不要把此前的笼统授权当成本次确认。用户拒绝或未回答时不能调用。',
    parameters: {
      action_id: { type: 'string', required: true, description: '预览工具返回的待确认操作编号' },
      confirmed: { type: 'boolean', required: true, description: '用户明确同意时才传 true' },
    },
    async execute(args, exec) {
      if (args.confirmed !== true) return { status: 'not_confirmed', executed: false };
      return apiRequest(`/api/v1/agent/actions/${encodeURIComponent(args.action_id)}/confirm`, exec, {
        method: 'POST',
        body: { confirmed: true },
      });
    },
  });
}
