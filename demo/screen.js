(() => {
  const $ = (selector) => document.querySelector(selector);
  const roomId = new URLSearchParams(window.location.search).get('room_id') || '';
  const tokenKey = `campus-screen-token:${roomId}`;
  const form = $('#token-form');
  const codePanel = $('#code-panel');
  let refreshTimer;

  function message(text) {
    $('#screen-message').textContent = text;
  }

  async function refreshCode() {
    const token = window.localStorage.getItem(tokenKey);
    if (!roomId || !token) {
      form.hidden = false;
      $('#change-token').hidden = true;
      codePanel.hidden = true;
      message(roomId ? '输入管理员为这间自习室配置的屏幕令牌。' : '链接缺少 room_id，请联系管理员。');
      return;
    }
    try {
      const response = await fetch(`/api/v1/rooms/${encodeURIComponent(roomId)}/screen-code`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: 'no-store',
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || '屏幕令牌无效');
      $('#room-name').textContent = body.room_name;
      $('#screen-date').textContent = `日期：${body.date}`;
      $('#daily-code').textContent = body.code;
      $('#expires-at').textContent = `本码有效至 ${body.expires_at}`;
      form.hidden = true;
      $('#change-token').hidden = false;
      codePanel.hidden = false;
      message('屏幕连接正常');
    } catch (error) {
      codePanel.hidden = true;
      form.hidden = false;
      $('#change-token').hidden = true;
      message(error.message);
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    window.localStorage.setItem(tokenKey, $('#screen-token').value.trim());
    $('#screen-token').value = '';
    await refreshCode();
  });
  $('#change-token').addEventListener('click', () => {
    window.localStorage.removeItem(tokenKey);
    refreshCode();
  });
  refreshCode();
  refreshTimer = window.setInterval(refreshCode, 60_000);
  window.addEventListener('pagehide', () => window.clearInterval(refreshTimer));
})();
