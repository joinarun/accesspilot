const form = document.querySelector("#signup-form");
const email = document.querySelector("#email");
const error = document.querySelector("#form-error");
const startAction = document.querySelector("#start-action");
const toast = document.querySelector("#toast");

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.setTimeout(() => toast.classList.remove("visible"), 2200);
}

startAction.addEventListener("click", () => {
  document.querySelector("#signup").scrollIntoView({ behavior: "smooth" });
  showToast("Ready when you are.");
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  error.textContent = "";

  if (!email.value.includes("@")) {
    error.textContent = "Enter a valid work email before continuing.";
    return;
  }

  showToast("Account request submitted.");
  form.reset();
});
