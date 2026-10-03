const form = document.querySelector("#login-form");
const button = document.querySelector("#login-button");
const message = document.querySelector("#login-message");
const readiness = document.querySelector("#login-readiness");
const readinessTitle = document.querySelector("#readiness-title");
const readinessDetail = document.querySelector("#readiness-detail");
const readinessRetry = document.querySelector("#readiness-retry");
let servicesReady = false;
let readinessTimer = null;

function renderReadiness(data, error = null) {
  servicesReady = Boolean(data?.ready);
  readiness.className = `login-readiness ${servicesReady ? "ready" : "waiting"}`;
  if (servicesReady) {
    readinessTitle.textContent = "로그인 준비 완료";
    readinessDetail.textContent = "증권 연결이 없어도 로그인 후 설정에서 등록·복구할 수 있습니다.";
    readinessRetry.hidden = true;
    button.disabled = false;
    button.textContent = "로그인";
    return;
  }
  const databaseMessage = data?.database?.message || "저장 서비스에 연결하지 못했습니다.";
  readinessTitle.textContent = "계정 저장소 연결 대기 중";
  readinessDetail.textContent = error?.message || databaseMessage;
  readinessRetry.hidden = false;
  button.disabled = true;
  button.textContent = "연결 대기 중...";
}

async function checkReadiness() {
  clearTimeout(readinessTimer);
  readiness.className = "login-readiness checking";
  try {
    const response = await fetch("/startup/readiness", { cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "연결 상태 확인에 실패했습니다.");
    renderReadiness(data);
  } catch (error) {
    renderReadiness(null, error);
  }
  if (!servicesReady) readinessTimer = setTimeout(checkReadiness, 5000);
}

readinessRetry.addEventListener("click", checkReadiness);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!servicesReady) {
    message.textContent = "서비스에 연결될 때까지 잠시 기다려 주세요.";
    await checkReadiness();
    return;
  }
  button.disabled = true;
  message.textContent = "로그인 확인 중...";
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#username").value,
        password: document.querySelector("#password").value,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "로그인에 실패했습니다.");
    // This checks local configuration only; broker outages must not delay sign-in.
    let destination = "/live";
    try {
      const connection = await fetch("/live/orders/real/readiness", {cache: "no-store"});
      if (connection.ok && !(await connection.json()).configured) destination = "/settings#account";
    } catch { /* A successful login remains usable if the status check fails. */ }
    window.location.replace(destination);
  } catch (error) {
    message.textContent = error.message;
    document.querySelector("#password").value = "";
    document.querySelector("#password").focus();
  } finally {
    button.disabled = false;
  }
});

checkReadiness();
