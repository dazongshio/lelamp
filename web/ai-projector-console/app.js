const current = document.body.dataset.page || "today";

document.querySelectorAll("[data-page-link]").forEach((link) => {
  if (link.dataset.pageLink === current) {
    link.classList.add("active");
  }
});

document.querySelectorAll("[data-preview-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.querySelector(button.dataset.previewTarget);
    if (!target) return;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

