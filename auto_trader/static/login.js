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
    readinessTitle.textContent = "서비스 연결 완료";
    readinessDetail.textContent = "PostgreSQL과 토스 API가 준비되었습니다.";
    readinessRetry.hidden = true;
    button.disabled = false;
    button.textContent = "로그인";
    return;
  }
  const databaseMessage = data?.database?.message || "PostgreSQL 상태를 확인하지 못했습니다.";
  const tossMessage = data?.toss_api?.message || error?.message || "토스 API 상태를 확인하지 못했습니다.";
  readinessTitle.textContent = "서비스 연결 대기 중";
  readinessDetail.textContent = `${databaseMessage} · ${tossMessage}`;
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
    message.textContent = "PostgreSQL과 토스 API 연결이 완료될 때까지 잠시 기다려 주세요.";
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
    window.location.replace("/live");
  } catch (error) {
    message.textContent = error.message;
    document.querySelector("#password").value = "";
    document.querySelector("#password").focus();
  } finally {
    button.disabled = false;
  }
});

checkReadiness();
