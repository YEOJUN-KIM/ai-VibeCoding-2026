const form = document.querySelector("#login-form");
const button = document.querySelector("#login-button");
const message = document.querySelector("#login-message");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
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
