'use strict';
// One browser dialog, one bounded CLI session per account. Never store codes or URLs.
const loginDialog = $('#login-dialog');
const loginTerminal = new Set(['succeeded', 'failed', 'cancelled', 'expired']);
let loginKey = null, loginState = null, loginTimer = null, loginBusy = false, loginGeneration = 0;

async function loginRequest(action, payload = {}) {
 const response = await fetch('/auth/' + encodeURIComponent(loginKey) + (action ? '/' + action : ''), {
  method: action ? 'POST' : 'GET', cache:'no-store',
  headers: action ? {'Content-Type':'application/json', 'X-TokenTV-Request':'1'} : {},
  ...(action ? {body:JSON.stringify(payload)} : {})
 });
 if (!response.ok) throw Error('로그인 서버에 요청하지 못했습니다. 상태를 확인하고 다시 시도해 주세요.');
 return response.json();
}
function paintLogin(data) {
 const previous = loginState;
 loginState = data;
 const active = !loginTerminal.has(data.phase), waiting = data.phase === 'waiting';
 $('#login-title').textContent = data.provider.toUpperCase() + ' 다시 로그인';
 $('#login-account').textContent = `${data.alias} · ${data.email}`;
 $('#login-status').textContent = data.message || '로그인 준비 중입니다.';
 $('#login-expires').textContent = active && data.expires_at ? '남은 시간 ' + Math.max(0, Math.ceil((data.expires_at * 1000 - Date.now()) / 1000)) + '초 · 창을 닫아도 이 시간 동안 다시 이어서 진행할 수 있습니다.' : '';
 $('#login-browser').hidden = !waiting || !data.auth_url;
 const link = $('#login-link');
 if (waiting && data.auth_url) link.href = data.auth_url; else link.removeAttribute('href');
 $('#login-device').hidden = !waiting || data.provider !== 'codex' || !data.device_code;
 $('#login-device-code').value = data.device_code || '';
 $('#login-code-form').hidden = !waiting || data.provider !== 'claude';
 $('#login-submit').disabled = loginBusy;
 $('#login-cancel').hidden = !active;
 $('#login-retry').hidden = !['failed', 'cancelled', 'expired'].includes(data.phase);
 $('#login-close').textContent = data.phase === 'succeeded' ? '완료' : '닫기';
 if (!active) $('#login-code').value = '';
 if (data.phase === 'succeeded' && previous?.phase !== 'succeeded') {update(); refreshDisplay(true)}
}
async function pollLogin(generation) {
 if (!loginDialog.open || generation !== loginGeneration) return;
 if (loginBusy) {loginTimer = setTimeout(() => pollLogin(generation), 300); return}
 try {
  const data = await loginRequest();
  if (generation !== loginGeneration || !loginDialog.open) return;
  if (!loginBusy) paintLogin(data);
  if (!loginTerminal.has(data.phase)) loginTimer = setTimeout(() => pollLogin(generation), 1000);
 } catch (error) {
  if (generation !== loginGeneration || !loginDialog.open) return;
  $('#login-status').textContent = error.message;
  loginTimer = setTimeout(() => pollLogin(generation), 3000);
 }
}
async function startLogin() {
 if (loginBusy) return;
 loginBusy = true;
 const generation = ++loginGeneration;
 clearTimeout(loginTimer);
 $('#login-status').textContent = '로그인 준비 중입니다.';
 $('#login-retry').disabled = true;
 $('#login-code').value = '';
 try {
  const data = await loginRequest('start');
  if (generation !== loginGeneration || !loginDialog.open) return;
  paintLogin(data);
  loginTimer = setTimeout(() => pollLogin(generation), 500);
 } catch (error) {
  if (generation !== loginGeneration) return;
  $('#login-status').textContent = error.message;
  $('#login-retry').hidden = false;
 } finally {if (generation === loginGeneration) {loginBusy = false; $('#login-retry').disabled = false; $('#login-submit').disabled = false}}
}
function openLogin(key) {
 loginKey = key; loginState = null;
 $('#login-browser').hidden = $('#login-device').hidden = $('#login-code-form').hidden = true;
 $('#login-link').removeAttribute('href');
 $('#login-device-code').value = $('#login-code').value = '';
 $('#login-account').textContent = '';
 $('#login-retry').hidden = true;
 $('#login-cancel').hidden = false;
 if (!loginDialog.open) loginDialog.showModal();
 startLogin();
}
loginDialog.addEventListener('close', () => {
 ++loginGeneration; clearTimeout(loginTimer); loginBusy = false;
 $('#login-code').value = $('#login-device-code').value = '';
 $('#login-link').removeAttribute('href');
 const key = loginKey; loginState = null; loginKey = null;
 document.querySelector(`[data-login-account="${CSS.escape(key || '')}"]`)?.focus();
});
$('#login-close').onclick = () => loginDialog.close();
$('#login-retry').onclick = startLogin;
$('#login-cancel').onclick = async () => {
 if (!loginState?.id || loginBusy) return;
 loginBusy = true;
 const generation = loginGeneration;
 try {const data = await loginRequest('cancel', {id:loginState.id}); if (generation === loginGeneration) paintLogin(data)}
 catch (error) {if (generation === loginGeneration) $('#login-status').textContent = error.message}
 finally {if (generation === loginGeneration) loginBusy = false}
};
$('#login-code-form').onsubmit = async event => {
 event.preventDefault();
 if (loginBusy || !loginState?.id || !$('#login-code').value) return;
 const generation = loginGeneration;
 loginBusy = true; $('#login-submit').disabled = true;
 try {
  const request = loginRequest('code', {id:loginState.id, code:$('#login-code').value.trim()});
  $('#login-code').value = '';
  const data = await request;
  if (generation === loginGeneration && loginDialog.open) paintLogin(data);
 } catch (error) {if (generation === loginGeneration) $('#login-status').textContent = error.message}
 finally {if (generation === loginGeneration) {loginBusy = false; $('#login-submit').disabled = false}}
};
$('#login-copy').onclick = async () => {
 const input = $('#login-device-code');
 try {await navigator.clipboard.writeText(input.value); $('#login-copy-status').textContent = '복사했습니다. 공식 로그인 페이지에 붙여 넣으세요.'}
 catch {input.focus(); input.select(); $('#login-copy-status').textContent = '선택된 코드를 복사해 공식 로그인 페이지에 입력하세요.'}
};
