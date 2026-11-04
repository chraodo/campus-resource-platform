(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const roleNames = {
    student: '学生', approver_l1: '一级审批人', approver_l2: '二级审批人',
    repair: '维修人员', admin: '管理员',
  };
  const stateNames = {
    confirmed: '待签到', checked_in: '已签到', cancelled: '已取消', expired: '已过期',
    pending: '审批中', approved: '已通过', rejected: '已驳回', accepted: '已验收',
    submitted: '待派单', assigned: '已派单', in_repair: '维修中', resolved: '待验收',
  };
  const stateClass = (status) => ({
    rejected: 'closed', cancelled: 'closed', expired: 'closed', pending: 'warning',
    submitted: 'warning', assigned: 'warning', in_repair: 'warning', resolved: 'warning',
  }[status] || '');
  const stateName = (status) => stateNames[status] || status;
  let currentUser = null;
  let rooms = [];
  let roleOptions = [];
  let roleDefinitions = [];
  let maxBookingHours = 4;
  let permissionCatalog = [];
  let seatResults = [];
  let toastTimer;

  function hasPermission(permission) {
    const permissions = currentUser?.permissions || [];
    return permissions.includes('*') || permissions.includes(permission);
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
  }

  function showToast(message) {
    const element = $('#toast');
    element.textContent = message;
    element.classList.add('show');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => element.classList.remove('show'), 3400);
  }

  function showMessage(message = '') {
    $('#page-message').textContent = message;
    if (message) showToast(message);
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body !== undefined && !headers.has('content-type')) {
      headers.set('content-type', 'application/json');
    }
    const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
    if (response.status === 204) return null;
    const text = await response.text();
    let payload = null;
    if (text) {
      try { payload = JSON.parse(text); } catch { payload = text; }
    }
    if (!response.ok) {
      const detail = payload?.detail;
      const message = typeof detail === 'string'
        ? detail
        : (detail?.message || (detail ? JSON.stringify(detail) : `请求失败（${response.status}）`));
      throw new Error(message);
    }
    return payload;
  }

  function jsonOptions(method, body) {
    return { method, body: JSON.stringify(body) };
  }

  function localDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  function dateTimeInput(date) {
    return `${localDate(date)}T${String(date.getHours()).padStart(2, '0')}:00`;
  }

  function formatDate(value) {
    const date = new Date(value);
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'long', day: 'numeric', weekday: 'short',
    }).format(date);
  }

  function timeRange(start, end) {
    return `${escapeHtml(start.replace('T', ' '))}–${escapeHtml(end.slice(-5))}`;
  }

  function emptyState(message) {
    return `<div class="empty-state">${escapeHtml(message)}</div>`;
  }

  function recordCard({ title, meta = '', status = '', statusLabel = '', actions = '', middle = '' }) {
    return `<article class="record-card">
      <div><div class="record-title">${escapeHtml(title)}</div><div class="record-meta">${meta}</div></div>
      <div class="record-meta">${middle}</div>
      <div class="record-actions">${status ? `<span class="record-status ${stateClass(status)}">${escapeHtml(statusLabel || stateName(status))}</span>` : ''}${actions}</div>
    </article>`;
  }

  function button(label, action, id, extra = '', danger = false) {
    return `<button class="small-button ${danger ? 'danger' : ''}" type="button" data-action="${escapeHtml(action)}" data-id="${escapeHtml(id)}" ${extra}>${escapeHtml(label)}</button>`;
  }

  function setPage(page) {
    $$('.content-panel').forEach((panel) => { panel.hidden = panel.dataset.page !== page; });
    $$('.nav-button').forEach((item) => item.classList.toggle('active', item.dataset.page === page));
    if (page === 'seats') refreshSeatSearch();
    if (page === 'classrooms') refreshApplications();
    if (page === 'repairs') refreshWorkOrders();
    if (page === 'operations') refreshOperations();
    if (page === 'admin') refreshAdmin();
    if (page === 'account') refreshAccount();
  }

  function renderNavigation() {
    const pages = [
      ['seats', '座位预约', hasPermission('reservation:create') || hasPermission('reservation:read_all')],
      ['classrooms', '教室申请', hasPermission('classroom:create') || hasPermission('classroom:read_own') || hasPermission('classroom:read_all')],
      ['repairs', '物业报修', hasPermission('work_order:create') || hasPermission('work_order:read_own') || hasPermission('work_order:read_all')],
      ['operations', '审批与维修', hasPermission('classroom:approve') || hasPermission('work_order:assign') || hasPermission('work_order:repair') || hasPermission('work_order:read_all')],
      ['admin', '系统管理', hasPermission('user:manage') || hasPermission('resource:manage') || hasPermission('metrics:read') || hasPermission('audit:read') || hasPermission('reservation:read_all')],
      ['account', '账号与通知', hasPermission('token:manage') || hasPermission('notification:read')],
    ].filter(([, , visible]) => visible);
    $('#main-nav').innerHTML = pages.map(([page, label], index) =>
      `<button type="button" class="nav-button ${index === 0 ? 'active' : ''}" data-page="${page}">${label}</button>`,
    ).join('');
    $('#main-nav').onclick = (event) => {
      const nav = event.target.closest('[data-page]');
      if (nav) setPage(nav.dataset.page);
    };
    $$('.content-panel').forEach((panel) => {
      const visible = pages.some(([page]) => panel.dataset.page === page);
      panel.hidden = !visible || panel.dataset.page !== pages[0][0];
    });
  }

  async function loadDirectory() {
    const [roomRows, roles, settings] = await Promise.all([
      api('/api/v1/rooms'), api('/api/v1/roles'), api('/api/v1/public-settings'),
    ]);
    rooms = roomRows;
    roleOptions = roles;
    maxBookingHours = settings.max_booking_hours;
    $('#seat-duration').innerHTML = Array.from({ length: maxBookingHours }, (_, index) => index + 1)
      .map((hours) => `<option value="${hours}">${hours} 小时</option>`).join('');
    $('#booking-duration-hint').textContent = `每次最多 ${maxBookingHours} 小时，整点开始`;
    $('#new-user-role').innerHTML = roleOptions.map((role) =>
      `<option value="${escapeHtml(role.code)}">${escapeHtml(role.name)}</option>`,
    ).join('');
    const areas = [...new Set(rooms.map((room) => room.area))];
    $('#seat-area').innerHTML = '<option value="">全部区域</option>' + areas.map((area) =>
      `<option value="${escapeHtml(area)}">${escapeHtml(area)}</option>`,
    ).join('');
    const classrooms = rooms.filter((room) => room.room_type === 'classroom');
    $('#classroom-room').innerHTML = classrooms.map((room) =>
      `<option value="${escapeHtml(room.id)}">${escapeHtml(room.name)} · ${escapeHtml(room.area)}</option>`,
    ).join('');
    $('#new-seat-room').innerHTML = rooms.map((room) =>
      `<option value="${escapeHtml(room.id)}">${escapeHtml(room.name)}</option>`,
    ).join('');
    $('#screen-room').innerHTML = rooms.map((room) =>
      `<option value="${escapeHtml(room.id)}">${escapeHtml(room.name)}</option>`,
    ).join('');
    const seatResponse = await api('/api/v1/seats?date=' + encodeURIComponent($('#seat-date').value)
      + '&start_time=10:00&duration_hours=1');
    const seatOptions = seatResponse.seats.map((seat) =>
      `<option value="seat:${escapeHtml(seat.id)}">座位 ${escapeHtml(seat.label)} · ${escapeHtml(seat.room_name)}</option>`,
    );
    $('#fault-target').innerHTML = [
      '<optgroup label="座位">' + seatOptions.join('') + '</optgroup>',
      '<optgroup label="房间">' + rooms.map((room) =>
        `<option value="room:${escapeHtml(room.id)}">房间 ${escapeHtml(room.name)}</option>`,
      ).join('') + '</optgroup>',
    ].join('');
  }

  function buildSeatQuery() {
    const query = new URLSearchParams({
      date: $('#seat-date').value,
      start_time: $('#seat-time').value || '10:00',
      duration_hours: $('#seat-duration').value,
    });
    if ($('#seat-area').value) query.set('area', $('#seat-area').value);
    if ($('#seat-power').checked) query.set('has_power', 'true');
    return query;
  }

  async function refreshSeatSearch() {
    if (!currentUser) return;
    const grid = $('#seat-grid');
    grid.innerHTML = emptyState('正在查询可预约座位…');
    try {
      const result = await api(`/api/v1/seats?${buildSeatQuery()}`);
      seatResults = result.seats;
      $('#seat-summary').innerHTML = `<span><strong>${result.available_count}</strong> 个座位当前可预约</span><span>${escapeHtml(result.start_at.replace('T', ' '))}–${escapeHtml(result.end_at.slice(-5))}</span>`;
      renderSeatGrid();
    } catch (error) {
      grid.innerHTML = emptyState(error.message);
      showMessage(error.message);
    }
  }

  function renderSeatGrid() {
    const grid = $('#seat-grid');
    if (!seatResults.length) {
      grid.innerHTML = emptyState('当前筛选条件下没有座位。');
      return;
    }
    grid.innerHTML = seatResults.map((seat) => {
      const source = seat.blocked_by;
      const detail = source
        ? `${source.module} · ${source.source_id} · ${source.reason}`
        : (seat.has_power ? '有电源' : '普通座位');
      return `<button class="seat-card ${seat.available ? 'available' : 'occupied'}" type="button" data-seat="${escapeHtml(seat.id)}" ${seat.available ? '' : 'disabled'}>
        <span><span class="seat-name">${escapeHtml(seat.label)}</span><span class="seat-detail">${escapeHtml(seat.room_name)} · ${escapeHtml(seat.area)}${seat.has_power ? ' · 电源' : ''}</span></span>
        <span class="seat-state ${seat.available ? '' : 'blocked'}">${seat.available ? '可预约' : '已占用'}<br><span>${escapeHtml(detail)}</span></span>
      </button>`;
    }).join('');
  }

  async function refreshReservations() {
    try {
      const rows = await api('/api/v1/reservations');
      const active = rows.filter((row) => ['confirmed', 'checked_in'].includes(row.status)).length;
      $('#reservation-list').innerHTML = rows.length ? rows.map((row) => {
        let actions = '';
        if (row.status === 'confirmed') {
          actions = button('签到地点', 'room-info', row.id)
            + button('输入屏幕今日码', 'check-in', row.id)
            + button('取消预约', 'cancel-reservation', row.id, '', true);
        }
        actions += button('复用条件', 'reuse-reservation', row.id);
        const meta = `${escapeHtml(row.id)} · ${escapeHtml(row.seat_id)} · ${escapeHtml(row.room_name)}`;
        return recordCard({
          title: `座位 ${row.seat_label} · ${timeRange(row.start_at, row.end_at)}`,
          meta,
          middle: `${row.duration_hours} 小时${row.violation ? ' · 违约记录' : ''}`,
          status: row.status,
          actions,
        });
      }).join('') : emptyState('还没有预约记录。');
      if ($('#booking-count')) $('#booking-count').textContent = String(active);
    } catch (error) { $('#reservation-list').innerHTML = emptyState(error.message); }
  }

  async function createReservation(seatId) {
    const response = await api('/api/v1/reservations', {
      ...jsonOptions('POST', {
      seat_id: seatId,
      start_at: `${$('#seat-date').value}T${$('#seat-time').value || '10:00'}:00`,
      duration_hours: Number($('#seat-duration').value),
      }),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    });
    showMessage(`预约已创建：${seatId}。签到时请使用预约所在自习室屏幕上的当日动态码。`);
    await Promise.all([refreshSeatSearch(), refreshReservations(), refreshNotifications()]);
  }

  async function refreshApplications(container = '#classroom-list', pendingOnly = false) {
    if (!currentUser) return;
    try {
      const rows = await api('/api/v1/classroom-applications');
      const visible = pendingOnly ? rows.filter((row) => row.status === 'pending') : rows;
      $(container).innerHTML = visible.length ? visible.map((row) => {
        let actions = '';
        if ((hasPermission('classroom:manage_all')
          || (row.applicant_id === currentUser.id && hasPermission('classroom:cancel_own')))
          && (row.status === 'pending' || row.status === 'approved')) {
          actions += button('取消申请', 'cancel-application', row.id, '', true);
        }
        const stageAllowed = hasPermission('classroom:approve:any')
          || hasPermission(`classroom:approve:${row.current_stage}`);
        if (pendingOnly && row.status === 'pending' && hasPermission('classroom:approve') && stageAllowed) {
          actions += button('通过', 'approve-application', row.id)
            + button('驳回', 'reject-application', row.id, '', true);
        }
        const approvals = row.approvals.map((item) =>
          `第${item.stage}级${item.decision === 'approve' ? '通过' : '驳回'}（${escapeHtml(item.approver_name)}）`,
        ).join('；') || '等待审批';
        return recordCard({
          title: `${row.room_name} · ${timeRange(row.start_at, row.end_at)}`,
          meta: `${escapeHtml(row.id)} · 申请人 ${escapeHtml(row.applicant_name)} · ${escapeHtml(row.purpose)}`,
          middle: approvals,
          status: row.status,
          actions,
        });
      }).join('') : emptyState(pendingOnly ? '当前没有待审批申请。' : '还没有教室申请记录。');
    } catch (error) { $(container).innerHTML = emptyState(error.message); }
  }

  async function refreshWorkOrders(container = '#work-order-list') {
    if (!currentUser) return;
    try {
      const rows = await api('/api/v1/work-orders');
      $(container).innerHTML = rows.length ? rows.map((row) => {
        let actions = '';
        const isOwner = row.reporter_id === currentUser.id;
        const canManage = hasPermission('work_order:manage_all');
        if (row.status === 'submitted' && hasPermission('work_order:assign')) {
          actions += button('认领工单', 'assign-self', row.id);
        }
        if (['submitted', 'assigned'].includes(row.status) && hasPermission('work_order:assign:any')) {
          actions += button('派给维修人员', 'assign-worker', row.id);
        }
        if (row.status === 'assigned' && row.assignee_id === currentUser.id && hasPermission('work_order:repair')) {
          actions += button('开始维修', 'start-repair', row.id);
        }
        if (row.status === 'in_repair' && hasPermission('work_order:repair')
          && (canManage || row.assignee_id === currentUser.id)) {
          actions += button('提交维修完成', 'resolve-repair', row.id);
        }
        if (row.status === 'resolved' && (canManage || (isOwner && hasPermission('work_order:accept_own')))) {
          actions += button('验收恢复', 'accept-repair', row.id);
        }
        if (row.status === 'accepted' && row.rating === null && isOwner && hasPermission('work_order:rate_own')) {
          actions += button('评价维修', 'rate-repair', row.id);
        }
        if (['submitted', 'assigned'].includes(row.status) && isOwner && hasPermission('work_order:cancel_own')) {
          actions += button('取消报修', 'cancel-work-order', row.id, '', true);
        }
        return recordCard({
          title: `${row.target_label} · ${row.title}`,
          meta: `${escapeHtml(row.id)} · 报修人 ${escapeHtml(row.reporter_name)} · ${escapeHtml(row.description)}`,
          middle: `等级 ${escapeHtml(row.severity)} · 维修人 ${escapeHtml(row.assignee_name || '待分配')} · 时限 ${escapeHtml(row.due_at)}`,
          status: row.status,
          actions,
        });
      }).join('') : emptyState('还没有报修工单。');
    } catch (error) { $(container).innerHTML = emptyState(error.message); }
  }

  async function refreshOperations() {
    const canApprove = hasPermission('classroom:approve');
    const canRepair = hasPermission('work_order:read_all') || hasPermission('work_order:assign')
      || hasPermission('work_order:repair');
    $('#approval-list').closest('.subsection-heading').hidden = !canApprove;
    $('#approval-list').hidden = !canApprove;
    $('#repair-desk-list').closest('.subsection-heading').hidden = !canRepair;
    $('#repair-desk-list').hidden = !canRepair;
    if (canApprove) await refreshApplications('#approval-list', true);
    if (canRepair) await refreshWorkOrders('#repair-desk-list');
  }

  async function refreshAdmin() {
    const canManageUsers = hasPermission('user:manage');
    const canManageResources = hasPermission('resource:manage');
    const canReadMetrics = hasPermission('metrics:read');
    const canReadAudit = hasPermission('audit:read');
    const canReadReservations = hasPermission('reservation:read_all');
    const canConfigure = hasPermission('system:config');
    $('#user-form').hidden = !canManageUsers;
    $('#role-form').hidden = !canManageUsers;
    $('#room-form').hidden = !canManageResources;
    $('#seat-form').hidden = !canManageResources;
    $('#screen-token-form').hidden = !canManageResources;
    $('#settings-form').hidden = !canConfigure;
    $('#admin-metrics').hidden = !canReadMetrics;
    $('#admin-grid').hidden = ![canManageUsers, canManageResources, canConfigure].some(Boolean);
    const hideSection = (selector, visible) => {
      const list = $(selector);
      list.hidden = !visible;
      if (list.previousElementSibling?.classList.contains('subsection-heading')) {
        list.previousElementSibling.hidden = !visible;
      }
    };
    hideSection('#user-list', canManageUsers);
    hideSection('#role-list', canManageUsers);
    hideSection('#audit-list', canReadAudit);
    hideSection('#admin-reservation-list', canReadReservations);
    try {
      if (canReadMetrics) {
        const counts = await api('/api/v1/admin/overview');
        $('#admin-metrics').innerHTML = Object.entries({
          users: '账号', rooms: '空间', seats: '座位', active_reservations: '有效预约',
          pending_applications: '待审批', open_work_orders: '未完成工单',
        }).map(([key, label]) => `<article class="metric-card"><div class="metric-value">${counts[key]}</div><div class="metric-label">${label}</div></article>`).join('');
      }
      if (canConfigure) {
        const settings = await api('/api/v1/admin/settings');
        maxBookingHours = settings.max_booking_hours;
        $('#max-booking-hours').innerHTML = Array.from({ length: 24 }, (_, index) => index + 1)
          .map((hours) => `<option value="${hours}">${hours} 小时</option>`).join('');
        $('#max-booking-hours').value = String(settings.max_booking_hours);
      }
      if (canManageUsers) {
        const [users, roles, catalog] = await Promise.all([
          api('/api/v1/admin/users'), api('/api/v1/admin/roles'), api('/api/v1/admin/permission-catalog'),
        ]);
        permissionCatalog = catalog;
        roleDefinitions = roles;
        roleOptions = roles.filter((role) => role.active).map((role) => ({ code: role.code, name: role.name }));
        $('#new-user-role').innerHTML = roleOptions.map((role) =>
          `<option value="${escapeHtml(role.code)}">${escapeHtml(role.name)}</option>`,
        ).join('');
        $('#user-list').innerHTML = users.map((person) => recordCard({
          title: `${person.display_name}（${person.username}）`,
          meta: `账号编号 ${person.id}`,
          middle: `${person.role_name || roleNames[person.role] || person.role} · ${person.active ? '启用' : '停用'}`,
          actions: `<select class="inline-code" data-user-role="${person.id}" aria-label="修改${escapeHtml(person.username)}角色">${roleOptions.map((role) => `<option value="${escapeHtml(role.code)}" ${role.code === person.role ? 'selected' : ''}>${escapeHtml(role.name)}</option>`).join('')}</select>${button(person.active ? '停用' : '启用', 'toggle-user', person.id)}`,
        })).join('');
        $('#role-list').innerHTML = roles.map((role) => recordCard({
          title: `${role.name}（${role.code}）`,
          meta: role.system ? '内置角色' : '自定义角色',
          middle: `${role.permissions.join(' · ')} · ${role.active ? '启用' : '停用'}`,
          actions: role.code === 'admin' ? '' : `${button('配置权限', 'edit-role', role.code)}${button(role.active ? '停用' : '启用', 'toggle-role', role.code)}`,
        })).join('');
        $('#permission-catalog').innerHTML = catalog.map((item) =>
          `<div><code>${escapeHtml(item.code)}</code> · ${escapeHtml(item.label)}</div>`,
        ).join('');
      }
      if (canReadAudit) {
        const audit = await api('/api/v1/admin/audit?limit=30');
        $('#audit-list').innerHTML = audit.length ? audit.map((row) => recordCard({
          title: `${row.action} · ${row.entity_type} ${row.entity_id}`,
          meta: `${row.actor} · ${row.created_at}`,
          middle: escapeHtml(JSON.stringify(row.details || {})),
        })).join('') : emptyState('还没有操作记录。');
      }
      if (canReadReservations) {
        const reservations = await api('/api/v1/admin/reservations');
        $('#admin-reservation-list').innerHTML = reservations.length ? reservations.map((row) => recordCard({
          title: `${row.student_name} · ${row.room_name} · ${row.seat_label}`,
          meta: `${row.id} · ${row.start_at}–${row.end_at.slice(-5)}`,
          middle: `${row.duration_hours} 小时${row.violation ? ' · 有违约记录' : ''}`,
          status: row.status,
        })).join('') : emptyState('目前没有预约记录。');
      }
      $('#new-seat-room').innerHTML = rooms.map((room) =>
        `<option value="${escapeHtml(room.id)}">${escapeHtml(room.name)}</option>`,
      ).join('');
    } catch (error) { $('#admin-metrics').innerHTML = emptyState(error.message); }
  }

  async function refreshAccount() {
    await Promise.all([refreshTokens(), refreshNotifications()]);
  }

  async function refreshTokens() {
    try {
      const tokens = await api('/api/v1/auth/tokens');
      $('#token-list').innerHTML = tokens.length ? tokens.map((token) => recordCard({
        title: token.label,
        meta: `创建于 ${token.created_at} · 到期 ${token.expires_at}`,
        middle: '密钥只在创建时显示',
        actions: button('撤销', 'revoke-token', token.id, '', true),
      })).join('') : emptyState('还没有个人 API 令牌。');
    } catch (error) { $('#token-list').innerHTML = emptyState(error.message); }
  }

  async function refreshNotifications() {
    if (!currentUser) return;
    if (!hasPermission('notification:read')) {
      $('#notification-count').textContent = '0';
      if ($('#notification-list')) $('#notification-list').innerHTML = emptyState('当前角色不能查看通知。');
      return;
    }
    try {
      const rows = await api('/api/v1/notifications');
      $('#notification-count').textContent = String(rows.filter((row) => !row.read).length);
      const list = $('#notification-list');
      if (list) {
        list.innerHTML = rows.length ? rows.map((row) => recordCard({
          title: row.title,
          meta: `${row.created_at} · ${row.related_id}`,
          middle: row.message,
          status: row.read ? 'accepted' : 'pending',
          statusLabel: row.read ? '已读' : '未读',
          actions: row.read ? '' : button('标为已读', 'read-notification', row.id),
        })).join('') : emptyState('暂时没有通知。');
      }
    } catch (error) { if ($('#notification-list')) $('#notification-list').innerHTML = emptyState(error.message); }
  }

  async function activateSession(user) {
    currentUser = user;
    $('#login-view').hidden = true;
    $('#app-view').hidden = false;
    $('#header-user').hidden = false;
    $('#user-badge').textContent = `${user.display_name} · ${user.role_name || roleNames[user.role] || user.role}`;
    await loadDirectory();
    renderNavigation();
    $('#seat-search-form').hidden = !hasPermission('reservation:create');
    $('#classroom-form').hidden = !hasPermission('classroom:create');
    $('#work-order-form').hidden = !hasPermission('work_order:create');
    $('#token-form').hidden = !hasPermission('token:manage');
    $('#token-list').hidden = !hasPermission('token:manage');
    $('#refresh-notifications').hidden = !hasPermission('notification:read');
    if (!hasPermission('notification:read')) {
      $('#notification-list').hidden = true;
      $('#notification-list').previousElementSibling.hidden = true;
    }
    setPage($$('.nav-button')[0]?.dataset.page || 'account');
    await refreshNotifications();
  }

  async function checkSession() {
    try {
      const result = await api('/api/v1/auth/me');
      await activateSession(result.user);
    } catch {
      $('#login-view').hidden = false;
      $('#app-view').hidden = true;
      $('#header-user').hidden = true;
    }
  }

  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    $('#login-message').textContent = '';
    try {
      const result = await api('/api/v1/auth/login', jsonOptions('POST', {
        username: $('#username').value.trim(), password: $('#password').value,
      }));
      await activateSession(result.user);
    } catch (error) { $('#login-message').textContent = error.message; }
  });

  $$('[data-demo-user]').forEach((buttonEl) => {
    buttonEl.addEventListener('click', () => {
      $('#username').value = buttonEl.dataset.demoUser;
      $('#password').value = 'demo2026';
      $('#login-form').requestSubmit();
    });
  });

  $('#logout-button').addEventListener('click', async () => {
    try { await api('/api/v1/auth/logout', { method: 'POST' }); } catch { /* Clear the local view either way. */ }
    currentUser = null;
    $('#app-view').hidden = true;
    $('#header-user').hidden = true;
    $('#login-view').hidden = false;
  });

  $('#seat-search-form').addEventListener('submit', (event) => {
    event.preventDefault();
    refreshSeatSearch();
  });
  ['#seat-date', '#seat-time', '#seat-duration', '#seat-area', '#seat-power'].forEach((selector) => {
    $(selector).addEventListener('change', refreshSeatSearch);
  });
  $('#seat-grid').addEventListener('click', async (event) => {
    const target = event.target.closest('[data-seat]');
    if (!target || target.disabled) return;
    try { await createReservation(target.dataset.seat); }
    catch (error) { showMessage(error.message); await refreshSeatSearch(); }
  });
  $('#refresh-reservations').addEventListener('click', refreshReservations);

  $('#classroom-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/v1/classroom-applications', jsonOptions('POST', {
        room_id: $('#classroom-room').value,
        start_at: $('#classroom-start').value,
        end_at: $('#classroom-end').value,
        purpose: $('#classroom-purpose').value.trim(),
      }));
      event.target.reset();
      showMessage('教室申请已提交，审批完成前不会占用该教室。');
      await Promise.all([refreshApplications(), refreshNotifications()]);
    } catch (error) { showMessage(error.message); }
  });
  $('#refresh-classrooms').addEventListener('click', refreshApplications);

  $('#work-order-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const target = $('#fault-target').value;
    const body = {
      seat_id: target.startsWith('seat:') ? target.slice(5) : null,
      room_id: target.startsWith('room:') ? target.slice(5) : null,
      title: $('#fault-title').value.trim(),
      description: $('#fault-description').value.trim(),
      severity: $('#fault-severity').value,
    };
    try {
      await api('/api/v1/work-orders', jsonOptions('POST', body));
      event.target.reset();
      showMessage('报修已提交。维修人员接单并开始维修后，受影响资源会暂时下架。');
      await Promise.all([refreshWorkOrders(), refreshNotifications()]);
    } catch (error) { showMessage(error.message); }
  });
  $('#refresh-work-orders').addEventListener('click', refreshWorkOrders);

  $('#refresh-operations').addEventListener('click', refreshOperations);
  $('#refresh-admin').addEventListener('click', refreshAdmin);
  $('#refresh-notifications').addEventListener('click', refreshNotifications);

  $('#room-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/v1/admin/rooms', jsonOptions('POST', {
        id: $('#new-room-id').value.trim(), name: $('#new-room-name').value.trim(),
        area: $('#new-room-area').value.trim(), room_type: $('#new-room-type').value,
        access_scope: $('#new-room-scope').value,
        department_id: $('#new-room-department').value.trim() || null,
        capacity: Number($('#new-room-capacity').value), open_time: $('#new-room-open').value,
        close_time: $('#new-room-close').value,
      }));
      event.target.reset();
      rooms = await api('/api/v1/rooms');
      await loadDirectory();
      showMessage('空间已加入公共资源目录。');
      await refreshAdmin();
    } catch (error) { showMessage(error.message); }
  });
  $('#seat-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/v1/admin/seats', jsonOptions('POST', {
        id: $('#new-seat-id').value.trim(), room_id: $('#new-seat-room').value,
        label: $('#new-seat-label').value.trim(), has_power: $('#new-seat-power').checked,
        accessible: $('#new-seat-accessible').checked,
      }));
      event.target.reset();
      showMessage('座位已加入公共资源目录。');
      await refreshAdmin();
    } catch (error) { showMessage(error.message); }
  });
  $('#user-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/v1/admin/users', jsonOptions('POST', {
        username: $('#new-user-name').value.trim(), display_name: $('#new-user-display').value.trim(),
        email: $('#new-user-email').value.trim() || null,
        password: $('#new-user-password').value, role: $('#new-user-role').value,
        department_id: $('#new-user-department').value.trim() || null,
      }));
      event.target.reset();
      showMessage('账号已创建。');
      await refreshAdmin();
    } catch (error) { showMessage(error.message); }
  });

  $('#settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const result = await api('/api/v1/admin/settings', jsonOptions('PUT', {
        max_booking_hours: Number($('#max-booking-hours').value),
      }));
      maxBookingHours = result.max_booking_hours;
      $('#seat-duration').innerHTML = Array.from({ length: maxBookingHours }, (_, index) => index + 1)
        .map((hours) => `<option value="${hours}">${hours} 小时</option>`).join('');
      $('#booking-duration-hint').textContent = `每次最多 ${maxBookingHours} 小时，整点开始`;
      showMessage(`每次最长预约已设为 ${maxBookingHours} 小时。`);
    } catch (error) { showMessage(error.message); }
  });

  $('#screen-token-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const roomId = $('#screen-room').value;
    try {
      const result = await api(`/api/v1/admin/rooms/${encodeURIComponent(roomId)}/screen-token`, { method: 'POST' });
      const display = $('#screen-token-created');
      display.hidden = false;
      display.innerHTML = `令牌只显示这一次。请安全配置到 ${escapeHtml(result.room_name)} 的屏幕浏览器。<code class="token-value">${escapeHtml(result.screen_token)}</code>到期时间：${escapeHtml(result.expires_at)}。屏幕页面：<code>/screen.html?room_id=${escapeHtml(result.room_id)}</code>`;
      showMessage('屏幕专用令牌已生成；旧令牌已失效。');
    } catch (error) { showMessage(error.message); }
  });

  $('#role-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const permissions = $('#new-role-permissions').value.split(',').map((value) => value.trim()).filter(Boolean);
    try {
      await api('/api/v1/admin/roles', jsonOptions('POST', {
        code: $('#new-role-code').value.trim(),
        name: $('#new-role-name').value.trim(),
        permissions,
      }));
      event.target.reset();
      showMessage('自定义角色已创建。');
      await refreshAdmin();
    } catch (error) { showMessage(error.message); }
  });

  $('#token-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      const token = await api('/api/v1/auth/tokens', jsonOptions('POST', { label: $('#token-label').value.trim() }));
      $('#token-created').hidden = false;
      $('#token-created').innerHTML = `令牌只显示这一次，请复制到安全位置。<code class="token-value">${escapeHtml(token.token)}</code>有效期 ${token.expires_in_days} 天。`;
      await refreshTokens();
      showMessage('个人 API 令牌已创建。');
    } catch (error) { showMessage(error.message); }
  });

  document.body.addEventListener('click', async (event) => {
    const target = event.target.closest('[data-action]');
    if (!target) return;
    const { action, id } = target.dataset;
    try {
      if (action === 'cancel-reservation') {
        await api(`/api/v1/reservations/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
        await Promise.all([refreshReservations(), refreshSeatSearch()]);
        showMessage('预约已取消。');
      } else if (action === 'reuse-reservation') {
        const rows = await api('/api/v1/reservations');
        const row = rows.find((item) => item.id === id);
        if (!row) throw new Error('没有找到这条预约记录');
        const start = new Date(row.start_at);
        const now = new Date();
        if (start <= now) start.setDate(now.getDate() + 1);
        $('#seat-date').value = localDate(start);
        $('#seat-time').value = `${String(start.getHours()).padStart(2, '0')}:00`;
        $('#seat-duration').value = String(row.duration_hours);
        $('#seat-area').value = '';
        $('#seat-power').checked = false;
        setPage('seats');
        showMessage(`已复用座位 ${row.seat_id} 和时长 ${row.duration_hours} 小时，时间调整到可选的未来日期。`);
      } else if (action === 'room-info') {
        const result = await api(`/api/v1/reservations/${encodeURIComponent(id)}/checkin-code`);
        window.alert(`请到 ${result.room_name} 查看屏幕上的今日签到码。屏幕码每天更新，只能现场获取。`);
      } else if (action === 'check-in') {
        const code = window.prompt('输入自习室屏幕显示的今日签到码：');
        if (!code) return;
        await api(`/api/v1/reservations/${encodeURIComponent(id)}/check-in`, jsonOptions('POST', { code }));
        showMessage('签到成功。');
        await refreshReservations();
      } else if (action === 'cancel-application') {
        await api(`/api/v1/classroom-applications/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
        showMessage('教室申请已取消。');
        await Promise.all([refreshApplications(), refreshOperations(), refreshSeatSearch()]);
      } else if (action === 'cancel-work-order') {
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
        showMessage('报修工单已取消。');
        await Promise.all([refreshWorkOrders(), refreshNotifications()]);
      } else if (action === 'approve-application' || action === 'reject-application') {
        const decision = action === 'approve-application' ? 'approve' : 'reject';
        const comment = decision === 'reject' ? (window.prompt('填写驳回原因：') || '') : '';
        if (decision === 'reject' && !comment.trim()) return;
        await api(`/api/v1/classroom-applications/${encodeURIComponent(id)}/decision`, jsonOptions('POST', { decision, comment }));
        showMessage(decision === 'approve' ? '本级审批已通过。' : '申请已驳回。');
        await Promise.all([refreshOperations(), refreshApplications(), refreshNotifications(), refreshSeatSearch()]);
      } else if (action === 'assign-self') {
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/assign`, jsonOptions('POST', { assignee_id: currentUser.id }));
        await refreshOperations();
        showMessage('已认领工单。');
      } else if (action === 'assign-worker') {
        const workers = await api('/api/v1/repairers');
        const selection = window.prompt(`输入维修人员账号编号：\n${workers.map((person) => `${person.id} · ${person.display_name}`).join('\n')}`);
        if (!selection) return;
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/assign`, jsonOptions('POST', { assignee_id: Number(selection) }));
        await refreshOperations();
        showMessage('工单已派发。');
      } else if (action === 'start-repair') {
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/start`, { method: 'POST' });
        showMessage('维修已开始，受影响资源已暂时下架。');
        await Promise.all([refreshOperations(), refreshWorkOrders(), refreshSeatSearch()]);
      } else if (action === 'resolve-repair') {
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/resolve`, { method: 'POST' });
        showMessage('已提交维修完成，等待报修人验收。');
        await Promise.all([refreshOperations(), refreshWorkOrders(), refreshNotifications()]);
      } else if (action === 'accept-repair') {
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/accept`, { method: 'POST' });
        showMessage('维修已验收，资源恢复可预约状态。');
        await Promise.all([refreshWorkOrders(), refreshOperations(), refreshSeatSearch()]);
      } else if (action === 'rate-repair') {
        const rating = Number(window.prompt('请给这次维修打分（1–5）：', '5'));
        if (!Number.isInteger(rating) || rating < 1 || rating > 5) return;
        const comment = window.prompt('补充评价（可留空）：') || '';
        await api(`/api/v1/work-orders/${encodeURIComponent(id)}/rating`, jsonOptions('POST', { rating, comment }));
        showMessage('评价已提交。');
        await refreshWorkOrders();
      } else if (action === 'toggle-user') {
        const active = target.textContent.trim() !== '启用';
        await api(`/api/v1/admin/users/${encodeURIComponent(id)}/active`, jsonOptions('PATCH', { active: !active }));
        await refreshAdmin();
      } else if (action === 'edit-role') {
        const role = roleDefinitions.find((item) => item.code === id);
        if (!role) return;
        const name = window.prompt('角色显示名称：', role.name);
        if (!name) return;
        const rawPermissions = window.prompt('权限编号（逗号分隔）：', role.permissions.join(', '));
        if (rawPermissions === null) return;
        const permissions = rawPermissions.split(',').map((value) => value.trim()).filter(Boolean);
        await api(`/api/v1/admin/roles/${encodeURIComponent(id)}`, jsonOptions('PUT', {
          name, permissions, active: role.active,
        }));
        showMessage('角色权限已更新。');
        await refreshAdmin();
      } else if (action === 'toggle-role') {
        const role = roleDefinitions.find((item) => item.code === id);
        if (!role) return;
        await api(`/api/v1/admin/roles/${encodeURIComponent(id)}`, jsonOptions('PUT', {
          name: role.name, permissions: role.permissions, active: !role.active,
        }));
        showMessage(role.active ? '角色已停用。' : '角色已启用。');
        await refreshAdmin();
      } else if (action === 'revoke-token') {
        await api(`/api/v1/auth/tokens/${encodeURIComponent(id)}`, { method: 'DELETE' });
        await refreshTokens();
        showMessage('令牌已撤销。');
      } else if (action === 'read-notification') {
        await api(`/api/v1/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' });
        await refreshNotifications();
      }
    } catch (error) { showMessage(error.message); }
  });

  document.body.addEventListener('change', async (event) => {
    const select = event.target.closest('[data-user-role]');
    if (!select) return;
    try {
      await api(`/api/v1/admin/users/${encodeURIComponent(select.dataset.userRole)}/role`, jsonOptions('PATCH', { role: select.value }));
      showMessage('账号角色已更新。');
      await refreshAdmin();
    } catch (error) { showMessage(error.message); await refreshAdmin(); }
  });

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  $('#seat-date').value = localDate(tomorrow);
  $('#seat-date').min = localDate(new Date());
  const classroomStart = new Date(tomorrow);
  classroomStart.setHours(10, 0, 0, 0);
  const classroomEnd = new Date(tomorrow);
  classroomEnd.setHours(11, 0, 0, 0);
  $('#classroom-start').value = dateTimeInput(classroomStart);
  $('#classroom-end').value = dateTimeInput(classroomEnd);
  checkSession();
})();
