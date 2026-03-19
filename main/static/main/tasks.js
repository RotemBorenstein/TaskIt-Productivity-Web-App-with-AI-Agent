document.addEventListener("DOMContentLoaded", function () {
  // Read Django's CSRF cookie for AJAX requests and rebuilt forms.
  function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === name + "=") {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function pinIconSvg(isActive) {
    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" ${isActive ? 'fill="currentColor" stroke="currentColor"' : 'fill="none" stroke="currentColor"'} stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <line x1="12" y1="17" x2="12" y2="22"></line>
        <path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 11.2V6a3 3 0 0 0-6 0v5.2a2 2 0 0 1-1.11 1.35l-1.78.9A2 2 0 0 0 5 15.24Z"></path>
      </svg>
    `;
  }

  function reminderIconSvg() {
    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M10.268 21a2 2 0 0 0 3.464 0"></path>
        <path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"></path>
      </svg>
    `;
  }

  function editIconSvg() {
    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 20h9"></path>
        <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
      </svg>
    `;
  }

  function trashIconSvg() {
    return `
      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <polyline points="3 6 5 6 21 6"></polyline>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
        <line x1="10" y1="11" x2="10" y2="17"></line>
        <line x1="14" y1="11" x2="14" y2="17"></line>
      </svg>
    `;
  }

  function formatDue(task) {
    if (!task.due_date) return "";
    return task.due_time
      ? `Due ${task.due_date}, ${String(task.due_time).slice(0, 5)}`
      : `Due ${task.due_date}`;
  }

  function renderReminderIndicator(reminderEnabled) {
    const label = reminderEnabled ? "Reminder set" : "No reminder";
    return `
      <span class="task-status-icon reminder-indicator ${reminderEnabled ? "is-active" : ""}" title="${label}" aria-label="${label}">
        ${reminderIconSvg()}
      </span>
    `;
  }

  function renderAnchorButton(task) {
    const isAnchored = !!task.is_anchored;
    const title = isAnchored ? "Unpin Daily Task" : "Pin to Daily Tasks";
    const ariaLabel = isAnchored ? "Unpin daily task" : "Pin to daily tasks";
    return `
      <button class="task-action-link anchor-btn ${isAnchored ? "is-active" : ""}" data-task-id="${task.id}" aria-label="${ariaLabel}" title="${title}">
        ${pinIconSvg(isAnchored)}
      </button>
    `;
  }

  function renderEditLink(taskId) {
    return `
      <a class="task-action-link" href="/tasks/${taskId}/edit/" title="Edit Task">
        ${editIconSvg()}
      </a>
    `;
  }

  function renderDeleteForm(taskId, csrfToken) {
    return `
      <form method="post" action="/tasks/${taskId}/delete/" class="task-delete-form">
        <input type="hidden" name="csrfmiddlewaretoken" value="${escapeHtml(csrfToken)}">
        <button type="submit" class="task-action-link task-action-danger" title="Delete Task">
          ${trashIconSvg()}
        </button>
      </form>
    `;
  }

  function renderTaskItem(task, options) {
    const dueText = options.includeDue ? formatDue(task) : "";
    const dueMarkup = dueText
      ? `<span class="task-pill">${escapeHtml(dueText)}</span>`
      : "";

    return `
      <div class="task-main">
        <label class="task-label">
          <input type="checkbox" class="task-checkbox" data-task-id="${task.id}">
          <span class="task-title">${escapeHtml(task.title)}</span>
        </label>
        <div class="task-meta-row">${dueMarkup}</div>
        ${task.description ? `<div class="task-desc">${escapeHtml(task.description)}</div>` : ""}
      </div>
      <div class="task-actions">
        ${renderReminderIndicator(task.reminder_enabled)}
        ${options.includeAnchor ? renderAnchorButton(task) : ""}
        ${renderEditLink(task.id)}
        ${renderDeleteForm(task.id, options.csrfToken)}
      </div>
    `;
  }

  function bindCheckboxes() {
    document.querySelectorAll(".task-checkbox").forEach(function (checkbox) {
      if (checkbox.dataset.bound === "true") return;
      checkbox.dataset.bound = "true";

      checkbox.addEventListener("change", function () {
        if (!checkbox.checked) return;
        const taskId = checkbox.getAttribute("data-task-id");
        if (!taskId) return;

        const csrftoken = getCookie("csrftoken");
        const data = new URLSearchParams();
        data.append("task_id", taskId);

        fetch("/tasks/complete/", {
          method: "POST",
          headers: {
            "X-CSRFToken": csrftoken,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: data,
        })
          .then((response) => response.json())
          .then((data) => {
            if (data.success) {
              const li = document.getElementById(`task-${taskId}`);
              if (li) {
                li.classList.add("fade-out");
                setTimeout(() => li.remove(), 500);
              }
            } else {
              alert("Failed to complete task: " + (data.error || "Unknown error"));
              checkbox.checked = false;
            }
          })
          .catch(() => {
            alert("Network error: could not mark task as complete.");
            checkbox.checked = false;
          });
      });
    });
  }

  function bindAnchorButtons() {
    document.querySelectorAll(".anchor-btn").forEach(function (btn) {
      if (btn.dataset.bound === "true") return;
      btn.dataset.bound = "true";

      btn.addEventListener("click", function (e) {
        e.preventDefault();
        const taskId = btn.getAttribute("data-task-id");
        if (!taskId) return;

        fetch(`/tasks/${taskId}/toggle_anchor/`, {
          method: "POST",
          headers: {
            "X-CSRFToken": getCookie("csrftoken"),
            "X-Requested-With": "XMLHttpRequest",
          },
        })
          .then((res) => res.json())
          .then((data) => {
            if (data.success) {
              btn.classList.toggle("is-active", data.anchored);
              btn.setAttribute(
                "title",
                data.anchored ? "Unpin Daily Task" : "Pin to Daily Tasks"
              );
              btn.setAttribute(
                "aria-label",
                data.anchored ? "Unpin daily task" : "Pin to daily tasks"
              );
              btn.innerHTML = pinIconSvg(data.anchored);
            } else {
              alert(data.error || "Failed to toggle anchor. Please try again.");
            }
          })
          .catch(() => alert("Network error: could not toggle anchor."));
      });
    });
  }

  window.refreshTasks = function () {
    const csrfToken = getCookie("csrftoken") || "";

    fetch("/api/tasks/", { credentials: "same-origin" })
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => {
        const dailyList = document.getElementById("daily-tasks-list");
        if (dailyList) {
          dailyList.innerHTML = "";
          if (data.daily_tasks.length === 0) {
            dailyList.innerHTML = '<li class="task-empty">No daily tasks. Enjoy your day!</li>';
          } else {
            data.daily_tasks.forEach((task) => {
              const li = document.createElement("li");
              li.id = `task-${task.id}`;
              li.className = "task-item";
              li.innerHTML = renderTaskItem(task, {
                includeDue: false,
                includeAnchor: true,
                csrfToken,
              });
              dailyList.appendChild(li);
            });
          }
        }

        const longList = document.getElementById("long-tasks-list");
        if (longList) {
          longList.innerHTML = "";
          if (data.long_tasks.length === 0) {
            longList.innerHTML = '<li class="task-empty">No long-term tasks yet.</li>';
          } else {
            data.long_tasks.forEach((task) => {
              const li = document.createElement("li");
              li.id = `task-${task.id}`;
              li.className = "task-item";
              li.innerHTML = renderTaskItem(task, {
                includeDue: true,
                includeAnchor: false,
                csrfToken,
              });
              longList.appendChild(li);
            });
          }
        }

        bindCheckboxes();
        bindAnchorButtons();
      })
      .catch((err) => console.error("refreshTasks failed:", err));
  };

  function showForm(formId) {
    document.getElementById(formId).style.display = "block";
    if (formId === "daily-form-container") {
      document.getElementById("long-form-container").style.display = "none";
    } else {
      document.getElementById("daily-form-container").style.display = "none";
    }
  }

  function hideForm(formId) {
    document.getElementById(formId).style.display = "none";
  }

  function syncReminderCard(formEl) {
    const reminderEnabled = formEl.querySelector('input[name$="reminder_enabled"]');
    const dueDateInput = formEl.querySelector('input[name$="due_date"]');
    const reminderTime = formEl.querySelector('input[name$="reminder_time"]');
    const reminderFields = formEl.querySelector(".reminder-fields");
    const isLongTerm = !!dueDateInput && dueDateInput.type !== "hidden";
    const canConfigure =
      !!reminderEnabled && reminderEnabled.checked && (!isLongTerm || !!dueDateInput.value);

    if (reminderFields) {
      reminderFields.style.opacity = canConfigure ? "1" : "0.55";
    }
    if (reminderTime) reminderTime.disabled = !canConfigure;
  }

  function bindReminderForms() {
    document.querySelectorAll(".add-task-form, .edit-task-form").forEach((formEl) => {
      const reminderEnabled = formEl.querySelector('input[name$="reminder_enabled"]');
      const dueDateInput = formEl.querySelector('input[name$="due_date"]');

      if (reminderEnabled && reminderEnabled.dataset.bound !== "true") {
        reminderEnabled.dataset.bound = "true";
        reminderEnabled.addEventListener("change", () => syncReminderCard(formEl));
      }

      if (dueDateInput && dueDateInput.dataset.bound !== "true") {
        dueDateInput.dataset.bound = "true";
        dueDateInput.addEventListener("change", () => syncReminderCard(formEl));
      }

      syncReminderCard(formEl);
    });
  }

  const showDailyBtn = document.getElementById("show-daily-form");
  const showLongBtn = document.getElementById("show-long-form");
  if (showDailyBtn) {
    showDailyBtn.onclick = function () {
      showForm("daily-form-container");
    };
  }
  if (showLongBtn) {
    showLongBtn.onclick = function () {
      showForm("long-form-container");
    };
  }

  const closeDailyBtn = document.getElementById("close-daily-form");
  const closeLongBtn = document.getElementById("close-long-form");
  if (closeDailyBtn) {
    closeDailyBtn.onclick = function () {
      hideForm("daily-form-container");
    };
  }
  if (closeLongBtn) {
    closeLongBtn.onclick = function () {
      hideForm("long-form-container");
    };
  }

  bindCheckboxes();
  bindAnchorButtons();
  bindReminderForms();
});
